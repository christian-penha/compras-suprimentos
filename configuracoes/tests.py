from django.contrib.auth import get_user_model
from django.http import QueryDict

from contas.models import ModoAprovacaoSetor, Setor
from tenancy.models import Filial
from tenancy.testing import TenantTestCase

from .forms import AlmoxarifadoForm, CentroCustoForm, SetorForm, UsuarioForm

Usuario = get_user_model()


class SetorFormTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.filial = Filial.objects.get(sigla="CP")
        self.ana = Usuario.objects.create_user("ana", password="x", tenant=self.tenant)
        self.bia = Usuario.objects.create_user("bia", password="x", tenant=self.tenant)
        self.caio = Usuario.objects.create_user("caio", password="x", tenant=self.tenant)

    def salvar(self, instancia=None, **dados):
        base = {"filial": self.filial.pk, "nome": "Financeiro", "ativo": "on", "modo_aprovacao": ""}
        base.update(dados)
        dados = QueryDict(mutable=True)
        for chave, valor in base.items():
            dados.setlist(chave, valor if isinstance(valor, list) else [valor])
        form = SetorForm(dados, instance=instancia)
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.tenant = self.tenant
        return form.save()

    def test_marca_membros_e_lideres_na_tela_do_setor(self):
        setor = self.salvar(membros=[self.ana.pk, self.bia.pk], lideres=[self.ana.pk])
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.setor, setor)
        self.assertEqual(list(setor.lideres.all()), [self.ana])
        self.assertEqual(setor.membros.count(), 2)

    def test_desmarcar_remove_do_setor_e_marcar_move_de_outro(self):
        setor = self.salvar(membros=[self.ana.pk, self.bia.pk])
        outro = Setor.objects.create(tenant=self.tenant, filial=self.filial, nome="TI")
        self.caio.setor = outro
        self.caio.save()

        self.salvar(instancia=setor, membros=[self.bia.pk, self.caio.pk])
        for u in (self.ana, self.bia, self.caio):
            u.refresh_from_db()
        self.assertIsNone(self.ana.setor)
        self.assertEqual(self.bia.setor, setor)
        self.assertEqual(self.caio.setor, setor)

    def test_regra_do_setor_e_salva(self):
        setor = self.salvar(modo_aprovacao=ModoAprovacaoSetor.TODOS_LIDERES)
        self.assertEqual(setor.modo_aprovacao, ModoAprovacaoSetor.TODOS_LIDERES)


class UsuarioFormEmailTest(TenantTestCase):
    def dados(self, **extra):
        base = {"username": "novo", "email": "ana@colegioperfil.com.br", "senha": "senha-forte-123", "ativo_no_sistema": "on"}
        base.update(extra)
        return base

    def test_email_repetido_e_recusado_sem_diferenciar_maiusculas(self):
        Usuario.objects.create_user("ana", email="ana@colegioperfil.com.br", password="x", tenant=self.tenant)
        form = UsuarioForm(self.dados(email="ANA@ColegioPerfil.com.br"))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_editar_o_proprio_usuario_mantem_o_email(self):
        ana = Usuario.objects.create_user("ana", email="ana@colegioperfil.com.br", password="x", tenant=self.tenant)
        form = UsuarioForm(self.dados(username="ana"), instance=ana)
        form.is_valid()
        self.assertNotIn("email", form.errors)

    def test_email_em_branco_continua_permitido(self):
        form = UsuarioForm(self.dados(email=""))
        form.is_valid()
        self.assertNotIn("email", form.errors)


class AlmoxarifadoCrudTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        from contas.models import Papel, PapelUsuario
        from django.urls import reverse
        self.reverse = reverse
        self.filial = Filial.objects.get(sigla="CP")
        self.admin = Usuario.objects.create_user("adm", password="x", tenant=self.tenant)
        PapelUsuario.objects.create(usuario=self.admin, papel=Papel.SUPERADMIN)
        self.client.force_login(self.admin)

    def dados(self, **extra):
        base = {"filial": self.filial.pk, "nome": "Biblioteca", "ativo": "on"}
        base.update(extra)
        return base

    def test_cria_lista_e_edita(self):
        from tenancy.models import Almoxarifado
        r = self.client.post(self.reverse("config_almoxarifado_novo"), self.dados())
        self.assertEqual(r.status_code, 302)
        a = Almoxarifado.objects.get(filial=self.filial, nome="Biblioteca")
        self.assertEqual(a.tenant, self.tenant)
        self.assertContains(self.client.get(self.reverse("config_almoxarifado_lista")), "Biblioteca")
        self.client.post(self.reverse("config_almoxarifado_editar", args=[a.pk]), self.dados(nome="Biblioteca 2"))
        a.refresh_from_db()
        self.assertEqual(a.nome, "Biblioteca 2")

    def test_nome_repetido_na_mesma_filial_e_recusado(self):
        from tenancy.models import Almoxarifado
        Almoxarifado.objects.create(tenant=self.tenant, filial=self.filial, nome="Biblioteca")
        form = AlmoxarifadoForm(self.dados())
        self.assertFalse(form.is_valid())

    def test_so_pode_haver_um_central(self):
        form = AlmoxarifadoForm(self.dados(nome="Outro central", central="on"))
        self.assertFalse(form.is_valid())
        self.assertIn("central", form.errors)

    def test_central_nao_pode_deixar_de_ser_central_nem_ser_desativado_nem_excluido(self):
        from tenancy.models import Almoxarifado
        central = Almoxarifado.objects.get(central=True)
        form = AlmoxarifadoForm(
            {"filial": central.filial_id, "nome": central.nome, "ativo": "on"}, instance=central
        )
        self.assertFalse(form.is_valid())
        form = AlmoxarifadoForm(
            {"filial": central.filial_id, "nome": central.nome, "central": "on"}, instance=central
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ativo", form.errors)
        self.client.post(self.reverse("config_almoxarifado_excluir", args=[central.pk]))
        self.assertTrue(Almoxarifado.objects.filter(pk=central.pk).exists())

    def test_exclui_vazio_e_protege_o_que_tem_saldo(self):
        from tenancy.models import Almoxarifado
        from cadastros.models import Produto
        from estoque.models import SaldoEstoque
        vazio = Almoxarifado.objects.create(tenant=self.tenant, filial=self.filial, nome="Vazio")
        self.client.post(self.reverse("config_almoxarifado_excluir", args=[vazio.pk]))
        self.assertFalse(Almoxarifado.objects.filter(pk=vazio.pk).exists())

        usado = Almoxarifado.objects.create(tenant=self.tenant, filial=self.filial, nome="Usado")
        from cadastros.models import Categoria
        produto = Produto.objects.create(codigo="ZZ-1", nome="Caneta", categoria=Categoria.objects.first())
        SaldoEstoque.objects.create(produto=produto, almoxarifado=usado, quantidade=5)
        self.client.post(self.reverse("config_almoxarifado_excluir", args=[usado.pk]))
        self.assertTrue(Almoxarifado.objects.filter(pk=usado.pk).exists())

    def test_excluir_exige_post_e_superadmin(self):
        from tenancy.models import Almoxarifado
        a = Almoxarifado.objects.create(tenant=self.tenant, filial=self.filial, nome="Temp")
        self.client.get(self.reverse("config_almoxarifado_excluir", args=[a.pk]))
        self.assertTrue(Almoxarifado.objects.filter(pk=a.pk).exists())
        comum = Usuario.objects.create_user("comum", password="x", tenant=self.tenant)
        self.client.force_login(comum)
        r = self.client.post(self.reverse("config_almoxarifado_excluir", args=[a.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Almoxarifado.objects.filter(pk=a.pk).exists())


class OrcamentoCentroCustoTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        from tenancy.models import CentroCusto
        self.filial = Filial.objects.get(sigla="CP")
        self.centro = CentroCusto.objects.create(tenant=self.tenant, filial=self.filial, nome="Orcado")

    def definir(self, **campos):
        for k, v in campos.items():
            setattr(self.centro, k, v)
        self.centro.save()

    def test_total_dividido_em_meses_fecha_exatamente_no_total(self):
        from datetime import date
        from decimal import Decimal
        from tenancy.orcamento import orcamento_do_mes
        self.definir(orcamento_modo="TOTAL", orcamento_valor=Decimal("1000.00"),
                     orcamento_inicio=date(2026, 1, 1), orcamento_meses=3)
        parcelas = [orcamento_do_mes(self.centro, 2026, m) for m in (1, 2, 3)]
        self.assertEqual(parcelas, [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")])
        self.assertEqual(sum(parcelas), Decimal("1000.00"))
        self.assertIsNone(orcamento_do_mes(self.centro, 2025, 12))
        self.assertIsNone(orcamento_do_mes(self.centro, 2026, 4))

    def test_periodo_atravessa_o_ano(self):
        from datetime import date
        from decimal import Decimal
        from tenancy.orcamento import orcamento_do_mes
        self.definir(orcamento_modo="TOTAL", orcamento_valor=Decimal("1200"),
                     orcamento_inicio=date(2026, 11, 1), orcamento_meses=4)
        self.assertEqual(orcamento_do_mes(self.centro, 2026, 12), Decimal("300.00"))
        self.assertEqual(orcamento_do_mes(self.centro, 2027, 2), Decimal("300.00"))
        self.assertIsNone(orcamento_do_mes(self.centro, 2027, 3))

    def test_mensal_vale_todo_mes_e_sem_orcamento_e_none(self):
        from decimal import Decimal
        from tenancy.orcamento import orcamento_do_mes
        self.assertIsNone(orcamento_do_mes(self.centro, 2026, 5))
        self.definir(orcamento_modo="MENSAL", orcamento_valor=Decimal("500"))
        self.assertEqual(orcamento_do_mes(self.centro, 2026, 5), Decimal("500"))
        self.assertEqual(orcamento_do_mes(self.centro, 2030, 1), Decimal("500"))

    def test_situacao_e_excede_com_consumo_do_mes(self):
        from datetime import date
        from decimal import Decimal
        from cadastros.models import Categoria, Produto
        from estoque.models import Movimentacao, TipoMovimentacao
        from pedidos.models import Pedido
        from tenancy.models import Almoxarifado, MotivoRequisicao
        from tenancy.orcamento import excede_orcamento, situacao_do_mes
        self.definir(orcamento_modo="MENSAL", orcamento_valor=Decimal("100"))
        hoje = date.today()
        self.assertEqual(situacao_do_mes(self.centro)["consumido"], Decimal("0"))

        usuario = Usuario.objects.create_user("req", password="x", tenant=self.tenant)
        produto = Produto.objects.create(codigo="OR-1", nome="Papel", categoria=Categoria.objects.first())
        almox = Almoxarifado.objects.filter(central=False).first()
        pedido = Pedido.objects.create(
            requisitante=usuario, almoxarifado_destino=almox,
            motivo=MotivoRequisicao.objects.first(), centro_custo=self.centro,
        )
        Movimentacao.objects.create(
            tipo=TipoMovimentacao.SAIDA, produto=produto, origem=almox, quantidade=Decimal("10"),
            valor_unitario=Decimal("6"), pedido=pedido, responsavel=usuario, centro_custo=self.centro,
        )
        s = situacao_do_mes(self.centro, hoje.year, hoje.month)
        self.assertEqual(s["consumido"], Decimal("60"))
        self.assertEqual(s["saldo"], Decimal("40"))
        self.assertEqual(s["percentual"], 60)
        self.assertFalse(excede_orcamento(self.centro, Decimal("40"), hoje.year, hoje.month))
        self.assertTrue(excede_orcamento(self.centro, Decimal("40.01"), hoje.year, hoje.month))

    def dados(self, **extra):
        base = {"filial": self.filial.pk, "nome": "Novo CC", "ativo": "on", "orcamento_modo": "SEM"}
        base.update(extra)
        return base

    def test_formulario_valida_cada_modo(self):
        self.assertTrue(CentroCustoForm(self.dados()).is_valid())
        self.assertFalse(CentroCustoForm(self.dados(orcamento_modo="MENSAL")).is_valid())
        self.assertTrue(CentroCustoForm(self.dados(orcamento_modo="MENSAL", orcamento_valor="250")).is_valid())
        incompleto = CentroCustoForm(self.dados(orcamento_modo="TOTAL", orcamento_valor="1200"))
        self.assertFalse(incompleto.is_valid())
        self.assertIn("orcamento_meses", incompleto.errors)
        self.assertIn("orcamento_inicio", incompleto.errors)

    def test_formulario_total_salva_no_dia_1_e_mensal_limpa_periodo(self):
        from datetime import date
        form = CentroCustoForm(self.dados(
            orcamento_modo="TOTAL", orcamento_valor="1200", orcamento_meses="12", orcamento_inicio="2026-03"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["orcamento_inicio"], date(2026, 3, 1))
        form = CentroCustoForm(self.dados(
            orcamento_modo="MENSAL", orcamento_valor="250", orcamento_meses="12", orcamento_inicio="2026-03"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["orcamento_meses"])
        self.assertIsNone(form.cleaned_data["orcamento_inicio"])
