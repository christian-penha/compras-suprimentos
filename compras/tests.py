from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from cadastros.models import Fornecedor, Produto
from contas.models import Alcada, AprovadorAlcada, Modulo, Papel, PapelAlcada, PapelUsuario
from estoque.models import SaldoEstoque
from pedidos.models import ItemPedido, OrigemAtendimento, Pedido, StatusPedido
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
        saldo = SaldoEstoque.objects.get(produto=self.papel, almoxarifado=self.destino)  # entra no almoxarifado do pedido
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


class AlcadaPorFilialNaCotacaoTest(BaseComprasTest):
    def test_tabela_da_filial_muda_a_faixa_da_cotacao(self):
        from contas.alcadas import salvar_tabela

        _, item = self.pedido_sem_estoque("5")
        sem_regra_propria = self.cotar(item, "100.00")  # R$ 500 na tabela do grupo
        self.assertEqual(sem_regra_propria.alcada.papel_aprovador, PapelAlcada.DIRETORIA_UNIDADE)
        services.cancelar_cotacao(sem_regra_propria, self.comprador)

        salvar_tabela(
            tenant=self.tenant, filial=self.filial, usuario=self.comprador,
            linhas=[(Decimal("1000.00"), "AUTOMATICA"), (None, "CEO")],
        )
        com_regra_propria = self.cotar(item, "100.00")
        self.assertEqual(com_regra_propria.alcada.filial, self.filial)
        self.assertEqual(com_regra_propria.status, StatusCotacao.APROVADA)  # automática até R$ 1.000


class ComparacaoDeFornecedoresTest(BaseComprasTest):
    def setUp(self):
        super().setUp()
        self.outro = Fornecedor.objects.create(razao_social="Distribuidora X", cnpj="55.666.777/0001-88")
        _, self.item = self.pedido_sem_estoque("5")

    def rodada(self, preco_a="20.00", preco_b="15.00"):
        from .rodadas import criar_rodada

        return criar_rodada(comprador=self.comprador, propostas=[
            {"fornecedor": self.fornecedor, "precos": {self.item.pk: Decimal(preco_a)}, "frete": Decimal("0")},
            {"fornecedor": self.outro, "precos": {self.item.pk: Decimal(preco_b)}, "frete": Decimal("10")},
        ])

    def test_rodada_prende_o_item_e_nao_tem_alcada_nem_numero(self):
        rodada = self.rodada()
        propostas = Cotacao.objects.filter(rodada=rodada)
        self.assertEqual(propostas.count(), 2)
        self.assertTrue(all(c.status == StatusCotacao.EM_COMPARACAO and not c.numero for c in propostas))
        self.assertNotIn(self.item, list(services.itens_para_cotar()))

    def test_fechar_gera_cotacao_so_do_vencedor(self):
        from .rodadas import fechar_rodada

        rodada = self.rodada()
        vencedora = Cotacao.objects.get(rodada=rodada, fornecedor=self.outro)
        resultado = fechar_rodada(rodada, self.comprador, {self.item.pk: vencedora.pk})
        self.assertEqual([c.pk for c in resultado], [vencedora.pk])
        vencedora.refresh_from_db()
        self.assertEqual(vencedora.valor_total, Decimal("85.00"))  # 5 x 15 + 10
        self.assertEqual(vencedora.status, StatusCotacao.APROVADA)
        self.assertTrue(vencedora.numero)
        perdedora = Cotacao.objects.get(rodada=rodada, fornecedor=self.fornecedor)
        self.assertEqual(perdedora.status, StatusCotacao.CANCELADA)

    def test_escolha_de_fornecedor_que_nao_cotou_e_rejeitada(self):
        from .rodadas import criar_rodada, fechar_rodada

        cola = Produto.objects.create(nome="Cola", especificacao="frasco", categoria=self.categoria)
        outro_item = ItemPedido.objects.create(pedido=self.item.pedido, produto=cola, quantidade=Decimal("1"))
        outro_item.origem_atendimento = OrigemAtendimento.COMPRA
        outro_item.save()
        rodada = criar_rodada(comprador=self.comprador, propostas=[
            {"fornecedor": self.fornecedor, "precos": {self.item.pk: Decimal("20")}, "frete": Decimal("0")},
            {"fornecedor": self.outro, "precos": {outro_item.pk: Decimal("9")}, "frete": Decimal("0")},
        ])
        a = Cotacao.objects.get(rodada=rodada, fornecedor=self.fornecedor)
        with self.assertRaises(TransicaoInvalida):
            fechar_rodada(rodada, self.comprador, {self.item.pk: a.pk, outro_item.pk: a.pk})

    def test_telas_de_fila_e_lista_agrupam_por_pedido_e_produto(self):
        self.client.force_login(self.comprador)
        for por in ("fornecedor", "pedido", "produto"):
            self.assertEqual(self.client.get(reverse("fila_compras"), {"agrupar": por}).status_code, 200)
        self.rodada()
        for por in ("cotacao", "pedido", "produto"):
            self.assertEqual(self.client.get(reverse("cotacao_lista"), {"agrupar": por}).status_code, 200)
        rodada = Cotacao.objects.exclude(rodada=None).first().rodada
        self.assertContains(self.client.get(reverse("rodada_comparar", args=[rodada])), "Comparar fornecedores")


class VisoesDaAreaDeComprasTest(BaseComprasTest):
    def test_cotacoes_e_ordens_abrem_em_cards_lista_e_kanban(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00")
        services.gerar_ordem_compra(cotacao, self.comprador)
        self.client.force_login(self.comprador)
        for nome in ("cotacao_lista", "ordem_lista"):
            for visao in ("cards", "lista", "kanban", "invalida"):
                resposta = self.client.get(reverse(nome), {"visao": visao})
                self.assertEqual(resposta.status_code, 200, (nome, visao))
        kanban = self.client.get(reverse("cotacao_lista"), {"visao": "kanban"})
        self.assertContains(kanban, "Aguardando alçada")


class CentroDeCustoTest(BaseComprasTest):
    def test_ordem_exige_centro_de_custo_em_todos_os_itens(self):
        pedido, item = self.pedido_sem_estoque("5")
        Pedido.objects.filter(pk=pedido.pk).update(centro_custo=None)
        cotacao = self.cotar(item, "20.00")
        self.assertFalse(hasattr(Cotacao.objects.get(pk=cotacao.pk), "ordem"))  # sem centro, sem OC automática
        with self.assertRaises(TransicaoInvalida):
            services.gerar_ordem_compra(cotacao, self.comprador)
        services.definir_centros(cotacao, {cotacao.itens.get().pk: self.centro}, self.comprador)
        self.assertEqual(services.gerar_ordem_compra(cotacao, self.comprador).cotacao_id, cotacao.pk)

    def test_cotacao_herda_centro_do_pedido(self):
        _, item = self.pedido_sem_estoque("5")
        self.assertEqual(self.cotar(item).itens.get().centro_custo, self.centro)

    def test_desembolso_distribui_o_valor_da_nota_pelos_centros(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00")
        ordem = services.gerar_ordem_compra(cotacao, self.comprador)
        ordem.notas.create(
            tenant=self.tenant, valor_total=Decimal("100.00"), registrada_por=self.comprador,
        )
        self.assertEqual(services.desembolso_por_centro(), {self.centro: Decimal("100.00")})

    def test_relatorio_de_centros_abre(self):
        self.client.force_login(self.comprador)
        self.assertEqual(self.client.get(reverse("relatorio_centros")).status_code, 200)


class SolicitacaoAoFornecedorTest(BaseComprasTest):
    def test_gera_texto_padronizado_com_total_por_produto(self):
        _, a = self.pedido_sem_estoque("5")
        self.client.force_login(self.comprador)
        resposta = self.client.get(reverse("solicitacao_cotacao"), {"item": [a.pk], "fornecedor": self.fornecedor.pk})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "SOLICITAÇÃO DE COTAÇÃO")
        self.assertContains(resposta, "Papelaria Ltda")
        self.assertContains(resposta, "1. Papel crepom")
        # sem seleção volta para a fila
        self.assertEqual(self.client.get(reverse("solicitacao_cotacao")).status_code, 302)


class IsolamentoDeModulosTest(BaseComprasTest):
    def test_administrador_de_compras_nao_ve_suprimentos(self):
        self.client.force_login(self.comprador)
        for nome in ("fila_suprimentos", "posicao_estoque", "entrada_planilha", "produto_lista", "sugestoes_lista"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)
        for nome in ("fila_compras", "cotacao_lista", "ordem_lista", "relatorio_centros"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)
        pagina = self.client.get(reverse("painel"))
        self.assertNotContains(pagina, "Fila de suprimentos")
        self.assertContains(pagina, "Fila de compras")

    def test_administrador_de_estoque_nao_ve_compras(self):
        estoquista = Usuario.objects.create_user("so_estoque", tenant=self.tenant, password=None)
        PapelUsuario.objects.create(usuario=estoquista, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.client.force_login(estoquista)
        self.assertEqual(self.client.get(reverse("fila_compras")).status_code, 403)
        self.assertEqual(self.client.get(reverse("posicao_estoque")).status_code, 200)
        self.assertNotContains(self.client.get(reverse("painel")), "Fila de compras")


class AprovacaoParcialTest(BaseComprasTest):
    def setUp(self):
        super().setUp()
        self.pedido_a, self.item_a = self.pedido_sem_estoque("5")
        self.pedido_b, self.item_b = self.pedido_sem_estoque("5")
        self.cotacao = services.criar_cotacao(
            comprador=self.comprador, fornecedor=self.fornecedor,
            precos={self.item_a.pk: Decimal("100.00"), self.item_b.pk: Decimal("100.00")},
        )
        self.ic_a = self.cotacao.itens.get(item_pedido=self.item_a)
        self.ic_b = self.cotacao.itens.get(item_pedido=self.item_b)

    def decidir(self, dec_b, motivo="Fora do orçamento"):
        return services.decidir_cotacao_por_item(
            self.cotacao, self.diretor, {self.ic_a.pk: "APROVAR", self.ic_b.pk: dec_b}, motivo
        )

    def test_item_eliminado_sai_do_pedido_e_a_cotacao_segue_com_o_resto(self):
        cotacao = self.decidir("ELIMINAR")
        self.assertEqual(cotacao.status, StatusCotacao.APROVADA)
        self.assertEqual(cotacao.valor_total, Decimal("500.00"))
        self.assertEqual(cotacao.itens.count(), 1)
        self.item_b.refresh_from_db()
        self.assertTrue(self.item_b.cancelado)
        self.assertNotIn(self.item_b, list(services.itens_para_cotar()))
        self.pedido_b.refresh_from_db()
        self.assertEqual(self.pedido_b.status, StatusPedido.RECUSADO)  # nada mais a entregar
        self.pedido_a.refresh_from_db()
        self.assertEqual(self.pedido_a.status, StatusPedido.AGUARDANDO_COMPRA)

    def test_item_recotar_volta_para_a_fila(self):
        cotacao = self.decidir("RECOTAR")
        self.assertEqual(cotacao.valor_total, Decimal("500.00"))
        self.item_b.refresh_from_db()
        self.assertFalse(self.item_b.cancelado)
        self.assertIn(self.item_b, list(services.itens_para_cotar()))

    def test_item_nao_aprovado_exige_motivo(self):
        with self.assertRaises(TransicaoInvalida):
            self.decidir("RECOTAR", motivo="")

    def test_nenhum_aprovado_recusa_a_cotacao(self):
        cotacao = services.decidir_cotacao_por_item(
            self.cotacao, self.diretor, {self.ic_a.pk: "RECOTAR", self.ic_b.pk: "RECOTAR"}, "Preço alto"
        )
        self.assertEqual(cotacao.status, StatusCotacao.RECUSADA)
        self.assertIn(self.item_a, list(services.itens_para_cotar()))

    def test_so_o_aprovador_da_faixa_decide(self):
        with self.assertRaises(SemPermissao):
            services.decidir_cotacao_por_item(
                self.cotacao, self.estranho, {self.ic_a.pk: "APROVAR", self.ic_b.pk: "APROVAR"}
            )


class OrdemAutomaticaTest(BaseComprasTest):
    def test_faixa_automatica_ja_gera_a_ordem(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00")  # 100: abaixo do teto
        self.assertEqual(cotacao.status, StatusCotacao.APROVADA)
        self.assertEqual(cotacao.ordem.criada_por, self.comprador)

    def test_aprovacao_da_alcada_gera_a_ordem(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "100.00")  # 500: precisa da diretoria
        self.assertFalse(hasattr(Cotacao.objects.get(pk=cotacao.pk), "ordem"))
        services.decidir_cotacao(cotacao, self.diretor, True)
        self.assertTrue(Cotacao.objects.get(pk=cotacao.pk).ordem)

    def test_gerar_ordem_e_idempotente(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "20.00")
        self.assertEqual(services.gerar_ordem_compra(cotacao, self.comprador).pk, cotacao.ordem.pk)


class AndamentoDaOrdemTest(BaseComprasTest):
    def test_comprador_atualiza_andamento_e_requisitante_ve(self):
        from datetime import date

        pedido, item = self.pedido_sem_estoque("5")
        ordem = self.cotar(item, "20.00").ordem
        self.assertEqual(ordem.andamento, "EMITIDA")
        services.atualizar_andamento(ordem, self.comprador, "EM_TRANSITO", "Transportadora X", date(2026, 11, 3))
        ordem.refresh_from_db()
        self.assertEqual(ordem.andamento, "EM_TRANSITO")
        self.assertEqual(ordem.previsao_entrega, date(2026, 11, 3))
        self.assertEqual(ordem.historico.count(), 2)
        self.client.force_login(pedido.requisitante)
        self.assertContains(self.client.get(reverse("pedido_detalhe", args=[pedido.pk])), "Em trânsito")

    def test_recebida_so_pelo_recebimento(self):
        _, item = self.pedido_sem_estoque("5")
        ordem = self.cotar(item, "20.00").ordem
        with self.assertRaises(TransicaoInvalida):
            services.atualizar_andamento(ordem, self.comprador, "RECEBIDA")

    def test_tela_da_ordem_aceita_a_atualizacao(self):
        _, item = self.pedido_sem_estoque("5")
        ordem = self.cotar(item, "20.00").ordem
        self.client.force_login(self.comprador)
        r = self.client.post(reverse("ordem_detalhe", args=[ordem.pk]), {
            "acao": "andamento", "andamento": "COMPRA_EFETUADA", "previsao": "2026-11-10", "observacao": "Pedido 123",
        })
        self.assertEqual(r.status_code, 302)
        ordem.refresh_from_db()
        self.assertEqual(ordem.andamento, "COMPRA_EFETUADA")


class FaixaLiderEEscopoPorSetorTest(BaseComprasTest):
    def setUp(self):
        super().setUp()
        from contas.alcadas import salvar_tabela
        from contas.models import AprovadorAlcada, Setor

        salvar_tabela(tenant=self.tenant, filial=None, usuario=self.comprador, linhas=[
            (Decimal("300.00"), "AUTOMATICA"), (Decimal("2000.00"), "LIDER_SETOR"),
            (None, "DIRETORIA_UNIDADE"),
        ])
        self.salvar_tabela = salvar_tabela
        self.outro_setor = Setor.objects.create(tenant=self.tenant, filial=self.filial, nome="Outro setor")
        self.vinculo = AprovadorAlcada.objects.get(
            alcada__papel_aprovador="DIRETORIA_UNIDADE", alcada__ativo=True, usuario=self.diretor
        )

    def ids(self, cotacao):
        return set(services.aprovadores_da_cotacao(cotacao).values_list("username", flat=True))

    def test_lider_do_setor_aprova_a_faixa_dele_e_o_diretor_nao(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "100.00")  # 500: faixa do líder
        self.assertEqual(cotacao.alcada.papel_aprovador, "LIDER_SETOR")
        self.assertEqual(self.ids(cotacao), {"aprov"})
        with self.assertRaises(SemPermissao):
            services.decidir_cotacao(cotacao, self.diretor, True)
        self.assertEqual(services.decidir_cotacao(cotacao, self.aprovador, True).status, StatusCotacao.APROVADA)

    def test_sem_lider_disponivel_escala_para_a_diretoria(self):
        _, item = self.pedido_sem_estoque("5")
        self.setor.lideres.set([self.requisitante])  # depois de aprovado, o único líder é quem pediu
        cotacao = self.cotar(item, "100.00")
        self.assertEqual(self.ids(cotacao), {"diretor"})

    def test_diretor_so_aprova_os_setores_que_cobre(self):
        _, item = self.pedido_sem_estoque("5")
        cotacao = self.cotar(item, "1000.00")  # 5.000: diretoria
        self.assertEqual(cotacao.alcada.papel_aprovador, "DIRETORIA_UNIDADE")
        self.assertEqual(self.ids(cotacao), {"diretor"})  # sem setores cadastrados: cobre todos
        self.vinculo.setores.set([self.outro_setor])
        self.assertEqual(self.ids(cotacao), set())
        self.vinculo.setores.set([self.setor])
        self.assertEqual(self.ids(cotacao), {"diretor"})

    def test_nova_versao_da_tabela_preserva_os_setores_do_diretor(self):
        from contas.models import AprovadorAlcada

        self.vinculo.setores.set([self.setor])
        self.salvar_tabela(tenant=self.tenant, filial=None, usuario=self.comprador, linhas=[
            (Decimal("500.00"), "AUTOMATICA"), (Decimal("3000.00"), "LIDER_SETOR"),
            (None, "DIRETORIA_UNIDADE"),
        ])
        novo = AprovadorAlcada.objects.get(
            alcada__papel_aprovador="DIRETORIA_UNIDADE", alcada__ativo=True, usuario=self.diretor
        )
        self.assertEqual(list(novo.setores.all()), [self.setor])

    def test_tela_de_aprovadores_da_faixa_de_lider_nao_tem_lista(self):
        from contas.models import Alcada

        admin = Usuario.objects.create_superuser("root_f", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        faixa = Alcada.objects.get(papel_aprovador="LIDER_SETOR", ativo=True, filial__isnull=True)
        self.assertEqual(self.client.get(reverse("config_alcada_editar", args=[faixa.pk])).status_code, 302)
        self.assertContains(self.client.get(reverse("config_alcada_lista")), "Líder do setor")


class TelaDeAprovadoresFiltraPorUnidadeTest(BaseComprasTest):
    def test_formulario_traz_unidades_da_pessoa_e_setores_por_filial(self):
        from contas.models import Alcada

        admin = Usuario.objects.create_superuser("root_u2", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        faixa = Alcada.objects.get(papel_aprovador="DIRETORIA_UNIDADE", ativo=True, filial__isnull=True)
        pagina = self.client.get(reverse("config_alcada_editar", args=[faixa.pk]))
        self.assertContains(pagina, 'id="unidades-do-usuario"')
        self.assertContains(pagina, f'data-filial="{self.setor.filial_id}"')
        self.assertEqual(pagina.context["unidades_do_usuario"][self.diretor.pk], [self.filial.pk])


class CentroDeCustoDaUnidadeTest(BaseComprasTest):
    def test_centro_de_outra_unidade_e_rejeitado_e_a_lista_e_filtrada(self):
        from tenancy.models import CentroCusto, Filial, centros_da_filial

        vc = Filial.objects.get(sigla="VC")
        de_fora = CentroCusto.objects.create(tenant=self.tenant, filial=vc, nome="Cantina VC")
        pedido, item = self.pedido_sem_estoque("5")
        unidade = pedido.almoxarifado_destino.filial_id
        self.assertNotIn(de_fora, list(centros_da_filial(unidade)))
        self.assertIn(self.centro, list(centros_da_filial(unidade)))
        with self.assertRaises(TransicaoInvalida):
            services.criar_cotacao(
                comprador=self.comprador, fornecedor=self.fornecedor,
                precos={item.pk: Decimal("20.00")}, centros={item.pk: de_fora},
            )
        cotacao = self.cotar(item, "20.00")
        with self.assertRaises(TransicaoInvalida):
            services.definir_centros(cotacao, {cotacao.itens.get().pk: de_fora}, self.comprador)

    def test_pedido_rejeita_centro_de_outra_unidade_na_tela(self):
        from pedidos.services import transicionar
        from tenancy.models import CentroCusto, Filial

        vc = Filial.objects.get(sigla="VC")
        de_fora = CentroCusto.objects.create(tenant=self.tenant, filial=vc, nome="Cantina VC")
        pedido, _ = self.pedido_sem_estoque("5")
        estoquista = Usuario.objects.create_user("est_cc", tenant=self.tenant, password=None)
        PapelUsuario.objects.create(usuario=estoquista, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.client.force_login(estoquista)
        antes = Pedido.objects.get(pk=pedido.pk).centro_custo_id
        self.client.post(reverse("pedido_detalhe", args=[pedido.pk]), {"acao": "definir_centro", "centro_custo": de_fora.pk})
        self.assertEqual(Pedido.objects.get(pk=pedido.pk).centro_custo_id, antes)
        pagina = self.client.get(reverse("pedido_detalhe", args=[pedido.pk]))
        self.assertNotIn(de_fora, list(pagina.context["centros"]))  # a lista só traz centros da unidade
