from django.test import TestCase

# Create your tests here.


from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from cadastros.models import Categoria, Produto
from tenancy.models import Almoxarifado
from tenancy.testing import TenantTestCase

from .models import SaldoEstoque


class PosicaoEstoqueTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        usuario = get_user_model().objects.create_superuser("root", tenant=self.tenant, password=None)
        self.client.force_login(usuario)
        categoria = Categoria.objects.get(nome="Escritório")
        self.central = Almoxarifado.objects.get(central=True)
        self.cp = Almoxarifado.objects.get(filial__sigla="CP", nome="Geral")

        def produto(nome):
            return Produto.objects.create(nome=nome, especificacao="un", categoria=categoria)

        self.papel, self.caneta, self.tinta, self.cola = (
            produto("Papel A4"), produto("Caneta"), produto("Tinta"), produto("Cola"),
        )
        SaldoEstoque.objects.create(  # em dia: 50 > mínimo 10
            produto=self.papel, almoxarifado=self.central, quantidade=Decimal("50"),
            custo_medio=Decimal("20.00"), estoque_minimo=Decimal("10"),
        )
        SaldoEstoque.objects.create(  # zerado e com mínimo: o que mais precisa de reposição
            produto=self.caneta, almoxarifado=self.central, quantidade=Decimal("0"),
            custo_medio=Decimal("2.00"), estoque_minimo=Decimal("30"),
        )
        SaldoEstoque.objects.create(  # abaixo do mínimo
            produto=self.tinta, almoxarifado=self.cp, quantidade=Decimal("3"),
            custo_medio=Decimal("10.00"), estoque_minimo=Decimal("5"),
        )
        SaldoEstoque.objects.create(  # em atenção: no ponto de reposição
            produto=self.cola, almoxarifado=self.cp, quantidade=Decimal("8"),
            custo_medio=Decimal("1.00"), estoque_minimo=Decimal("5"), ponto_reposicao=Decimal("8"),
        )

    def get(self, **params):
        return self.client.get(reverse("posicao_estoque"), params)

    def test_item_zerado_com_minimo_aparece(self):
        resposta = self.get()
        nomes = [s.produto.nome for s in resposta.context["pagina"]]
        self.assertIn("Caneta", nomes)  # antes, quantidade__gt=0 escondia justamente esse

    def test_totais(self):
        kpis = self.get().context["kpis"]
        self.assertEqual(kpis["itens"], 3)  # Caneta está zerada
        self.assertEqual(kpis["abaixo"], 2)  # Caneta (sem estoque) + Tinta
        self.assertEqual(kpis["atencao"], 1)
        self.assertEqual(kpis["valor"], Decimal("1038.00"))  # 50×20 + 0×2 + 3×10 + 8×1

    def test_filtro_por_situacao_e_por_almoxarifado(self):
        so_abaixo = self.get(situacao="abaixo").context["pagina"]
        self.assertEqual([s.produto.nome for s in so_abaixo], ["Tinta"])
        do_cp = self.get(almoxarifado=self.cp.pk).context["pagina"]
        self.assertEqual({s.produto.nome for s in do_cp}, {"Tinta", "Cola"})

    def test_busca_por_nome(self):
        resultado = self.get(q="papel").context["pagina"]
        self.assertEqual([s.produto.nome for s in resultado], ["Papel A4"])
