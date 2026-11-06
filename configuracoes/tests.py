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
        # no mensal o período é mantido: define os meses da grade mês a mês
        self.assertEqual(form.cleaned_data["orcamento_meses"], 12)
        self.assertEqual(form.cleaned_data["orcamento_inicio"], date(2026, 3, 1))
        # sem período completo, o mensal fica sem grade (vale o padrão em qualquer mês)
        form = CentroCustoForm(self.dados(orcamento_modo="MENSAL", orcamento_valor="250", orcamento_meses="12"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["orcamento_meses"])
        self.assertIsNone(form.cleaned_data["orcamento_inicio"])


class SlaEFeriadosTelasTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        from contas.models import Papel, PapelUsuario
        from django.urls import reverse
        self.reverse = reverse
        self.admin = Usuario.objects.create_user("adm2", password="x", tenant=self.tenant)
        PapelUsuario.objects.create(usuario=self.admin, papel=Papel.SUPERADMIN)
        self.client.force_login(self.admin)

    def test_salva_e_reabre_o_sla(self):
        from tenancy.models import ConfiguracaoSLA
        r = self.client.post(self.reverse("config_sla"), {"prazo_estoque_dias": 2, "prazo_compra_dias": 9})
        self.assertEqual(r.status_code, 302)
        sla = ConfiguracaoSLA.objects.get()
        self.assertEqual((sla.prazo_estoque_dias, sla.prazo_compra_dias), (2, 9))
        self.client.post(self.reverse("config_sla"), {"prazo_estoque_dias": 4, "prazo_compra_dias": 10})
        self.assertEqual(ConfiguracaoSLA.objects.count(), 1)  # edita, não duplica
        self.assertContains(self.client.get(self.reverse("config_sla")), 'value="10"')

    def test_compra_menor_que_estoque_e_recusado(self):
        r = self.client.post(self.reverse("config_sla"), {"prazo_estoque_dias": 5, "prazo_compra_dias": 2})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "não pode ser menor")

    def test_cria_periodo_de_um_dia_e_de_intervalo(self):
        from datetime import date
        from tenancy.models import PeriodoNaoUtil
        self.client.post(self.reverse("config_periodo_novo"), {
            "nome": "Dia do Professor", "tipo": "FERIADO", "inicio": "2026-10-15", "ativo": "on"})
        um = PeriodoNaoUtil.objects.get(nome="Dia do Professor")
        self.assertEqual(um.fim, date(2026, 10, 15))
        self.client.post(self.reverse("config_periodo_novo"), {
            "nome": "Recesso de julho", "tipo": "RECESSO", "inicio": "2026-07-13", "fim": "2026-07-24", "ativo": "on"})
        self.assertEqual(PeriodoNaoUtil.objects.get(nome="Recesso de julho").dias, 12)

    def test_fim_antes_do_inicio_e_recusado(self):
        r = self.client.post(self.reverse("config_periodo_novo"), {
            "nome": "Errado", "tipo": "RECESSO", "inicio": "2026-07-24", "fim": "2026-07-13", "ativo": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "não pode ser antes")

    def test_importa_feriados_nacionais_sem_duplicar(self):
        from tenancy.models import PeriodoNaoUtil
        self.client.post(self.reverse("config_periodo_importar"), {"ano": "2026"})
        total = PeriodoNaoUtil.objects.count()
        self.assertEqual(total, 13)
        self.client.post(self.reverse("config_periodo_importar"), {"ano": "2026"})
        self.assertEqual(PeriodoNaoUtil.objects.count(), total)
        self.assertContains(self.client.get(self.reverse("config_periodo_lista"), {"ano": 2026}), "Natal")

    def test_exclui_periodo_e_exige_post(self):
        from tenancy.models import PeriodoNaoUtil
        from datetime import date
        p = PeriodoNaoUtil.objects.create(tenant=self.tenant, nome="X", inicio=date(2026, 5, 4), fim=date(2026, 5, 4))
        self.client.get(self.reverse("config_periodo_excluir", args=[p.pk]))
        self.assertTrue(PeriodoNaoUtil.objects.filter(pk=p.pk).exists())
        self.client.post(self.reverse("config_periodo_excluir", args=[p.pk]))
        self.assertFalse(PeriodoNaoUtil.objects.filter(pk=p.pk).exists())

    def test_so_superadmin_acessa(self):
        comum = Usuario.objects.create_user("comum2", password="x", tenant=self.tenant)
        self.client.force_login(comum)
        for nome in ("config_sla", "config_periodo_lista", "config_periodo_novo"):
            self.assertEqual(self.client.get(self.reverse(nome)).status_code, 403, nome)


class OrcamentoMesAMesETest(TenantTestCase):
    def setUp(self):
        super().setUp()
        from tenancy.models import CentroCusto
        from contas.models import Papel, PapelUsuario
        from django.urls import reverse
        self.reverse = reverse
        self.admin = Usuario.objects.create_user("adm_mes", password="x", tenant=self.tenant)
        PapelUsuario.objects.create(usuario=self.admin, papel=Papel.SUPERADMIN)
        self.client.force_login(self.admin)
        self.filial = Filial.objects.get(sigla="CP")
        self.CentroCusto = CentroCusto

    def salvar(self, extra=None, pk=None, **campos):
        dados = {"filial": self.filial.pk, "nome": "Meses", "ativo": "on", **campos, **(extra or {})}
        url = self.reverse("config_centro_editar", args=[pk]) if pk else self.reverse("config_centro_novo")
        return self.client.post(url, dados)

    def test_total_com_meses_fixos_redistribui_o_resto(self):
        from decimal import Decimal
        from tenancy.orcamento import orcamento_do_mes
        r = self.salvar({"mes_2026-01": "500"}, orcamento_modo="TOTAL", orcamento_valor="1000",
                        orcamento_meses="3", orcamento_inicio="2026-01")
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        c = self.CentroCusto.objects.get(nome="Meses")
        self.assertEqual([orcamento_do_mes(c, 2026, m) for m in (1, 2, 3)],
                         [Decimal("500.00"), Decimal("250.00"), Decimal("250.00")])

    def test_total_nao_aceita_meses_acima_do_total_nem_soma_que_nao_fecha(self):
        r = self.salvar({"mes_2026-01": "800", "mes_2026-02": "300"}, orcamento_modo="TOTAL",
                        orcamento_valor="1000", orcamento_meses="3", orcamento_inicio="2026-01")
        self.assertEqual(r.status_code, 200)
        r = self.salvar({"mes_2026-01": "400", "mes_2026-02": "300"}, orcamento_modo="TOTAL",
                        orcamento_valor="1000", orcamento_meses="2", orcamento_inicio="2026-01")
        self.assertEqual(r.status_code, 200)  # todos preenchidos e soma 700 != 1000
        self.assertFalse(self.CentroCusto.objects.filter(nome="Meses").exists())

    def test_mensal_com_mes_diferente_e_reabre_preenchido(self):
        from decimal import Decimal
        from tenancy.orcamento import orcamento_do_mes
        self.salvar({"mes_2026-02": "900"}, orcamento_modo="MENSAL", orcamento_valor="400",
                    orcamento_meses="3", orcamento_inicio="2026-01")
        c = self.CentroCusto.objects.get(nome="Meses")
        self.assertEqual([orcamento_do_mes(c, 2026, m) for m in (1, 2, 3)],
                         [Decimal("400"), Decimal("900.00"), Decimal("400")])
        self.assertContains(self.client.get(self.reverse("config_centro_editar", args=[c.pk])), "2026-02")
        # salvar de novo sem o valor do mês remove o valor próprio
        self.salvar(pk=c.pk, orcamento_modo="MENSAL", orcamento_valor="400", orcamento_meses="3", orcamento_inicio="2026-01")
        c.refresh_from_db()
        self.assertEqual(orcamento_do_mes(c, 2026, 2), Decimal("400"))

    def test_mes_negativo_e_recusado(self):
        r = self.salvar({"mes_2026-01": "-5"}, orcamento_modo="MENSAL", orcamento_valor="400",
                        orcamento_meses="2", orcamento_inicio="2026-01")
        self.assertEqual(r.status_code, 200)

    def test_plano_respeita_mes_fixo(self):
        from datetime import date
        from decimal import Decimal
        from tenancy.models import OrcamentoMes
        from tenancy.previsao import plano
        c = self.CentroCusto.objects.create(
            tenant=self.tenant, filial=self.filial, nome="P", orcamento_modo="TOTAL",
            orcamento_valor=Decimal("1000"), orcamento_inicio=date(2026, 1, 1), orcamento_meses=4)
        OrcamentoMes.objects.create(tenant=self.tenant, centro=c, ano_mes=date(2026, 2, 1), valor=Decimal("400"))
        limites = [l["limite"] for l in plano(c, date(2026, 1, 15))]
        self.assertEqual(limites, [Decimal("200.00"), Decimal("400.00"), Decimal("200.00"), Decimal("200.00")])

    def test_subconta_aparece_dentro_do_centro_e_nasce_pre_preenchida(self):
        pai = self.CentroCusto.objects.create(tenant=self.tenant, filial=self.filial, nome="Pai")
        self.salvar(nome="Filha", pai=pai.pk, orcamento_modo="")
        filha = self.CentroCusto.objects.get(nome="Filha")
        self.assertEqual(filha.pai_id, pai.pk)
        lista = self.client.get(self.reverse("config_centro_lista"))
        self.assertContains(lista, "Filha")
        self.assertContains(lista, f"?pai={pai.pk}")
        topo = [c.pk for c in lista.context["centros"]]
        self.assertIn(pai.pk, topo)
        self.assertNotIn(filha.pk, topo)  # a filha vem aninhada na conta pai
        self.assertContains(self.client.get(self.reverse("config_centro_editar", args=[pai.pk])), "Filha")
        novo = self.client.get(self.reverse("config_centro_novo"), {"pai": pai.pk})
        self.assertEqual(novo.context["form"].initial["pai"], pai.pk)

    def test_busca_pela_subconta_mostra_a_conta_pai(self):
        pai = self.CentroCusto.objects.create(tenant=self.tenant, filial=self.filial, nome="Pai")
        self.CentroCusto.objects.create(tenant=self.tenant, filial=self.filial, nome="Filha rara", pai=pai)
        lista = self.client.get(self.reverse("config_centro_lista"), {"q": "rara"})
        self.assertEqual([c.pk for c in lista.context["centros"]], [pai.pk])

    def test_soma_das_subcontas_em_qualquer_mes_nao_passa_do_pai(self):
        from datetime import date
        from decimal import Decimal
        pai = self.CentroCusto.objects.create(
            tenant=self.tenant, filial=self.filial, nome="Pai", orcamento_modo="MENSAL", orcamento_valor=Decimal("1000"))
        r = self.salvar(nome="F1", pai=pai.pk, orcamento_modo="MENSAL", orcamento_valor="600")
        self.assertEqual(r.status_code, 302)
        r = self.salvar(nome="F2", pai=pai.pk, orcamento_modo="MENSAL", orcamento_valor="300",
                        extra={"mes_2026-03": "500"}, orcamento_meses="3", orcamento_inicio="2026-01")
        self.assertEqual(r.status_code, 200)  # em março 600 + 500 > 1000
