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
            "data_desejada": (timezone.localdate() + timedelta(days=3)).isoformat(),
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


class KanbanArrastavelTest(BaseFluxoTest):
    def setUp(self):
        super().setUp()
        self.est = Usuario.objects.create_user("kan", tenant=self.tenant, password=None)
        PapelUsuario.objects.create(usuario=self.est, papel=Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
        self.client.force_login(self.est)

    def pedido_aprovado(self):
        pedido = self.novo_pedido("4")
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        aprovar_como_lider(pedido, self.aprovador)
        pedido.refresh_from_db()
        return pedido

    def mover(self, pedido, destino):
        return self.client.post(reverse("pedido_mover", args=[pedido.pk]), {"destino": destino})

    def test_arrastar_para_separacao_muda_direto(self):
        pedido = self.pedido_aprovado()
        resposta = self.mover(pedido, "EM_SEPARACAO")
        self.assertTrue(resposta.json()["ok"])
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.EM_SEPARACAO)

    def test_entrega_pede_dados_e_o_sistema_recusa_o_resto(self):
        pedido = self.pedido_aprovado()
        self.assertEqual(self.mover(pedido, "ENTREGUE").status_code, 400)  # antes precisa separar
        self.mover(pedido, "EM_SEPARACAO")
        resposta = self.mover(pedido, "ENTREGUE").json()
        self.assertIn("modal", resposta)  # abre a janela de quantidades
        self.assertContains(self.client.get(resposta["modal"]), "Entregar agora")
        recusa = self.mover(pedido, "AGUARDANDO_COMPRA")
        self.assertEqual(recusa.status_code, 400)
        self.assertIn("sistema", recusa.json()["mensagem"])

    def test_entrega_pela_janela_aceita_centro_e_volta_para_o_kanban(self):
        pedido = self.pedido_aprovado()
        Pedido.objects.filter(pk=pedido.pk).update(centro_custo=None)
        self.mover(pedido, "EM_SEPARACAO")
        item = pedido.itens.get()
        resposta = self.client.post(reverse("pedido_detalhe", args=[pedido.pk]), {
            "acao": "entregar", f"qtd_{item.pk}": "1", "centro_custo": self.centro.pk,
            "next": "/suprimentos/?visao=kanban",
        })
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], "/suprimentos/?visao=kanban")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, StatusPedido.ENTREGA_PARCIAL)  # entregou 1 de 4
        self.assertEqual(pedido.centro_custo, self.centro)
        # redirecionamento só para o próprio site
        self.client.post(reverse("pedido_detalhe", args=[pedido.pk]), {"acao": "entregar", f"qtd_{item.pk}": "1", "next": "https://outro.site/"})

    def test_kanban_traz_a_coluna_entregue(self):
        self.assertContains(self.client.get(reverse("fila_suprimentos"), {"visao": "kanban"}), 'data-status="ENTREGUE"')
