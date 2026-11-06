from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.contrib.sessions.backends.db import SessionStore

from cadastros.models import Categoria, Produto
from contas.models import Modulo, ModoAprovacaoSetor, Papel, PapelUsuario, RegraAprovacaoEmpresa, Setor
from estoque.models import DebitoAlmoxarifado, SaldoEstoque
from estoque.services import EstoqueInsuficiente, registrar_entrada, registrar_saida, transferir
from tenancy.models import Almoxarifado, CentroCusto, Evento, Filial, MotivoRequisicao
from tenancy.testing import TenantTestCase

from .forms import PedidoForm
from .models import ItemPedido, OrigemAtendimento, Pedido, StatusPedido
from .services import (
    SemPermissao,
    TransicaoInvalida,
    aprovar_como_lider,
    entregar,
    transicionar,
)

Usuario = get_user_model()


class BaseFluxoTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.filial = Filial.objects.get(sigla="CP")
        self.setor = Setor.objects.create(
            tenant=self.tenant, filial=self.filial, nome="Pedagógico"
        )
        self.requisitante = Usuario.objects.create_user(
            "req", password="senha-forte-123", tenant=self.tenant,
            filial=self.filial, setor=self.setor,
        )
        self.aprovador = Usuario.objects.create_user(
            "aprov", password="senha-forte-123", tenant=self.tenant, filial=self.filial
        )
        self.suprimentos = Usuario.objects.create_user(
            "supr", password="senha-forte-123", tenant=self.tenant
        )
        self.setor.lideres.add(self.aprovador)

        self.central = Almoxarifado.objects.get(central=True)
        self.destino = Almoxarifado.objects.get(filial__sigla="CP", nome="Ed. Infantil")
        self.categoria = Categoria.objects.get(nome="Pedagógico")
        self.produto = Produto.objects.create(
            nome="Argila", especificacao="pacote 1kg", categoria=self.categoria
        )
        self.centro = CentroCusto.objects.get(filial__sigla="CP", nome="Pedagógico")
        self.motivo = MotivoRequisicao.objects.get(nome="Projeto/Ação Pedagógica")

        registrar_entrada(
            produto=self.produto, destino=self.destino, quantidade=Decimal("50"),
            valor_unitario=Decimal("10.00"), responsavel=self.suprimentos,
        )

    def novo_pedido(self, quantidade="10"):
        pedido = Pedido.objects.create(
            requisitante=self.requisitante, almoxarifado_destino=self.destino,
            centro_custo=self.centro, motivo=self.motivo,
        )
        ItemPedido.objects.create(pedido=pedido, produto=self.produto, quantidade=Decimal(quantidade))
        return pedido


class CicloCompletoTest(BaseFluxoTest):
    def test_ciclo_estoque_completo(self):
        pedido = self.novo_pedido("10")
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        transicionar(pedido, StatusPedido.APROVADO_UNIDADE, self.aprovador)

        item = pedido.itens.first()
        self.assertEqual(item.origem_atendimento, OrigemAtendimento.ESTOQUE)

        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        entregar(pedido, self.suprimentos)
        transicionar(pedido, StatusPedido.CONCLUIDO, self.requisitante)

        self.assertEqual(pedido.status, StatusPedido.CONCLUIDO)
        saldo_destino = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.destino)
        self.assertEqual(saldo_destino.quantidade, Decimal("40"))  # a baixa saiu do almoxarifado do pedido
        self.assertEqual(pedido.eventos.count(), 5)
        self.assertFalse(DebitoAlmoxarifado.objects.exists())

    def test_item_sem_saldo_classificado_como_compra(self):
        pedido = self.novo_pedido("100")
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        transicionar(pedido, StatusPedido.APROVADO_UNIDADE, self.aprovador)
        self.assertEqual(pedido.itens.first().origem_atendimento, OrigemAtendimento.COMPRA)

    def test_recusa_exige_motivo(self):
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        with self.assertRaises(TransicaoInvalida):
            transicionar(pedido, StatusPedido.RECUSADO, self.aprovador, observacao="")
        transicionar(pedido, StatusPedido.RECUSADO, self.aprovador, observacao="Sem verba no mês")
        self.assertEqual(pedido.status, StatusPedido.RECUSADO)
        self.assertEqual(pedido.eventos.last().observacao, "Sem verba no mês")

    def test_nao_vinculado_nao_aprova(self):
        intruso = Usuario.objects.create_user(
            "intruso", password="senha-forte-123", tenant=self.tenant
        )
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        with self.assertRaises(SemPermissao):
            transicionar(pedido, StatusPedido.APROVADO_UNIDADE, intruso)

    def test_requisitante_nao_aprova_proprio_pedido(self):
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        with self.assertRaises(SemPermissao):
            transicionar(pedido, StatusPedido.APROVADO_UNIDADE, self.requisitante)

    def test_transicao_invalida_bloqueada(self):
        pedido = self.novo_pedido()
        with self.assertRaises(TransicaoInvalida):
            transicionar(pedido, StatusPedido.ENTREGUE, self.suprimentos)

    def test_pedido_sem_itens_nao_envia(self):
        pedido = Pedido.objects.create(
            requisitante=self.requisitante, almoxarifado_destino=self.destino,
            centro_custo=self.centro, motivo=self.motivo,
        )
        with self.assertRaises(TransicaoInvalida):
            transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)


class EstoqueServicesTest(BaseFluxoTest):
    def test_custo_medio_recalculado(self):
        registrar_entrada(
            produto=self.produto, destino=self.destino, quantidade=Decimal("50"),
            valor_unitario=Decimal("20.00"), responsavel=self.suprimentos,
        )
        saldo = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.destino)
        self.assertEqual(saldo.quantidade, Decimal("100"))
        self.assertEqual(saldo.custo_medio, Decimal("15.00"))

    def test_entrada_valor_zero_preserva_custo(self):
        registrar_entrada(
            produto=self.produto, destino=self.central, quantidade=Decimal("30"),
            responsavel=self.suprimentos, observacao="Doação lista de material",
        )
        saldo = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.central)
        self.assertEqual(saldo.quantidade, Decimal("30"))
        self.assertEqual(saldo.custo_medio, Decimal("0"))

    def test_saida_sem_saldo_bloqueada(self):
        with self.assertRaises(EstoqueInsuficiente):
            registrar_saida(
                produto=self.produto, origem=self.central, quantidade=Decimal("1"),
                responsavel=self.suprimentos,
            )

    def test_transferencia_entre_unidades_gera_debito(self):
        vc = Almoxarifado.objects.get(filial__sigla="VC", nome="Geral")
        cp = Almoxarifado.objects.get(filial__sigla="CP", nome="Geral")
        registrar_entrada(
            produto=self.produto, destino=vc, quantidade=Decimal("20"),
            responsavel=self.suprimentos,
        )
        transferir(
            produto=self.produto, origem=vc, destino=cp, quantidade=Decimal("5"),
            responsavel=self.suprimentos, observacao="Empréstimo para projeto",
        )
        debito = DebitoAlmoxarifado.objects.get()
        self.assertEqual(debito.devedor, cp)
        self.assertEqual(debito.credor, vc)
        self.assertEqual(debito.quantidade, Decimal("5"))


class AprovacaoPorSetorTest(BaseFluxoTest):
    def setUp(self):
        super().setUp()
        self.segundo_lider = Usuario.objects.create_user(
            "lider2", password="senha-forte-123", tenant=self.tenant, filial=self.filial
        )

    def definir_modo(self, modo):
        RegraAprovacaoEmpresa.objects.update_or_create(
            empresa=self.filial.empresa, defaults={"modo_aprovacao_setor": modo}
        )

    def test_qualquer_lider_aprova_sozinho(self):
        self.setor.lideres.add(self.segundo_lider)
        self.definir_modo(ModoAprovacaoSetor.QUALQUER_LIDER)
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)

        aprovar_como_lider(pedido, self.aprovador)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

    def test_todos_lideres_precisam_aprovar(self):
        self.setor.lideres.add(self.segundo_lider)
        self.definir_modo(ModoAprovacaoSetor.TODOS_LIDERES)
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)

        aprovar_como_lider(pedido, self.aprovador)
        self.assertEqual(pedido.status, StatusPedido.ENVIADO, "Falta o segundo líder")
        self.assertEqual(pedido.aprovacoes_lider.count(), 1)

        aprovar_como_lider(pedido, self.segundo_lider)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

    def test_regra_do_setor_vale_mais_que_a_da_empresa(self):
        self.setor.lideres.add(self.segundo_lider)
        self.definir_modo(ModoAprovacaoSetor.QUALQUER_LIDER)  # empresa: um basta
        self.setor.modo_aprovacao = ModoAprovacaoSetor.TODOS_LIDERES  # setor: todos
        self.setor.save()
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)

        aprovar_como_lider(pedido, self.aprovador)
        self.assertEqual(pedido.status, StatusPedido.ENVIADO)
        aprovar_como_lider(pedido, self.segundo_lider)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

    def test_envio_bloqueado_sem_setor(self):
        self.requisitante.setor = None
        self.requisitante.save(update_fields=["setor"])
        pedido = self.novo_pedido()
        with self.assertRaises(TransicaoInvalida):
            transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)

    def test_envio_bloqueado_sem_lider_no_setor(self):
        self.setor.lideres.clear()
        pedido = self.novo_pedido()
        with self.assertRaises(TransicaoInvalida):
            transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)

    def test_lider_unico_pula_a_aprovacao_do_setor(self):
        self.setor.lideres.set([self.requisitante])
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)
        self.assertEqual(pedido.aprovacoes_lider.count(), 0)
        self.assertEqual(pedido.eventos.last().para_status, StatusPedido.APROVADO_UNIDADE)

    def test_lider_com_outro_lider_no_setor_precisa_de_aprovacao(self):
        self.setor.lideres.add(self.requisitante)  # aprovador continua sendo líder
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        self.assertEqual(pedido.status, StatusPedido.ENVIADO)
        aprovar_como_lider(pedido, self.aprovador)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

    def test_requisitante_lider_nao_conta_como_voto_pendente(self):
        self.setor.lideres.add(self.requisitante, self.segundo_lider)
        self.definir_modo(ModoAprovacaoSetor.TODOS_LIDERES)
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)

        aprovar_como_lider(pedido, self.aprovador)
        aprovar_como_lider(pedido, self.segundo_lider)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

    def test_lider_de_outro_setor_nao_aprova(self):
        outro_setor = Setor.objects.create(
            tenant=self.tenant, filial=self.filial, nome="Financeiro"
        )
        outro_setor.lideres.add(self.segundo_lider)
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        with self.assertRaises(SemPermissao):
            aprovar_como_lider(pedido, self.segundo_lider)


class CarrinhoTest(TenantTestCase):
    def test_produto_sem_codigo_aparece_no_carrinho(self):
        from . import cart

        categoria = Categoria.objects.get(nome="Pedagógico")
        produto = Produto.objects.create(nome="Sem código", especificacao="x", categoria=categoria)
        self.assertIsNone(produto.codigo)
        sessao = SessionStore()
        self.assertTrue(cart.adicionar(sessao, str(produto.pk), "2"))
        self.assertEqual(cart.quantidade_total(sessao), 1)
        self.assertEqual([i["produto"] for i in cart.itens(sessao)], [produto])

    def test_chave_orfa_nao_infla_o_contador(self):
        from . import cart

        sessao = SessionStore()
        sessao["carrinho"] = {"None": "1", "999999": "1"}
        self.assertEqual(cart.quantidade_total(sessao), 0)


class CheckoutFormTest(BaseFluxoTest):
    def setUp(self):
        super().setUp()
        self.vc = Filial.objects.get(sigla="VC")
        self.motivo_evento = MotivoRequisicao.objects.get(nome="Evento Pedagógico")
        self.evento = Evento.objects.create(tenant=self.tenant, filial=self.filial, nome="Festa Junina")
        self.evento_vc = Evento.objects.create(tenant=self.tenant, filial=self.vc, nome="Feira VC")

    def dados(self, **sobrescrever):
        base = {
            "filial_destino": self.filial.pk,
            "almoxarifado_destino": self.filial.almoxarifado_padrao().pk,
            "observacao": "Material para a semana",
            "prioridade": "NORMAL",
            "data_desejada": (timezone.localdate() + timedelta(days=30)).isoformat(),
            "motivo": self.motivo.pk,
        }
        base.update(sobrescrever)
        return base

    def test_pedido_valido_usa_o_almoxarifado_escolhido(self):
        self.requisitante.almoxarifados.add(self.destino)
        form = PedidoForm(self.dados(almoxarifado_destino=self.destino.pk), usuario=self.requisitante)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).almoxarifado_destino, self.destino)

    def test_sem_almoxarifados_marcados_vale_o_padrao_da_unidade(self):
        padrao = self.filial.almoxarifado_padrao()
        form = PedidoForm(self.dados(almoxarifado_destino=padrao.pk), usuario=self.requisitante)
        self.assertTrue(form.is_valid(), form.errors)

    def test_almoxarifado_que_nao_e_do_usuario_rejeitado(self):
        self.requisitante.almoxarifados.add(self.destino)
        outro = self.filial.almoxarifado_padrao()
        form = PedidoForm(self.dados(almoxarifado_destino=outro.pk), usuario=self.requisitante)
        self.assertFalse(form.is_valid())
        self.assertIn("almoxarifado_destino", form.errors)

    def test_motivo_evento_exige_evento(self):
        form = PedidoForm(self.dados(motivo=self.motivo_evento.pk), usuario=self.requisitante)
        self.assertFalse(form.is_valid())
        self.assertIn("evento", form.errors)

    def test_motivo_evento_com_evento_da_unidade_e_valido(self):
        dados = self.dados(motivo=self.motivo_evento.pk, evento=self.evento.pk)
        form = PedidoForm(dados, usuario=self.requisitante)
        self.assertTrue(form.is_valid(), form.errors)

    def test_evento_de_outra_unidade_rejeitado(self):
        self.requisitante.filiais_permitidas.add(self.filial, self.vc)
        dados = self.dados(motivo=self.motivo_evento.pk, evento=self.evento_vc.pk)
        form = PedidoForm(dados, usuario=self.requisitante)
        self.assertFalse(form.is_valid())
        self.assertIn("evento", form.errors)

    def test_data_no_passado_rejeitada(self):
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        form = PedidoForm(self.dados(data_desejada=ontem), usuario=self.requisitante)
        self.assertFalse(form.is_valid())
        self.assertIn("data_desejada", form.errors)

    def test_unidade_fora_das_permitidas_rejeitada(self):
        self.requisitante.filiais_permitidas.add(self.filial)
        form = PedidoForm(
            self.dados(filial_destino=self.vc.pk, almoxarifado_destino=self.vc.almoxarifado_padrao().pk),
            usuario=self.requisitante,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("filial_destino", form.errors)

    def test_unidade_nao_pede_no_almoxarifado_de_outra(self):
        self.requisitante.filiais_permitidas.add(self.filial, self.vc)
        form = PedidoForm(
            self.dados(filial_destino=self.filial.pk, almoxarifado_destino=self.vc.almoxarifado_padrao().pk),
            usuario=self.requisitante,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("almoxarifado_destino", form.errors)

    def test_resumo_mostra_estoque_por_almoxarifado_do_usuario(self):
        self.requisitante.almoxarifados.add(self.destino, self.filial.almoxarifado_padrao())
        self.client.force_login(self.requisitante)
        self.client.post(reverse("carrinho_adicionar"), {"produto": self.produto.pk, "quantidade": "2"})
        for nome in ("carrinho_ver", "checkout"):
            pagina = self.client.get(reverse(nome))
            self.assertContains(pagina, "Estoque por almoxarifado")
            self.assertContains(pagina, "atende 1 de 1")  # o Ed. Infantil tem os 50 de partida

    def test_sem_permitidas_vale_a_filial_do_usuario(self):
        form = PedidoForm(usuario=self.requisitante)
        self.assertEqual(list(form.filiais), [self.filial])

    def test_usuario_sem_destino_nao_tem_opcao(self):
        self.requisitante.filial = None
        form = PedidoForm(usuario=self.requisitante)
        self.assertFalse(form.filiais.exists())

    def test_filial_sem_almoxarifado_de_destino_fica_de_fora(self):
        csc = Filial.objects.get(sigla="CSC")  # só tem o almoxarifado central
        self.requisitante.filiais_permitidas.add(csc, self.filial)
        form = PedidoForm(usuario=self.requisitante)
        self.assertEqual(list(form.filiais), [self.filial])


class RelatorioEventosTest(BaseFluxoTest):
    def test_custo_de_estoque_soma_quantidade_vezes_custo_medio(self):
        evento = Evento.objects.create(tenant=self.tenant, filial=self.filial, nome="Festa Junina")
        pedido = self.novo_pedido("10")
        pedido.evento = evento
        pedido.save(update_fields=["evento"])
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        entregar(pedido, self.suprimentos)

        admin = Usuario.objects.create_superuser(
            "admin", password="senha-forte-123", tenant=self.tenant
        )
        self.client.force_login(admin)
        resposta = self.client.get(reverse("relatorio_eventos"))

        self.assertEqual(resposta.status_code, 200)
        linha = next(l for l in resposta.context["linhas"] if l["evento"] == evento)
        self.assertEqual(linha["custo_estoque"], Decimal("100.00"))  # 10 un × R$ 10,00
        self.assertEqual(linha["pedidos"], 1)
        self.assertEqual(resposta.context["total_estoque"], Decimal("100.00"))


class EntregaParcialTest(BaseFluxoTest):
    def pedido_em_separacao(self, quantidade="10"):
        pedido = self.novo_pedido(quantidade)
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        return pedido

    def saldo(self, almoxarifado):
        return SaldoEstoque.objects.get(produto=self.produto, almoxarifado=almoxarifado).quantidade

    def test_entrega_parcial_e_depois_o_restante(self):
        pedido = self.pedido_em_separacao("10")
        item = pedido.itens.get()

        entregar(pedido, self.suprimentos, {item.pk: Decimal("4")})
        item.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.ENTREGA_PARCIAL)
        self.assertEqual(item.quantidade_entregue, Decimal("4"))
        self.assertEqual(self.saldo(self.destino), Decimal("46"))

        entregar(pedido, self.suprimentos)  # sem quantidades: entrega todo o pendente
        item.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.ENTREGUE)
        self.assertEqual(item.quantidade_entregue, Decimal("10"))
        self.assertEqual(self.saldo(self.destino), Decimal("40"))

    def test_quantidade_acima_do_pendente_rejeitada(self):
        pedido = self.pedido_em_separacao("10")
        item = pedido.itens.get()
        with self.assertRaises(TransicaoInvalida):
            entregar(pedido, self.suprimentos, {item.pk: Decimal("11")})
        self.assertEqual(self.saldo(self.destino), Decimal("50"))

    def test_entrega_sem_nenhuma_quantidade_rejeitada(self):
        pedido = self.pedido_em_separacao("10")
        item = pedido.itens.get()
        with self.assertRaises(TransicaoInvalida):
            entregar(pedido, self.suprimentos, {item.pk: Decimal("0")})

    def test_estoque_insuficiente_nao_deixa_entrega_pela_metade(self):
        pedido = self.pedido_em_separacao("10")
        item = pedido.itens.get()
        registrar_saida(  # o estoque do almoxarifado cai para 3 depois da aprovação
            produto=self.produto, origem=self.destino, quantidade=Decimal("47"),
            responsavel=self.suprimentos,
        )
        with self.assertRaises(EstoqueInsuficiente):
            entregar(pedido, self.suprimentos, {item.pk: Decimal("5")})
        item.refresh_from_db()
        self.assertEqual(item.quantidade_entregue, Decimal("0"))
        self.assertEqual(pedido.status, StatusPedido.EM_SEPARACAO)

    def test_pedido_parcial_continua_na_fila_de_suprimentos(self):
        from .views import FILA_SUPRIMENTOS
        self.assertIn(StatusPedido.ENTREGA_PARCIAL, FILA_SUPRIMENTOS)


class PedidoComItemDeCompraTest(BaseFluxoTest):
    def aprovar(self, pedido):
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)

    def test_pedido_so_de_compra_fica_aguardando_compra(self):
        pedido = self.novo_pedido("100")  # estoque central só tem 50
        self.aprovar(pedido)
        self.assertEqual(pedido.status, StatusPedido.AGUARDANDO_COMPRA)
        with self.assertRaises(TransicaoInvalida):
            transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)

    def test_pedido_misto_nao_fecha_com_item_de_compra_pendente(self):
        sem_estoque = Produto.objects.create(
            nome="Papel crepom", especificacao="rolo", categoria=self.categoria
        )
        pedido = self.novo_pedido("10")  # tem estoque
        ItemPedido.objects.create(pedido=pedido, produto=sem_estoque, quantidade=Decimal("5"))
        self.aprovar(pedido)
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        entregar(pedido, self.suprimentos)
        self.assertEqual(pedido.status, StatusPedido.ENTREGA_PARCIAL)
        with self.assertRaises(TransicaoInvalida):  # nada mais do estoque para entregar
            entregar(pedido, self.suprimentos)


class SugestaoDeProdutoTest(BaseFluxoTest):
    def test_sugestao_vira_produto_cadastrado(self):
        from django.urls import reverse

        from .models import StatusSugestao, SugestaoProduto

        self.client.force_login(self.requisitante)
        resposta = self.client.post(reverse("sugerir_produto"), {"nome": "Giz de cera 24 cores", "especificacao": "Acrilex"})
        self.assertEqual(resposta.status_code, 302)
        sugestao = SugestaoProduto.objects.get()
        self.assertEqual(sugestao.status, StatusSugestao.PENDENTE)
        # quem não é de suprimentos não acessa o cadastro
        self.assertEqual(self.client.get(reverse("produto_lista")).status_code, 403)

        estoquista = Usuario.objects.create_user("suprimentista", tenant=self.tenant, password=None)
        PapelUsuario.objects.create(usuario=estoquista, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.client.force_login(estoquista)
        pagina = self.client.get(reverse("produto_novo"), {"sugestao": sugestao.pk})
        self.assertContains(pagina, "Giz de cera 24 cores")
        self.client.post(reverse("produto_novo"), {
            "sugestao": sugestao.pk, "nome": "Giz de cera 24 cores", "especificacao": "Acrilex",
            "categoria": self.categoria.pk, "grupo": "MATERIAL", "unidade_medida": "UN", "ativo": "on",
        })
        sugestao.refresh_from_db()
        self.assertEqual(sugestao.status, StatusSugestao.CADASTRADO)
        self.assertEqual(sugestao.produto.nome, "Giz de cera 24 cores")


class FilaDeSuprimentosVisoesTest(BaseFluxoTest):
    def test_fila_abre_em_cards_lista_e_kanban(self):
        estoquista = Usuario.objects.create_user("filista", tenant=self.tenant, password=None)
        PapelUsuario.objects.create(usuario=estoquista, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.client.force_login(estoquista)
        for visao in ("cards", "lista", "kanban", "x"):
            self.assertEqual(self.client.get(reverse("fila_suprimentos"), {"visao": visao}).status_code, 200)
        self.assertContains(self.client.get(reverse("fila_suprimentos"), {"visao": "kanban"}), "Em separação")


class CatalogoFiltroDeEstoqueTest(BaseFluxoTest):
    def test_filtra_com_e_sem_estoque_central(self):
        self.requisitante.almoxarifados.add(self.destino)
        self.client.force_login(self.requisitante)
        todos = self.client.get(reverse("catalogo")).context["produtos"]
        com = self.client.get(reverse("catalogo"), {"estoque": "com"}).context["produtos"]
        sem = self.client.get(reverse("catalogo"), {"estoque": "sem"}).context["produtos"]
        self.assertEqual(len(com) + len(sem), len(todos))
        self.assertTrue(all(p.saldo > 0 for p in com))
        self.assertTrue(all(p.saldo <= 0 for p in sem))


class CentroNaEntregaTest(BaseFluxoTest):
    def test_entrega_exige_centro_e_grava_na_movimentacao(self):
        from estoque.models import Movimentacao

        pedido = self.novo_pedido("1")
        registrar_entrada(produto=self.papel_produto if hasattr(self, "papel_produto") else self.produto,
                          destino=self.destino, quantidade=Decimal("10"),
                          valor_unitario=Decimal("2"), responsavel=self.requisitante)
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        Pedido.objects.filter(pk=pedido.pk).update(centro_custo=None)
        pedido.refresh_from_db()
        with self.assertRaises(TransicaoInvalida):
            entregar(pedido, self.suprimentos)
        Pedido.objects.filter(pk=pedido.pk).update(centro_custo=self.centro)
        pedido.refresh_from_db()
        entregar(pedido, self.suprimentos)
        self.assertEqual(Movimentacao.objects.filter(pedido=pedido).first().centro_custo, self.centro)


class CadastroDeCentroDeCustoTest(BaseFluxoTest):
    def test_superadmin_cria_e_edita_centro_por_filial(self):
        from tenancy.models import CentroCusto

        admin = Usuario.objects.create_superuser("root_cc", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        filial = Filial.objects.get(sigla="VC")
        resposta = self.client.post(reverse("config_centro_novo"), {"filial": filial.pk, "nome": "Cantina", "ativo": "on"})
        self.assertEqual(resposta.status_code, 302)
        centro = CentroCusto.objects.get(filial=filial, nome="Cantina")
        self.client.post(reverse("config_centro_editar", args=[centro.pk]), {"filial": filial.pk, "nome": "Cantina Central", "ativo": "on"})
        centro.refresh_from_db()
        self.assertEqual(centro.nome, "Cantina Central")
        self.assertContains(self.client.get(reverse("config_centro_lista"), {"filial": filial.pk}), "Cantina Central")


class CarrinhoSemRecarregarTest(BaseFluxoTest):
    def test_adicionar_via_fetch_devolve_json_e_nao_redireciona(self):
        self.client.force_login(self.requisitante)
        r = self.client.post(
            reverse("carrinho_adicionar"), {"produto": self.produto.pk, "quantidade": "2"},
            headers={"x-requested-with": "fetch"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["qtd"], 1)
        self.assertTrue(r.json()["ok"])
        r = self.client.post(
            reverse("carrinho_adicionar"), {"produto": self.produto.pk, "quantidade": "abc"},
            headers={"x-requested-with": "fetch"},
        )
        self.assertEqual(r.status_code, 400)


class SlaDaDataDesejadaTest(BaseFluxoTest):
    """A data desejada precisa respeitar o SLA (estoque x compra), em dias úteis."""

    def setUp(self):
        super().setUp()
        self.padrao = self.filial.almoxarifado_padrao()
        registrar_entrada(
            produto=self.produto, destino=self.padrao, quantidade=Decimal("50"),
            valor_unitario=Decimal("10.00"), responsavel=self.suprimentos,
        )

    def dados(self, data):
        return {
            "filial_destino": self.filial.pk,
            "almoxarifado_destino": self.padrao.pk,
            "observacao": "Material",
            "prioridade": "NORMAL",
            "data_desejada": data.isoformat(),
            "motivo": self.motivo.pk,
        }

    def itens(self, quantidade):
        return [{"produto": self.produto, "quantidade": Decimal(quantidade)}]

    def minimos(self):
        from tenancy.calendario import prazos_sla
        return prazos_sla(self.filial)

    def test_pedido_coberto_pelo_estoque_usa_o_prazo_do_estoque(self):
        minimo = self.minimos()["estoque"]
        ok = PedidoForm(self.dados(minimo), usuario=self.requisitante, itens=self.itens("10"))
        self.assertTrue(ok.is_valid(), ok.errors)
        cedo = PedidoForm(self.dados(minimo - timedelta(days=1)), usuario=self.requisitante, itens=self.itens("10"))
        self.assertFalse(cedo.is_valid())
        self.assertIn("data_desejada", cedo.errors)

    def test_item_sem_saldo_usa_o_prazo_de_compra(self):
        prazos = self.minimos()
        # 500 > saldo (50): precisa comprar. A data do estoque já não basta.
        form = PedidoForm(self.dados(prazos["estoque"]), usuario=self.requisitante, itens=self.itens("500"))
        self.assertFalse(form.is_valid())
        self.assertIn("compra", str(form.errors["data_desejada"]))
        ok = PedidoForm(self.dados(prazos["compra"]), usuario=self.requisitante, itens=self.itens("500"))
        self.assertTrue(ok.is_valid(), ok.errors)

    def test_prazo_configurado_vale_no_lugar_do_padrao(self):
        from tenancy.models import ConfiguracaoSLA
        ConfiguracaoSLA.objects.create(tenant=self.tenant, prazo_estoque_dias=10, prazo_compra_dias=15)
        minimo = self.minimos()["estoque"]
        antigo_padrao = self.minimos()["estoque"] - timedelta(days=5)
        self.assertFalse(
            PedidoForm(self.dados(antigo_padrao), usuario=self.requisitante, itens=self.itens("10")).is_valid()
        )
        self.assertTrue(PedidoForm(self.dados(minimo), usuario=self.requisitante, itens=self.itens("10")).is_valid())

    def test_recesso_empurra_o_prazo_minimo(self):
        from tenancy.models import PeriodoNaoUtil
        antes = self.minimos()["estoque"]
        hoje = timezone.localdate()
        PeriodoNaoUtil.objects.create(
            tenant=self.tenant, nome="Recesso", inicio=hoje + timedelta(days=1), fim=hoje + timedelta(days=14),
        )
        depois = self.minimos()["estoque"]
        self.assertGreater(depois, antes + timedelta(days=10))
        self.assertFalse(
            PedidoForm(self.dados(antes), usuario=self.requisitante, itens=self.itens("10")).is_valid()
        )


class CancelamentoDePedidoTest(BaseFluxoTest):
    """Requisitante cancela até a aprovação; aprovador e suprimentos, até a entrega. Motivo sempre."""

    def setUp(self):
        super().setUp()
        from .services import cancelar_item, cancelar_pedido, pode_cancelar
        self.cancelar_pedido, self.cancelar_item, self.pode_cancelar = cancelar_pedido, cancelar_item, pode_cancelar
        PapelUsuario.objects.create(usuario=self.suprimentos, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.estranho = Usuario.objects.create_user("estranho", password="x", tenant=self.tenant)

    def enviado(self):
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        return pedido

    def aprovado(self):
        pedido = self.enviado()
        aprovar_como_lider(pedido, self.aprovador)
        pedido.refresh_from_db()
        return pedido

    def test_requisitante_cancela_antes_da_aprovacao_com_motivo(self):
        pedido = self.enviado()
        self.cancelar_pedido(pedido, self.requisitante, "Não preciso mais")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.CANCELADO)
        self.assertEqual(pedido.motivo_cancelamento, "Não preciso mais")
        self.assertEqual(pedido.cancelado_por, self.requisitante)
        self.assertTrue(all(i.cancelado for i in pedido.itens.all()))
        ultimo = pedido.eventos.last()
        self.assertEqual(ultimo.para_status, StatusPedido.CANCELADO)
        self.assertIn("Não preciso mais", ultimo.observacao)

    def test_motivo_e_obrigatorio(self):
        pedido = self.enviado()
        for vazio in ("", "   "):
            with self.assertRaises(TransicaoInvalida):
                self.cancelar_pedido(pedido, self.requisitante, vazio)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.ENVIADO)

    def test_requisitante_nao_cancela_depois_da_aprovacao(self):
        pedido = self.aprovado()
        self.assertFalse(self.pode_cancelar(self.requisitante, pedido))
        with self.assertRaises(SemPermissao):
            self.cancelar_pedido(pedido, self.requisitante, "Desisti")

    def test_aprovador_cancela_ate_a_entrega(self):
        pedido = self.aprovado()
        self.cancelar_pedido(pedido, self.aprovador, "Fora do orçamento")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.CANCELADO)

    def test_aprovador_cancela_pedido_ainda_em_aprovacao(self):
        pedido = self.enviado()
        self.cancelar_pedido(pedido, self.aprovador, "Duplicado")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.CANCELADO)

    def test_suprimentos_cancela_em_separacao_mas_nao_depois_de_entregar(self):
        pedido = self.aprovado()
        transicionar(pedido, StatusPedido.EM_SEPARACAO, self.suprimentos)
        self.assertTrue(self.pode_cancelar(self.suprimentos, pedido))

        outro = self.aprovado()
        transicionar(outro, StatusPedido.EM_SEPARACAO, self.suprimentos)
        entregar(outro, self.suprimentos)
        outro.refresh_from_db()
        self.assertFalse(self.pode_cancelar(self.suprimentos, outro))
        with self.assertRaises(TransicaoInvalida):
            self.cancelar_pedido(outro, self.suprimentos, "Tarde demais")

        self.cancelar_pedido(pedido, self.suprimentos, "Produto descontinuado")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.CANCELADO)

    def test_quem_nao_participa_nao_cancela(self):
        pedido = self.enviado()
        self.assertFalse(self.pode_cancelar(self.estranho, pedido))
        with self.assertRaises(SemPermissao):
            self.cancelar_pedido(pedido, self.estranho, "Quero cancelar")

    def test_pedido_cancelado_nao_cancela_de_novo(self):
        pedido = self.enviado()
        self.cancelar_pedido(pedido, self.requisitante, "Teste")
        with self.assertRaises(TransicaoInvalida):
            self.cancelar_pedido(pedido, self.aprovador, "De novo")

    def test_cancelar_um_item_mantem_o_resto_e_o_ultimo_cancela_o_pedido(self):
        pedido = self.novo_pedido()
        outro_produto = Produto.objects.create(nome="Cola", especificacao="90g", categoria=self.categoria)
        ItemPedido.objects.create(pedido=pedido, produto=outro_produto, quantidade=Decimal("2"))
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        primeiro, segundo = list(pedido.itens.order_by("pk"))

        self.cancelar_item(primeiro, self.requisitante, "Já tenho")
        primeiro.refresh_from_db()
        pedido.refresh_from_db()
        self.assertTrue(primeiro.cancelado)
        self.assertEqual(pedido.status, StatusPedido.ENVIADO)

        self.cancelar_item(segundo, self.requisitante, "Também não")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.CANCELADO)

    def test_item_em_cotacao_em_andamento_bloqueia_o_cancelamento(self):
        from unittest import mock
        pedido = self.aprovado()
        with mock.patch("pedidos.services._cotacoes_em_andamento", return_value=[12]):
            with self.assertRaisesMessage(TransicaoInvalida, "#12"):
                self.cancelar_pedido(pedido, self.aprovador, "Quero cancelar")
        pedido.refresh_from_db()
        self.assertNotEqual(pedido.status, StatusPedido.CANCELADO)

    def test_cancelados_ficam_fora_das_listas_ate_filtrar_por_status(self):
        ativo = self.enviado()
        cancelado = self.enviado()
        self.cancelar_pedido(cancelado, self.requisitante, "Erro")
        self.client.force_login(self.requisitante)

        lista = self.client.get(reverse("pedido_lista"))
        self.assertContains(lista, f"Pedido #{ativo.pk}")
        self.assertNotContains(lista, f"Pedido #{cancelado.pk}")

        so_cancelados = self.client.get(reverse("pedido_lista"), {"status": "CANCELADO"})
        self.assertContains(so_cancelados, f"Pedido #{cancelado.pk}")
        self.assertNotContains(so_cancelados, f"Pedido #{ativo.pk}")

    def test_painel_nao_conta_pedido_cancelado_como_em_andamento(self):
        pedido = self.enviado()
        self.client.force_login(self.requisitante)
        antes = self.client.get(reverse("painel")).context["qtd_meus_abertos"]
        self.cancelar_pedido(pedido, self.requisitante, "Erro")
        depois = self.client.get(reverse("painel")).context["qtd_meus_abertos"]
        self.assertEqual(depois, antes - 1)

    def test_cancelar_pela_tela_do_pedido(self):
        pedido = self.enviado()
        self.client.force_login(self.requisitante)
        sem_motivo = self.client.post(reverse("pedido_detalhe", args=[pedido.pk]),
                                      {"acao": "cancelar_pedido", "observacao": ""}, follow=True)
        self.assertContains(sem_motivo, "Informe o motivo")
        self.client.post(reverse("pedido_detalhe", args=[pedido.pk]),
                         {"acao": "cancelar_pedido", "observacao": "Mudou o planejamento"})
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.CANCELADO)
        detalhe = self.client.get(reverse("pedido_detalhe", args=[pedido.pk]))
        self.assertContains(detalhe, "Pedido cancelado")
        self.assertContains(detalhe, "Mudou o planejamento")

    def test_pedido_cancelado_nao_entra_na_fila_de_compras(self):
        from compras.services import itens_para_cotar
        pedido = self.novo_pedido(quantidade="500")  # sem saldo: iria para compra
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        self.assertIn(pedido.pk, {i.pedido_id for i in itens_para_cotar()})
        pedido.refresh_from_db()
        self.cancelar_pedido(pedido, self.aprovador, "Não vamos comprar")
        self.assertNotIn(pedido.pk, {i.pedido_id for i in itens_para_cotar()})


class ConversaDoPedidoTest(BaseFluxoTest):
    def setUp(self):
        super().setUp()
        from .services import enviar_mensagem, papel_na_conversa, pode_participar
        self.enviar, self.papel, self.pode = enviar_mensagem, papel_na_conversa, pode_participar
        PapelUsuario.objects.create(usuario=self.suprimentos, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.comprador = Usuario.objects.create_user("comp", password="x", tenant=self.tenant)
        PapelUsuario.objects.create(usuario=self.comprador, papel=Papel.ADMINISTRADOR, modulo=Modulo.COMPRAS)
        self.estranho = Usuario.objects.create_user("estranho", password="x", tenant=self.tenant)
        self.pedido = self.novo_pedido()
        transicionar(self.pedido, StatusPedido.ENVIADO, self.requisitante)

    def test_participantes_conversam_e_ficam_registrados_em_ordem(self):
        self.enviar(self.pedido, self.requisitante, "Preciso para a feira de ciências")
        self.enviar(self.pedido, self.aprovador, "Pode detalhar a quantidade?")
        self.enviar(self.pedido, self.suprimentos, "Temos em estoque")
        self.enviar(self.pedido, self.comprador, "Sem compra necessária")
        textos = list(self.pedido.mensagens.values_list("texto", flat=True))
        self.assertEqual(textos, [
            "Preciso para a feira de ciências", "Pode detalhar a quantidade?",
            "Temos em estoque", "Sem compra necessária",
        ])

    def test_papel_de_cada_um_na_conversa(self):
        self.assertEqual(self.papel(self.requisitante, self.pedido), "Requisitante")
        self.assertEqual(self.papel(self.aprovador, self.pedido), "Aprovador")
        self.assertEqual(self.papel(self.suprimentos, self.pedido), "Suprimentos")
        self.assertEqual(self.papel(self.comprador, self.pedido), "Compras")
        self.assertEqual(self.papel(self.estranho, self.pedido), "")

    def test_quem_nao_participa_nao_escreve(self):
        self.assertFalse(self.pode(self.estranho, self.pedido))
        with self.assertRaises(SemPermissao):
            self.enviar(self.pedido, self.estranho, "Oi")
        self.assertEqual(self.pedido.mensagens.count(), 0)

    def test_aprovador_de_compra_do_pedido_participa(self):
        from unittest import mock
        with mock.patch("pedidos.services.aprovador_de_compra_do_pedido", return_value=True):
            self.assertTrue(self.pode(self.estranho, self.pedido))

    def test_mensagem_vazia_ou_grande_demais_e_recusada(self):
        for texto in ("", "   \n "):
            with self.assertRaises(TransicaoInvalida):
                self.enviar(self.pedido, self.requisitante, texto)
        with self.assertRaises(TransicaoInvalida):
            self.enviar(self.pedido, self.requisitante, "x" * 1001)
        self.assertEqual(self.pedido.mensagens.count(), 0)

    def test_mensagem_nao_pode_ser_editada_nem_apagada(self):
        msg = self.enviar(self.pedido, self.requisitante, "Original")
        msg.texto = "Alterada"
        with self.assertRaises(ValueError):
            msg.save()
        with self.assertRaises(ValueError):
            msg.delete()
        msg.refresh_from_db()
        self.assertEqual(msg.texto, "Original")

    def test_conversa_pela_tela_do_pedido(self):
        url = reverse("pedido_detalhe", args=[self.pedido.pk])
        self.client.force_login(self.requisitante)
        self.client.post(url, {"acao": "mensagem", "texto": "Urgente para segunda"})
        self.client.force_login(self.aprovador)
        pagina = self.client.get(url)
        self.assertContains(pagina, "Conversa do pedido")
        self.assertContains(pagina, "Urgente para segunda")
        self.assertContains(pagina, "Requisitante")
        self.client.post(url, {"acao": "mensagem", "texto": "Aprovado, pode seguir"})
        self.assertEqual(self.pedido.mensagens.count(), 2)

    def test_estranho_nao_ve_nem_escreve_pela_tela(self):
        url = reverse("pedido_detalhe", args=[self.pedido.pk])
        self.client.force_login(self.estranho)
        self.client.post(url, {"acao": "mensagem", "texto": "Intruso"})
        self.assertEqual(self.pedido.mensagens.count(), 0)
        self.assertRedirects(self.client.get(url), reverse("painel"))

    def test_pedido_cancelado_continua_com_a_conversa_consultavel(self):
        from .services import cancelar_pedido
        self.enviar(self.pedido, self.requisitante, "Pedido por engano")
        cancelar_pedido(self.pedido, self.requisitante, "Engano")
        self.client.force_login(self.requisitante)
        pagina = self.client.get(reverse("pedido_detalhe", args=[self.pedido.pk]))
        self.assertContains(pagina, "Pedido por engano")


class ProdutosSimilaresTest(BaseFluxoTest):
    """Suprimentos vincula produtos similares; sem estoque, sugere a troca; o requisitante decide."""

    def setUp(self):
        super().setUp()
        from cadastros.similares import SimilarInvalido, desvincular, similares_de, vincular
        from .services import (
            SemPermissao as _Sem, candidatos_similares, responder_similar, sugerir_similar,
        )
        self.vincular, self.desvincular, self.similares_de = vincular, desvincular, similares_de
        self.SimilarInvalido = SimilarInvalido
        self.candidatos, self.sugerir, self.responder = candidatos_similares, sugerir_similar, responder_similar
        PapelUsuario.objects.create(usuario=self.suprimentos, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.similar = Produto.objects.create(nome="Argila Branca", especificacao="pacote 1kg", categoria=self.categoria)
        registrar_entrada(
            produto=self.similar, destino=self.destino, quantidade=Decimal("600"),
            valor_unitario=Decimal("12.00"), responsavel=self.suprimentos,
        )
        self.vincular(self.produto, self.similar, self.suprimentos)

    def pedido_sem_estoque(self, quantidade="500"):
        pedido = self.novo_pedido(quantidade=quantidade)  # 50 em estoque: 500 vai para compra
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        pedido.refresh_from_db()
        return pedido, pedido.itens.get()

    # --- vínculo
    def test_vinculo_vale_nos_dois_sentidos_e_nao_duplica(self):
        self.assertEqual(list(self.similares_de(self.produto)), [self.similar])
        self.assertEqual(list(self.similares_de(self.similar)), [self.produto])
        with self.assertRaises(self.SimilarInvalido):
            self.vincular(self.similar, self.produto)  # mesmo par, ordem invertida
        with self.assertRaises(self.SimilarInvalido):
            self.vincular(self.produto, self.produto)

    def test_desvincular_remove_o_par_nos_dois_sentidos(self):
        self.desvincular(self.similar, self.produto)
        self.assertEqual(list(self.similares_de(self.produto)), [])
        self.assertEqual(list(self.similares_de(self.similar)), [])

    def test_similar_inativo_nao_e_oferecido(self):
        self.similar.ativo = False
        self.similar.save()
        self.assertEqual(list(self.similares_de(self.produto)), [])

    # --- candidatos
    def test_candidato_precisa_de_saldo_para_o_pedido_inteiro(self):
        pedido, item = self.pedido_sem_estoque("500")
        self.assertEqual([c["produto"] for c in self.candidatos(item)], [self.similar])
        pedido2, item2 = self.pedido_sem_estoque("700")  # similar só tem 600
        self.assertEqual(self.candidatos(item2), [])

    def test_item_com_estoque_nao_recebe_sugestao(self):
        pedido = self.novo_pedido("10")
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        self.assertEqual(self.candidatos(pedido.itens.get()), [])

    # --- sugestão
    def test_suprimentos_sugere_e_fica_registrado_no_historico_e_na_conversa(self):
        pedido, item = self.pedido_sem_estoque()
        self.sugerir(item, self.similar, self.suprimentos)
        item.refresh_from_db()
        self.assertEqual(item.similar_status, "PENDENTE")
        self.assertEqual(item.similar_sugerido, self.similar)
        self.assertIn("Similar sugerido", pedido.eventos.last().observacao)
        self.assertIn("Argila Branca", pedido.mensagens.get().texto)
        self.assertEqual(pedido.mensagens.get().autor, self.suprimentos)

    def test_so_suprimentos_sugere_e_so_um_similar_vinculado(self):
        pedido, item = self.pedido_sem_estoque()
        with self.assertRaises(SemPermissao):
            self.sugerir(item, self.similar, self.requisitante)
        outro = Produto.objects.create(nome="Massinha", especificacao="500g", categoria=self.categoria)
        with self.assertRaises(TransicaoInvalida):
            self.sugerir(item, outro, self.suprimentos)  # não vinculado

    def test_nao_sugere_duas_vezes_enquanto_pendente(self):
        pedido, item = self.pedido_sem_estoque()
        self.sugerir(item, self.similar, self.suprimentos)
        item.refresh_from_db()
        with self.assertRaises(TransicaoInvalida):
            self.sugerir(item, self.similar, self.suprimentos)

    # --- resposta
    def test_aceitar_troca_o_produto_e_libera_o_pedido_para_separacao(self):
        pedido, item = self.pedido_sem_estoque()
        self.assertEqual(pedido.status, StatusPedido.AGUARDANDO_COMPRA)
        self.sugerir(item, self.similar, self.suprimentos)
        item.refresh_from_db()
        self.responder(item, self.requisitante, True)
        item.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(item.produto, self.similar)
        self.assertEqual(item.produto_original, self.produto)
        self.assertEqual(item.origem_atendimento, OrigemAtendimento.ESTOQUE)
        self.assertEqual(item.similar_status, "ACEITO")
        self.assertEqual(pedido.status, StatusPedido.APROVADO_UNIDADE)

    def test_recusar_mantem_o_item_para_compra(self):
        pedido, item = self.pedido_sem_estoque()
        self.sugerir(item, self.similar, self.suprimentos)
        item.refresh_from_db()
        self.responder(item, self.requisitante, False, "Preciso da marca original")
        item.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(item.similar_status, "RECUSADO")
        self.assertEqual(item.produto, self.produto)
        self.assertEqual(item.origem_atendimento, OrigemAtendimento.COMPRA)
        self.assertEqual(pedido.status, StatusPedido.AGUARDANDO_COMPRA)
        self.assertIn("Preciso da marca original", pedido.mensagens.last().texto)

    def test_so_o_requisitante_responde(self):
        pedido, item = self.pedido_sem_estoque()
        self.sugerir(item, self.similar, self.suprimentos)
        item.refresh_from_db()
        for quem in (self.aprovador, self.suprimentos):
            with self.assertRaises(SemPermissao):
                self.responder(item, quem, True)

    def test_aceitar_sem_sugestao_pendente_e_recusado(self):
        pedido, item = self.pedido_sem_estoque()
        with self.assertRaises(TransicaoInvalida):
            self.responder(item, self.requisitante, True)

    def test_aceitar_bloqueia_se_o_pedido_ja_tem_o_similar(self):
        pedido, item = self.pedido_sem_estoque()
        ItemPedido.objects.create(pedido=pedido, produto=self.similar, quantidade=Decimal("1"))
        self.sugerir(item, self.similar, self.suprimentos)
        item.refresh_from_db()
        with self.assertRaises(TransicaoInvalida):
            self.responder(item, self.requisitante, True)

    def test_aceitar_bloqueia_se_o_saldo_do_similar_acabou(self):
        pedido, item = self.pedido_sem_estoque()
        self.sugerir(item, self.similar, self.suprimentos)
        registrar_saida(produto=self.similar, origem=self.destino, quantidade=Decimal("300"),
                        responsavel=self.suprimentos)
        item.refresh_from_db()
        with self.assertRaises(TransicaoInvalida):
            self.responder(item, self.requisitante, True)

    # --- telas
    def test_fluxo_pela_tela_do_pedido(self):
        pedido, item = self.pedido_sem_estoque()
        url = reverse("pedido_detalhe", args=[pedido.pk])
        self.client.force_login(self.suprimentos)
        self.assertContains(self.client.get(url), "Sugerir produto similar")
        self.client.post(url, {"acao": "sugerir_similar", "item": item.pk, "similar": self.similar.pk})
        self.client.force_login(self.requisitante)
        pagina = self.client.get(url)
        self.assertContains(pagina, "Similar sugerido")
        self.assertContains(pagina, "Aceitar")
        self.client.post(url, {"acao": "responder_similar", "item": item.pk, "resposta": "aceitar"})
        item.refresh_from_db()
        self.assertEqual(item.produto, self.similar)

    def test_vincular_pela_tela_do_produto_so_para_suprimentos(self):
        outro = Produto.objects.create(nome="Massinha", especificacao="500g", categoria=self.categoria)
        url = reverse("produto_similar", args=[self.produto.pk])
        self.client.force_login(self.requisitante)
        self.assertEqual(self.client.post(url, {"outro": outro.pk, "acao": "vincular"}).status_code, 403)
        self.client.force_login(self.suprimentos)
        self.client.post(url, {"outro": outro.pk, "acao": "vincular"})
        self.assertIn(outro, list(self.similares_de(self.produto)))
        self.client.post(url, {"outro": outro.pk, "acao": "desvincular"})
        self.assertNotIn(outro, list(self.similares_de(self.produto)))
        busca = self.client.get(reverse("produto_editar", args=[self.produto.pk]), {"busca_similar": "Massinha"})
        self.assertContains(busca, "Vincular")
