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


# ============================================================ estoque robusto: movimentação, limites, balanço
from django.contrib.auth import get_user_model as _gum  # noqa: E402

from . import services  # noqa: E402
from .models import Balanco, Movimentacao, StatusBalanco, TipoMovimentacao  # noqa: E402
from .services import EstoqueInsuficiente, MovimentacaoInvalida  # noqa: E402


class EstoqueBase(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.usuario = _gum().objects.create_superuser("rootop", tenant=self.tenant, password=None)
        self.client.force_login(self.usuario)
        categoria = Categoria.objects.get(nome="Escritório")
        self.central = Almoxarifado.objects.get(central=True)
        self.cp = Almoxarifado.objects.get(filial__sigla="CP", nome="Geral")
        self.papel = Produto.objects.create(nome="Papel A4", especificacao="75g", categoria=categoria)
        self.caneta = Produto.objects.create(nome="Caneta", especificacao="azul", categoria=categoria)

    def saldo(self, produto, almox):
        return SaldoEstoque.objects.get(produto=produto, almoxarifado=almox).quantidade

    def entrada(self, produto, almox, qtd, valor="0"):
        return services.registrar_entrada(produto=produto, destino=almox, quantidade=Decimal(qtd),
                                          valor_unitario=Decimal(valor), responsavel=self.usuario)


class ServicosDeEstoqueTest(EstoqueBase):
    def test_quantidade_precisa_ser_positiva(self):
        for qtd in ("0", "-3"):
            with self.assertRaises(MovimentacaoInvalida):
                self.entrada(self.papel, self.central, qtd)
        with self.assertRaises(MovimentacaoInvalida):
            services.registrar_saida(produto=self.papel, origem=self.central, quantidade=Decimal("0"), responsavel=self.usuario)
        self.assertFalse(SaldoEstoque.objects.filter(produto=self.papel).exists())

    def test_valor_unitario_negativo_e_recusado(self):
        with self.assertRaises(MovimentacaoInvalida):
            self.entrada(self.papel, self.central, "5", "-1")

    def test_custo_medio_pondera_as_entradas(self):
        self.entrada(self.papel, self.central, "10", "2.00")
        self.entrada(self.papel, self.central, "30", "4.00")
        s = SaldoEstoque.objects.get(produto=self.papel, almoxarifado=self.central)
        self.assertEqual(s.quantidade, Decimal("40"))
        self.assertEqual(s.custo_medio, Decimal("3.50"))

    def test_saida_acima_do_saldo_nao_altera_nada(self):
        self.entrada(self.papel, self.central, "5")
        with self.assertRaises(EstoqueInsuficiente):
            services.registrar_saida(produto=self.papel, origem=self.central, quantidade=Decimal("6"), responsavel=self.usuario)
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("5"))
        self.assertEqual(Movimentacao.objects.filter(tipo="SAIDA").count(), 0)

    def test_transferencia_para_o_mesmo_almoxarifado_e_recusada(self):
        self.entrada(self.papel, self.central, "5")
        with self.assertRaises(MovimentacaoInvalida):
            services.transferir(produto=self.papel, origem=self.central, destino=self.central,
                                quantidade=Decimal("1"), responsavel=self.usuario)

    def test_ajuste_para_cima_para_baixo_e_sem_mudanca(self):
        self.entrada(self.papel, self.cp, "10", "2.00")
        m = services.registrar_ajuste(produto=self.papel, almoxarifado=self.cp, nova_quantidade=Decimal("14"), responsavel=self.usuario)
        self.assertEqual((m.tipo, m.quantidade, m.destino, m.origem), (TipoMovimentacao.AJUSTE, Decimal("4"), self.cp, None))
        m = services.registrar_ajuste(produto=self.papel, almoxarifado=self.cp, nova_quantidade=Decimal("9"), responsavel=self.usuario)
        self.assertEqual((m.quantidade, m.origem, m.destino), (Decimal("5"), self.cp, None))
        self.assertIsNone(services.registrar_ajuste(produto=self.papel, almoxarifado=self.cp, nova_quantidade=Decimal("9"), responsavel=self.usuario))
        self.assertEqual(self.saldo(self.papel, self.cp), Decimal("9"))
        with self.assertRaises(MovimentacaoInvalida):
            services.registrar_ajuste(produto=self.papel, almoxarifado=self.cp, nova_quantidade=Decimal("-1"), responsavel=self.usuario)

    def test_limites_exigem_ordem_e_nao_negativos(self):
        d = dict(produto=self.papel, almoxarifado=self.central)
        s = services.definir_limites(**d, minimo=Decimal("10"), reposicao=Decimal("20"), maximo=Decimal("50"))
        self.assertEqual((s.estoque_minimo, s.ponto_reposicao, s.estoque_maximo), (Decimal("10"), Decimal("20"), Decimal("50")))
        for ruim in (dict(minimo=Decimal("30"), maximo=Decimal("20")), dict(minimo=Decimal("10"), reposicao=Decimal("5")),
                     dict(reposicao=Decimal("60"), maximo=Decimal("50")), dict(minimo=Decimal("-1"))):
            with self.assertRaises(MovimentacaoInvalida):
                services.definir_limites(**d, **ruim)
        s.refresh_from_db()
        self.assertEqual(s.estoque_maximo, Decimal("50"))  # recusas não gravam nada
        s = services.definir_limites(**d, minimo=None, reposicao=None, maximo=None)  # None limpa
        self.assertIsNone(s.estoque_minimo)

    def test_situacao_e_sugestao_de_reposicao(self):
        self.entrada(self.papel, self.central, "15")
        s = services.definir_limites(produto=self.papel, almoxarifado=self.central,
                                     minimo=Decimal("10"), reposicao=Decimal("20"), maximo=Decimal("60"))
        self.assertTrue(s.precisa_repor)  # 15 <= reposição 20
        self.assertFalse(s.abaixo_do_minimo)
        self.assertEqual(s.sugestao_reposicao, Decimal("45"))  # até o máximo
        self.entrada(self.papel, self.central, "10")  # 25: em dia
        s.refresh_from_db()
        self.assertFalse(s.precisa_repor)
        self.assertEqual(s.sugestao_reposicao, Decimal("0"))
        self.entrada(self.papel, self.central, "50")  # 75: acima do máximo
        s.refresh_from_db()
        self.assertTrue(s.acima_do_maximo)

    def test_sugestao_sem_maximo_leva_ao_ponto_de_reposicao(self):
        self.entrada(self.papel, self.central, "4")
        s = services.definir_limites(produto=self.papel, almoxarifado=self.central, minimo=Decimal("10"))
        self.assertTrue(s.abaixo_do_minimo)
        self.assertEqual(s.sugestao_reposicao, Decimal("6"))


class BalancoDeEstoqueTest(EstoqueBase):
    def setUp(self):
        super().setUp()
        self.entrada(self.papel, self.cp, "10", "2.00")
        self.entrada(self.caneta, self.cp, "20", "1.00")

    def abrir(self):
        return services.abrir_balanco(almoxarifado=self.cp, responsavel=self.usuario)

    def test_abrir_congela_o_saldo_e_nao_duplica(self):
        b = self.abrir()
        self.assertEqual({i.produto_id: i.quantidade_sistema for i in b.itens.all()},
                         {self.papel.pk: Decimal("10"), self.caneta.pk: Decimal("20")})
        with self.assertRaises(MovimentacaoInvalida):
            self.abrir()
        with self.assertRaises(MovimentacaoInvalida):
            services.abrir_balanco(almoxarifado=self.central, responsavel=self.usuario)  # nada para contar

    def test_aplicar_ajusta_so_o_contado_e_registra_no_extrato(self):
        b = self.abrir()
        itens = {i.produto_id: i for i in b.itens.all()}
        services.registrar_contagem(b, {itens[self.papel.pk].pk: Decimal("8"), itens[self.caneta.pk].pk: None})
        self.assertEqual(self.saldo(self.papel, self.cp), Decimal("10"))  # contar não mexe no saldo
        self.assertEqual(services.aplicar_balanco(b, self.usuario), 1)
        self.assertEqual(self.saldo(self.papel, self.cp), Decimal("8"))
        self.assertEqual(self.saldo(self.caneta, self.cp), Decimal("20"))  # sem contagem: intacto
        b.refresh_from_db()
        self.assertEqual(b.status, StatusBalanco.APLICADO)
        aj = Movimentacao.objects.get(tipo=TipoMovimentacao.AJUSTE)
        self.assertEqual((aj.quantidade, aj.origem, aj.observacao), (Decimal("2"), self.cp, f"Balanço #{b.pk}"))
        with self.assertRaises(MovimentacaoInvalida):
            services.aplicar_balanco(b, self.usuario)  # não aplica duas vezes
        with self.assertRaises(MovimentacaoInvalida):
            services.registrar_contagem(b, {itens[self.papel.pk].pk: Decimal("1")})

    def test_movimento_durante_a_contagem_nao_e_dobrado(self):
        b = self.abrir()
        self.entrada(self.papel, self.cp, "5")  # chega material com a contagem aberta (saldo 15)
        item = b.itens.get(produto=self.papel)
        services.registrar_contagem(b, {item.pk: Decimal("15")})  # a contagem física já inclui as 5
        self.assertEqual(services.aplicar_balanco(b, self.usuario), 0)  # nada a ajustar
        self.assertEqual(self.saldo(self.papel, self.cp), Decimal("15"))
        item.refresh_from_db()
        self.assertEqual(item.diferenca_aplicada, Decimal("0"))

    def test_contagem_igual_ao_sistema_nao_gera_ajuste(self):
        b = self.abrir()
        services.registrar_contagem(b, {i.pk: i.quantidade_sistema for i in b.itens.all()})
        self.assertEqual(services.aplicar_balanco(b, self.usuario), 0)
        self.assertFalse(Movimentacao.objects.filter(tipo=TipoMovimentacao.AJUSTE).exists())

    def test_aplicar_sem_contagem_e_contagem_negativa_sao_recusados(self):
        b = self.abrir()
        with self.assertRaises(MovimentacaoInvalida):
            services.aplicar_balanco(b, self.usuario)
        with self.assertRaises(MovimentacaoInvalida):
            services.registrar_contagem(b, {b.itens.first().pk: Decimal("-1")})

    def test_cancelar_nao_altera_saldo_e_libera_novo_balanco(self):
        b = self.abrir()
        services.registrar_contagem(b, {b.itens.first().pk: Decimal("1")})
        services.cancelar_balanco(b, self.usuario)
        self.assertEqual(self.saldo(self.papel, self.cp), Decimal("10"))
        self.assertEqual(Balanco.objects.get(pk=b.pk).status, StatusBalanco.CANCELADO)
        self.abrir()  # agora pode


class TelasDeEstoqueTest(EstoqueBase):
    def post_mov(self, **extra):
        dados = {"tipo": "ENTRADA", "almoxarifado": self.central.pk, "observacao": "NF 1",
                 "produto": [self.papel.pk, self.caneta.pk], "quantidade": ["10", "4,5"], "valor": ["2", ""]}
        dados.update(extra)
        return self.client.post(reverse("estoque_movimentar"), dados)

    def test_todas_as_telas_abrem(self):
        for nome in ("estoque_movimentar", "estoque_extrato", "estoque_limites", "estoque_reposicao",
                     "estoque_balanco_lista", "posicao_estoque", "entrada_planilha"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)

    def test_entrada_manual_com_varias_linhas(self):
        r = self.post_mov()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("10"))
        self.assertEqual(self.saldo(self.caneta, self.central), Decimal("4.5"))
        self.assertEqual(Movimentacao.objects.filter(tipo="ENTRADA").count(), 2)

    def test_saida_exige_motivo_e_saldo_e_e_tudo_ou_nada(self):
        self.post_mov()
        r = self.post_mov(tipo="SAIDA", observacao="")
        self.assertEqual(r.status_code, 200)  # sem motivo
        r = self.post_mov(tipo="SAIDA", observacao="quebra", quantidade=["3", "99"])
        self.assertEqual(r.status_code, 200)  # a 2ª linha excede o saldo -> nada é baixado
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("10"))
        self.assertEqual(Movimentacao.objects.filter(tipo="SAIDA").count(), 0)
        r = self.post_mov(tipo="SAIDA", observacao="quebra", quantidade=["3", "1"])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("7"))

    def test_transferencia_manual(self):
        self.post_mov()
        r = self.post_mov(tipo="TRANSFERENCIA", destino=self.cp.pk, observacao="reposição da unidade", quantidade=["4", "1"])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.saldo(self.papel, self.cp), Decimal("4"))
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("6"))
        r = self.post_mov(tipo="TRANSFERENCIA", destino=self.central.pk, observacao="x")  # mesmo almoxarifado
        self.assertEqual(r.status_code, 200)

    def test_quantidade_invalida_nao_grava(self):
        for qtd in (["0", "1"], ["abc", "1"], ["-2", "1"]):
            self.assertEqual(self.post_mov(quantidade=qtd).status_code, 200)
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_extrato_filtra_por_almoxarifado_tipo_e_produto(self):
        self.post_mov()
        self.post_mov(tipo="SAIDA", observacao="perda", quantidade=["1", "1"])
        r = self.client.get(reverse("estoque_extrato"), {"tipo": "SAIDA"})
        self.assertEqual(len(r.context["pagina"].object_list), 2)
        r = self.client.get(reverse("estoque_extrato"), {"q": "Caneta", "almoxarifado": self.cp.pk})
        self.assertEqual(len(r.context["pagina"].object_list), 0)
        r = self.client.get(reverse("estoque_extrato"), {"de": "lixo"})  # data inválida não quebra
        self.assertEqual(r.status_code, 200)

    def test_limites_salvam_e_recusam_tudo_se_algum_for_invalido(self):
        self.post_mov()
        url = reverse("estoque_limites")
        r = self.client.post(url, {"almoxarifado": self.central.pk, "produto": [self.papel.pk, self.caneta.pk],
                                   "minimo": ["5", "9"], "reposicao": ["8", "2"], "maximo": ["20", ""]})
        self.assertEqual(r.status_code, 200)  # caneta: reposição 2 < mínimo 9
        self.assertIsNone(SaldoEstoque.objects.get(produto=self.papel, almoxarifado=self.central).estoque_minimo)
        r = self.client.post(url, {"almoxarifado": self.central.pk, "produto": [self.papel.pk], "minimo": ["5"],
                                   "reposicao": ["8"], "maximo": ["20"]})
        self.assertEqual(r.status_code, 302)
        s = SaldoEstoque.objects.get(produto=self.papel, almoxarifado=self.central)
        self.assertEqual((s.estoque_minimo, s.ponto_reposicao, s.estoque_maximo), (Decimal("5"), Decimal("8"), Decimal("20")))

    def test_limites_incluem_produto_sem_saldo(self):
        novo = Produto.objects.create(nome="Régua", especificacao="30cm", categoria=self.papel.categoria)
        self.client.post(reverse("estoque_limites"), {"almoxarifado": self.cp.pk, "novo_produto": novo.pk,
                                                      "novo_minimo": "3", "novo_maximo": "10"})
        s = SaldoEstoque.objects.get(produto=novo, almoxarifado=self.cp)
        self.assertEqual((s.quantidade, s.estoque_minimo, s.estoque_maximo), (Decimal("0"), Decimal("3"), Decimal("10")))

    def test_reposicao_lista_so_o_que_precisa_e_sugere_ate_o_maximo(self):
        self.post_mov()
        services.definir_limites(produto=self.papel, almoxarifado=self.central, minimo=Decimal("20"), maximo=Decimal("50"))
        services.definir_limites(produto=self.caneta, almoxarifado=self.central, minimo=Decimal("1"), maximo=Decimal("2"))
        r = self.client.get(reverse("estoque_reposicao"))
        self.assertEqual([s.produto_id for s in r.context["linhas"]], [self.papel.pk])
        self.assertEqual(r.context["linhas"][0].sugestao_reposicao, Decimal("40"))
        self.assertEqual([s.produto_id for s in r.context["excesso"]], [self.caneta.pk])  # 4,5 > máximo 2

    def test_fluxo_de_balanco_pela_tela(self):
        self.post_mov()
        r = self.client.post(reverse("estoque_balanco_lista"), {"almoxarifado": self.central.pk})
        self.assertEqual(r.status_code, 302)
        b = Balanco.objects.get()
        itens = {i.produto_id: i for i in b.itens.all()}
        url = reverse("estoque_balanco_detalhe", args=[b.pk])
        self.assertEqual(self.client.post(url, {"acao": "salvar", f"contagem_{itens[self.papel.pk].pk}": "7,5"}).status_code, 302)
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("10"))
        self.client.post(url, {"acao": "aplicar", f"contagem_{itens[self.papel.pk].pk}": "7,5"})
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("7.5"))
        self.assertContains(self.client.get(url), "Aplicado")
        # encerrado: novo POST não altera nada
        self.client.post(url, {"acao": "aplicar", f"contagem_{itens[self.papel.pk].pk}": "1"})
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("7.5"))

    def test_contagem_com_texto_invalido_nao_aplica(self):
        self.post_mov()
        self.client.post(reverse("estoque_balanco_lista"), {"almoxarifado": self.central.pk})
        b = Balanco.objects.get()
        item = b.itens.get(produto=self.papel)
        self.client.post(reverse("estoque_balanco_detalhe", args=[b.pk]), {"acao": "aplicar", f"contagem_{item.pk}": "abc"})
        self.assertEqual(self.saldo(self.papel, self.central), Decimal("10"))
        self.assertEqual(Balanco.objects.get().status, StatusBalanco.EM_CONTAGEM)

    def test_quem_nao_e_do_estoque_nao_acessa(self):
        comum = _gum().objects.create_user("solic", tenant=self.tenant, password="x")
        self.client.force_login(comum)
        for nome in ("estoque_movimentar", "estoque_extrato", "estoque_limites", "estoque_reposicao", "estoque_balanco_lista"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)
        self.assertEqual(self.client.post(reverse("estoque_movimentar"), {}).status_code, 403)


# ============================================================ entrada por planilha com almoxarifado por linha
class EntradaComAlmoxarifadoNaPlanilhaTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(_gum().objects.create_superuser("root_epa", tenant=self.tenant, password=None))
        categoria = Categoria.objects.first()
        self.papel = Produto.objects.create(codigo="EPA-1", nome="Papel", especificacao="resma", categoria=categoria)
        self.caneta = Produto.objects.create(codigo="EPA-2", nome="Caneta", especificacao="un", categoria=categoria)
        self.central = Almoxarifado.objects.get(central=True)
        self.cp_geral = Almoxarifado.objects.get(filial__sigla="CP", nome="Geral")
        self.vc_geral = Almoxarifado.objects.get(filial__sigla="VC", nome="Geral")

    def planilha(self, *linhas, cabecalho=("Código", "Almoxarifado", "Quantidade", "Valor unitário")):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook

        wb = Workbook()
        wb.active.append(list(cabecalho))
        for linha in linhas:
            wb.active.append(list(linha))
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile("entrada.xlsx", buf.getvalue())

    def enviar(self, arquivo, padrao=""):
        self.client.post(reverse("entrada_planilha"), {"arquivo": arquivo, "almoxarifado": padrao, "observacao": ""})
        return self.client.get(reverse("entrada_planilha"))

    def saldo(self, produto, almoxarifado):
        s = SaldoEstoque.objects.filter(produto=produto, almoxarifado=almoxarifado).first()
        return s.quantidade if s else Decimal("0")

    def test_modelo_traz_coluna_aba_e_lista_suspensa(self):
        import io

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(self.client.get(reverse("entrada_modelo")).content))
        entrada = wb["Entrada"]
        self.assertIn("Almoxarifado", [c.value for c in entrada[1]])
        valores = [r[0] for r in wb["Almoxarifados"].iter_rows(min_row=2, values_only=True)]
        self.assertIn("CP — Geral", valores)
        self.assertIn("VC — Geral", valores)
        self.assertEqual(len(entrada.data_validations.dataValidation), 1)

    def test_cada_linha_vai_para_o_seu_almoxarifado_e_vazio_usa_o_padrao(self):
        arquivo = self.planilha(
            ("EPA-1", "CP — Geral", 10, 2),
            ("EPA-1", "vc - geral", 4, 2),  # sem acento/traço exato também vale
            ("EPA-2", "", 7, 1),  # vazio: almoxarifado padrão
        )
        pagina = self.enviar(arquivo, padrao=self.central.pk)
        self.assertEqual(pagina.context["previa"]["erros"], [])
        self.assertEqual(len(pagina.context["destinos"]), 3)
        self.assertContains(pagina, "Resumo por almoxarifado")

        self.client.post(reverse("entrada_planilha"), {"acao": "aplicar"})
        self.assertEqual(self.saldo(self.papel, self.cp_geral), Decimal("10"))
        self.assertEqual(self.saldo(self.papel, self.vc_geral), Decimal("4"))
        self.assertEqual(self.saldo(self.caneta, self.central), Decimal("7"))

    def test_almoxarifado_desconhecido_ou_ausente_sem_padrao_vira_erro(self):
        arquivo = self.planilha(("EPA-1", "XX — Inexistente", 5, 1), ("EPA-2", "", 3, 1), ("EPA-1", "CP — Geral", 2, 1))
        previa = self.enviar(arquivo).context["previa"]
        self.assertEqual(len(previa["linhas"]), 1)
        self.assertEqual(previa["linhas"][0]["almoxarifado_id"], self.cp_geral.pk)
        self.assertEqual(len(previa["erros"]), 2)
        self.assertIn("XX — Inexistente", previa["erros"][0])
        self.assertIn("almoxarifado padrão", previa["erros"][1])

    def test_nome_repetido_entre_unidades_exige_a_sigla(self):
        previa = self.enviar(self.planilha(("EPA-1", "Geral", 5, 1))).context["previa"]
        self.assertEqual(previa["linhas"], [])  # "Geral" existe em várias unidades
        self.assertEqual(len(previa["erros"]), 1)

    def test_planilha_antiga_sem_coluna_usa_o_padrao(self):
        arquivo = self.planilha(("EPA-1", 6, 3), cabecalho=("Código", "Quantidade", "Valor unitário"))
        self.enviar(arquivo, padrao=self.cp_geral.pk)
        self.client.post(reverse("entrada_planilha"), {"acao": "aplicar"})
        self.assertEqual(self.saldo(self.papel, self.cp_geral), Decimal("6"))
