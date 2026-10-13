from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from cadastros.models import Fornecedor, Produto
from contas.models import Alcada, AprovadorAlcada, Modulo, Papel, PapelAlcada, PapelUsuario
from estoque.models import SaldoEstoque
from pedidos.models import ItemPedido, OrigemAtendimento, StatusPedido
from pedidos.services import SemPermissao, TransicaoInvalida, aprovar_como_lider, transicionar
from pedidos.tests import BaseFluxoTest
from tenancy.models import Almoxarifado, Empresa, Evento

from . import services
from .models import Cotacao, StatusCotacao, StatusOrdem
from .nfe import NFeInvalida, ler_nfe

Usuario = get_user_model()

CNPJ_FORNECEDOR = "11222333000181"


def nfe_xml(chave="3" * 44, emitente=CNPJ_FORNECEDOR, destinatario="00000000000100",
            linhas=(("TESTE-1", "Papel crepom", "5", "20.00"),), frete="0.00"):
    dets = "".join(
        f'<det nItem="{n}"><prod><cProd>{c}</cProd><xProd>{d}</xProd><qCom>{q}</qCom>'
        f"<vUnCom>{v}</vUnCom></prod></det>"
        for n, (c, d, q, v) in enumerate(linhas, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
        f'<NFe><infNFe Id="NFe{chave}"><ide><nNF>123</nNF><dhEmi>2026-10-14T10:00:00-03:00</dhEmi></ide>'
        f"<emit><CNPJ>{emitente}</CNPJ></emit><dest><CNPJ>{destinatario}</CNPJ></dest>{dets}"
        f"<total><ICMSTot><vProd>100.00</vProd><vFrete>{frete}</vFrete><vNF>100.00</vNF></ICMSTot></total>"
        "</infNFe></NFe></nfeProc>"
    ).encode()


class BaseComprasTest(BaseFluxoTest):
    def setUp(self):
        super().setUp()
        self.comprador = Usuario.objects.create_user("comprador", tenant=self.tenant, filial=self.filial)
        PapelUsuario.objects.create(usuario=self.comprador, papel=Papel.ADMINISTRADOR, modulo=Modulo.COMPRAS)
        self.diretor = Usuario.objects.create_user("diretor", tenant=self.tenant, filial=self.filial)
        self.estranho = Usuario.objects.create_user("estranho", tenant=self.tenant)
        self.fornecedor = Fornecedor.objects.create(razao_social="Papelaria Ltda", cnpj="11.222.333/0001-81")

        diretoria = Alcada.objects.get(papel_aprovador=PapelAlcada.DIRETORIA_UNIDADE)
        AprovadorAlcada.objects.create(alcada=diretoria, usuario=self.diretor, filial=self.filial)

        self.papel = Produto.objects.create(
            nome="Papel crepom", especificacao="rolo", categoria=self.categoria
        )

    def pedido_sem_estoque(self, quantidade="5"):
        pedido = self.novo_pedido("1")
        pedido.itens.all().delete()
        item = ItemPedido.objects.create(pedido=pedido, produto=self.papel, quantidade=Decimal(quantidade))
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        item.refresh_from_db()
        return pedido, item

    def cotar(self, item, preco="20.00", frete="0"):
        return services.criar_cotacao(
            comprador=self.comprador, fornecedor=self.fornecedor,
            precos={item.pk: Decimal(preco)}, valor_frete=Decimal(frete),
        )


class CotacaoEAlcadaTest(BaseComprasTest):
    def test_pedido_sem_estoque_espera_compra_e_entra_na_fila(self):
        pedido, item = self.pedido_sem_estoque()
        self.assertEqual(pedido.status, StatusPedido.AGUARDANDO_COMPRA)
        self.assertEqual(item.origem_atendimento, OrigemAtendimento.COMPRA)
        self.assertIn(item, list(services.itens_para_cotar()))

    def test_valor_ate_300_e_aprovado_automaticamente(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00", "10")  # 5 x 20 + 10 = 110
        self.assertEqual(cotacao.valor_total, Decimal("110.00"))
        self.assertEqual(cotacao.status, StatusCotacao.APROVADA)

    def test_alcada_e_sobre_o_total_com_frete(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "58.00", "20")  # itens 290 (<300) mas com frete 310
        self.assertEqual(cotacao.alcada.papel_aprovador, PapelAlcada.DIRETORIA_UNIDADE)
        self.assertEqual(cotacao.status, StatusCotacao.AGUARDANDO_ALCADA)

    def test_so_aprovador_da_faixa_decide(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "100.00")
        with self.assertRaises(SemPermissao):
            services.decidir_cotacao(cotacao, self.estranho, True)
        with self.assertRaises(SemPermissao):  # quem cotou não aprova a própria compra
            services.decidir_cotacao(cotacao, self.comprador, True)
        services.decidir_cotacao(cotacao, self.diretor, True)
        cotacao.refresh_from_db()
        self.assertEqual(cotacao.status, StatusCotacao.APROVADA)

    def test_recusa_exige_motivo_e_devolve_itens_a_fila(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "100.00")
        self.assertNotIn(item, list(services.itens_para_cotar()))
        with self.assertRaises(TransicaoInvalida):
            services.decidir_cotacao(cotacao, self.diretor, False, "")
        services.decidir_cotacao(cotacao, self.diretor, False, "Preço acima do mercado")
        self.assertIn(item, list(services.itens_para_cotar()))

    def test_item_nao_entra_em_duas_cotacoes_ativas(self):
        _, item = self.pedido_sem_estoque("5")
        self.cotar(item, "100.00")
        with self.assertRaises(TransicaoInvalida):
            self.cotar(item, "90.00")


class NotaFiscalXmlTest(BaseComprasTest):
    def ordem(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00")
        return services.gerar_ordem_compra(cotacao, self.comprador), item

    def test_xml_valido_e_aceito_e_chave_duplicada_rejeitada(self):
        ordem, _ = self.ordem()
        nota = services.registrar_nota_xml(ordem, nfe_xml(), self.comprador)
        self.assertEqual(nota.numero, "123")
        with self.assertRaises(TransicaoInvalida):
            services.registrar_nota_xml(ordem, nfe_xml(), self.comprador)

    def test_cnpj_destinatario_fora_do_grupo_rejeitado(self):
        ordem, _ = self.ordem()
        with self.assertRaises(TransicaoInvalida):
            services.registrar_nota_xml(ordem, nfe_xml(destinatario="99999999000199"), self.comprador)

    def test_emitente_diferente_do_fornecedor_rejeitado(self):
        ordem, _ = self.ordem()
        with self.assertRaises(TransicaoInvalida):
            services.registrar_nota_xml(ordem, nfe_xml(emitente="99999999000199"), self.comprador)

    def test_xml_com_entidade_externa_e_rejeitado(self):
        malicioso = (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b"<nfeProc><NFe><infNFe Id=\"NFe" + b"1" * 44 + b'">&e;</infNFe></NFe></nfeProc>'
        )
        with self.assertRaises(NFeInvalida):
            ler_nfe(malicioso)

    def test_xml_que_nao_e_nfe_rejeitado(self):
        with self.assertRaises(NFeInvalida):
            ler_nfe(b"<html><body>oi</body></html>")


class RecebimentoTest(BaseComprasTest):
    def pronto_para_receber(self, frete="10.00"):
        pedido, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00", frete)  # 100 + frete
        ordem = services.gerar_ordem_compra(cotacao, self.comprador)
        return pedido, item, ordem

    def test_recebimento_da_entrada_a_custo_com_frete_rateado_e_libera_pedido(self):
        pedido, item, ordem = self.pronto_para_receber("10.00")
        ic = ordem.cotacao.itens.get()
        services.confirmar_recebimento(
            ordem, self.suprimentos, [{"item_id": ic.pk, "quantidade": Decimal("5")}]
        )
        central = Almoxarifado.objects.get(central=True)
        saldo = SaldoEstoque.objects.get(produto=self.papel, almoxarifado=central)
        self.assertEqual(saldo.quantidade, Decimal("5"))
        self.assertEqual(saldo.custo_medio, Decimal("22.00"))  # 20 + frete 10/5

        item.refresh_from_db()
        pedido.refresh_from_db()
        ordem.refresh_from_db()
        self.assertEqual(item.origem_atendimento, OrigemAtendimento.ESTOQUE)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)
        self.assertEqual(ordem.status, StatusOrdem.CONCLUIDA)

    def test_recebimento_parcial_mantem_ordem_aberta_e_item_como_compra(self):
        pedido, item, ordem = self.pronto_para_receber()
        ic = ordem.cotacao.itens.get()
        services.confirmar_recebimento(
            ordem, self.suprimentos, [{"item_id": ic.pk, "quantidade": Decimal("2")}]
        )
        ordem.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(ordem.status, StatusOrdem.PARCIALMENTE_RECEBIDA)
        self.assertEqual(item.origem_atendimento, OrigemAtendimento.COMPRA)

    def test_quantidade_acima_do_pendente_rejeitada(self):
        _, _, ordem = self.pronto_para_receber()
        ic = ordem.cotacao.itens.get()
        with self.assertRaises(TransicaoInvalida):
            services.confirmar_recebimento(
                ordem, self.suprimentos, [{"item_id": ic.pk, "quantidade": Decimal("6")}]
            )

    def test_comprador_conferindo_a_propria_compra_fica_registrado(self):
        _, _, ordem = self.pronto_para_receber()
        ic = ordem.cotacao.itens.get()
        recebimento = services.confirmar_recebimento(
            ordem, self.comprador, [{"item_id": ic.pk, "quantidade": Decimal("5")}]
        )
        self.assertIn("próprio comprador", recebimento.observacao)


class RelatorioCompraTest(BaseComprasTest):
    def test_compra_aparece_como_valor_cotado_sem_contar_duas_vezes_no_estoque(self):
        evento = Evento.objects.create(tenant=self.tenant, filial=self.filial, nome="Festa Junina")
        pedido, item = self.pedido_sem_estoque("5")
        pedido.evento = evento
        pedido.save(update_fields=["evento"])

        cotacao = self.cotar(item, "20.00", "10")  # total 110
        ordem = services.gerar_ordem_compra(cotacao, self.comprador)
        ic = cotacao.itens.get()
        services.confirmar_recebimento(ordem, self.suprimentos, [{"item_id": ic.pk, "quantidade": Decimal("5")}])
        pedido.refresh_from_db()
        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        from pedidos.services import entregar
        entregar(pedido, self.suprimentos)

        admin = Usuario.objects.create_superuser("admin", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        resposta = self.client.get(reverse("relatorio_eventos"))
        linha = next(l for l in resposta.context["linhas"] if l["evento"] == evento)
        self.assertEqual(linha["valor_compra"], Decimal("110.00"))
        self.assertEqual(linha["custo_estoque"], 0)  # veio da compra; já está em valor_compra


class AcessoACotacaoTest(BaseComprasTest):
    def test_aprovador_continua_vendo_a_cotacao_depois_de_decidir(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "100.00")
        self.client.force_login(self.diretor)
        url = reverse("cotacao_detalhe", args=[cotacao.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

        resposta = self.client.post(url, {"acao": "aprovar"}, follow=True)
        self.assertEqual(resposta.status_code, 200)  # antes: 403 ao voltar para a cotação
        cotacao.refresh_from_db()
        self.assertEqual(cotacao.status, StatusCotacao.APROVADA)

    def test_usuario_sem_relacao_com_a_cotacao_nao_acessa(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "100.00")
        self.client.force_login(self.estranho)
        resposta = self.client.get(reverse("cotacao_detalhe", args=[cotacao.pk]))
        self.assertEqual(resposta.status_code, 403)


class NumeracaoTest(BaseComprasTest):
    """Cotação e OC herdam o número do pedido, para a cadeia ser reconhecível."""

    def test_cotacao_e_ordem_tem_o_numero_do_pedido(self):
        pedido, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00")
        ordem = services.gerar_ordem_compra(cotacao, self.comprador)
        self.assertEqual(cotacao.numero, str(pedido.pk))
        self.assertEqual(ordem.numero, str(pedido.pk))

    def test_segunda_cotacao_do_mesmo_pedido_ganha_sufixo(self):
        pedido, item = self.pedido_sem_estoque("5")
        primeira = self.cotar(item, "100.00")  # alçada: aguarda
        services.cancelar_cotacao(primeira, self.comprador)
        segunda = self.cotar(item, "90.00")
        self.assertEqual(primeira.numero, str(pedido.pk))
        self.assertEqual(segunda.numero, f"{pedido.pk}.2")

    def test_cotacao_com_varios_pedidos_junta_os_numeros(self):
        pedido_a, item_a = self.pedido_sem_estoque("5")
        pedido_b, item_b = self.pedido_sem_estoque("3")
        cotacao = services.criar_cotacao(
            comprador=self.comprador, fornecedor=self.fornecedor,
            precos={item_a.pk: Decimal("10.00"), item_b.pk: Decimal("10.00")},
        )
        esperado = " + ".join(str(i) for i in sorted([pedido_a.pk, pedido_b.pk]))
        self.assertEqual(cotacao.numero, esperado)
        self.assertEqual(cotacao.pedidos_origem, esperado)
