from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from contas.models import Alcada, PapelAlcada, VinculoAprovacao
from estoque.models import SaldoEstoque
from tenancy.models import Almoxarifado, Filial
from tenancy.testing import TenantTestCase

from .models import Categoria, Produto

Usuario = get_user_model()


class SeedDadosIniciaisTest(TenantTestCase):
    def test_filiais_criadas(self):
        self.assertEqual(Filial.objects.count(), 5)
        self.assertTrue(Filial.objects.filter(sigla="CSC").exists())

    def test_almoxarifados_criados_com_um_central(self):
        self.assertEqual(Almoxarifado.objects.count(), 22)
        self.assertEqual(Almoxarifado.objects.filter(central=True).count(), 1)

    def test_alcadas_seed(self):
        self.assertEqual(Alcada.objects.count(), 4)


class AlcadaParaValorTest(TenantTestCase):
    def test_faixa_automatica(self):
        alcada = Alcada.para_valor(Decimal("299.99"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.AUTOMATICA)

    def test_limite_exato_300(self):
        alcada = Alcada.para_valor(Decimal("300.00"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.AUTOMATICA)

    def test_faixa_diretoria(self):
        alcada = Alcada.para_valor(Decimal("300.01"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.DIRETORIA_UNIDADE)

    def test_faixa_cfo(self):
        alcada = Alcada.para_valor(Decimal("20000.00"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.CFO)

    def test_faixa_ceo_sem_teto(self):
        alcada = Alcada.para_valor(Decimal("1000000.00"))
        self.assertEqual(alcada.papel_aprovador, PapelAlcada.CEO)


class ConstraintsTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.categoria = Categoria.objects.get(nome="Escritório")
        self.produto = Produto.objects.create(
            nome="Papel A4", especificacao="75g branco — resma", categoria=self.categoria
        )
        self.central = Almoxarifado.objects.get(central=True)

    def test_produto_duplicado_rejeitado(self):
        with self.assertRaises(IntegrityError):
            Produto.objects.create(
                nome="Papel A4", especificacao="75g branco — resma", categoria=self.categoria
            )

    def test_segundo_almoxarifado_central_rejeitado(self):
        filial = Filial.objects.get(sigla="CP")
        with self.assertRaises(IntegrityError):
            Almoxarifado.objects.create(
                tenant=self.tenant, filial=filial, nome="Outro Central", central=True
            )

    def test_saldo_negativo_rejeitado(self):
        with self.assertRaises(IntegrityError):
            SaldoEstoque.objects.create(
                produto=self.produto, almoxarifado=self.central, quantidade=Decimal("-1")
            )

    def test_autoaprovacao_rejeitada(self):
        usuario = Usuario.objects.create_user(
            username="karlysson", password="senha-forte-123", tenant=self.tenant
        )
        with self.assertRaises(IntegrityError):
            VinculoAprovacao.objects.create(requisitante=usuario, aprovador=usuario)

    def test_vinculo_com_ordem(self):
        requisitante = Usuario.objects.create_user(
            username="req", password="senha-forte-123", tenant=self.tenant
        )
        aprovador1 = Usuario.objects.create_user(
            username="ap1", password="senha-forte-123", tenant=self.tenant
        )
        aprovador2 = Usuario.objects.create_user(
            username="ap2", password="senha-forte-123", tenant=self.tenant
        )
        VinculoAprovacao.objects.create(requisitante=requisitante, aprovador=aprovador1, ordem=1)
        VinculoAprovacao.objects.create(requisitante=requisitante, aprovador=aprovador2, ordem=2)
        vinculos = requisitante.vinculos_como_requisitante.order_by("ordem")
        self.assertEqual([v.aprovador.username for v in vinculos], ["ap1", "ap2"])
