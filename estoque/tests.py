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


class EstoqueDoAlmoxarifadoTest(TenantTestCase):
    def test_pagina_do_almoxarifado_mostra_o_nome_dele(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from tenancy.models import Almoxarifado

        admin = get_user_model().objects.create_superuser("root_alm", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        alm = Almoxarifado.objects.get(central=True)
        self.assertContains(self.client.get(reverse("config_almoxarifado_lista")), f"almoxarifado={alm.pk}")
        self.assertContains(self.client.get(reverse("posicao_estoque"), {"almoxarifado": alm.pk}), f"Estoque — {alm.nome}")


class FiltrosDependentesDeEstoqueTest(TenantTestCase):
    def test_almoxarifado_de_outra_unidade_e_descartado_e_planilha_traz_unidades(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from tenancy.models import Almoxarifado, Filial

        admin = get_user_model().objects.create_superuser("root_fd", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        cp, vc = Filial.objects.get(sigla="CP"), Filial.objects.get(sigla="VC")
        do_vc = Almoxarifado.objects.filter(filial=vc).first()
        resposta = self.client.get(reverse("posicao_estoque"), {"filial": cp.pk, "almoxarifado": do_vc.pk})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["almoxarifado_sel"], None)  # não é da unidade escolhida
        pagina = self.client.get(reverse("entrada_planilha"))
        self.assertContains(pagina, 'id="e-unidade"')
        self.assertContains(pagina, f'data-filial="{do_vc.filial_id}"')


class EntradaDeMaterialTelaTest(TenantTestCase):
    def test_modelo_baixa_e_previa_mostra_totais(self):
        import io

        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from openpyxl import Workbook

        from cadastros.models import Categoria, Produto
        from tenancy.models import Almoxarifado

        admin = get_user_model().objects.create_superuser("root_ent", tenant=self.tenant, password=None)
        self.client.force_login(admin)
        produto = Produto.objects.create(
            codigo="TST-001", nome="Papel A4", especificacao="resma", categoria=Categoria.objects.first(),
        )
        modelo = self.client.get(reverse("entrada_modelo"))
        self.assertEqual(modelo.status_code, 200)
        self.assertIn("spreadsheetml", modelo["Content-Type"])

        wb = Workbook()
        wb.active.append(["Código", "Quantidade", "Valor unitário"])
        wb.active.append(["TST-001", 10, 2.5])
        buf = io.BytesIO()
        wb.save(buf)
        alm = Almoxarifado.objects.get(central=True)
        self.client.post(reverse("entrada_planilha"), {
            "arquivo": SimpleUploadedFile("e.xlsx", buf.getvalue()), "almoxarifado": alm.pk, "observacao": "teste",
        })
        pagina = self.client.get(reverse("entrada_planilha"))
        self.assertContains(pagina, "Conferir a prévia")
        self.assertEqual(pagina.context["total_quantidade"], 10)
        self.assertEqual(float(pagina.context["total_valor"]), 25.0)
