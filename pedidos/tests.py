from decimal import Decimal

from django.contrib.auth import get_user_model

from cadastros.models import Categoria, Produto
from contas.models import ModoAprovacaoSetor, RegraAprovacaoEmpresa, Setor
from estoque.models import DebitoAlmoxarifado, SaldoEstoque
from estoque.services import EstoqueInsuficiente, registrar_entrada, registrar_saida, transferir
from tenancy.models import Almoxarifado, CentroCusto, Filial, MotivoRequisicao
from tenancy.testing import TenantTestCase

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
            produto=self.produto, destino=self.central, quantidade=Decimal("50"),
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
        saldo_central = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.central)
        saldo_destino = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.destino)
        self.assertEqual(saldo_central.quantidade, Decimal("40"))
        self.assertEqual(saldo_destino.quantidade, Decimal("10"))
        self.assertEqual(pedido.eventos.count(), 5)
        self.assertFalse(
            DebitoAlmoxarifado.objects.exists(),
            "Fluxo central → unidade não gera débito",
        )

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
            produto=self.produto, destino=self.central, quantidade=Decimal("50"),
            valor_unitario=Decimal("20.00"), responsavel=self.suprimentos,
        )
        saldo = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.central)
        self.assertEqual(saldo.quantidade, Decimal("100"))
        self.assertEqual(saldo.custo_medio, Decimal("15.00"))

    def test_entrada_valor_zero_preserva_custo(self):
        registrar_entrada(
            produto=self.produto, destino=self.destino, quantidade=Decimal("30"),
            responsavel=self.suprimentos, observacao="Doação lista de material",
        )
        saldo = SaldoEstoque.objects.get(produto=self.produto, almoxarifado=self.destino)
        self.assertEqual(saldo.quantidade, Decimal("30"))
        self.assertEqual(saldo.custo_medio, Decimal("0"))

    def test_saida_sem_saldo_bloqueada(self):
        with self.assertRaises(EstoqueInsuficiente):
            registrar_saida(
                produto=self.produto, origem=self.destino, quantidade=Decimal("1"),
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

    def test_lider_de_outro_setor_nao_aprova(self):
        outro_setor = Setor.objects.create(
            tenant=self.tenant, filial=self.filial, nome="Financeiro"
        )
        outro_setor.lideres.add(self.segundo_lider)
        pedido = self.novo_pedido()
        transicionar(pedido, StatusPedido.ENVIADO, self.requisitante)
        with self.assertRaises(SemPermissao):
            aprovar_como_lider(pedido, self.segundo_lider)
