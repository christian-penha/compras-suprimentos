from django.apps import apps
from django.test import TestCase

from .context import definir_tenant_atual, limpar_tenant_atual
from .models import Empresa, Filial, Tenant, TenantAwareManager, TenantModel


class TodosOsModelsTenantUsamManagerCorretoTest(TestCase):
    def test_subclasses_de_tenant_model_usam_tenant_aware_manager(self):
        for model in apps.get_models():
            if not issubclass(model, TenantModel) or model._meta.abstract:
                continue
            with self.subTest(model=model.__name__):
                self.assertIsInstance(
                    model._default_manager,
                    TenantAwareManager,
                    f"{model.__name__} deveria usar TenantAwareManager como manager padrão",
                )


class IsolamentoEntreTenantsTest(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(nome="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(nome="Tenant B", slug="tenant-b")

        token = definir_tenant_atual(self.tenant_a.id)
        self.empresa_a = Empresa.objects.create(
            tenant=self.tenant_a, razao_social="Empresa A", cnpj="11.111.111/0001-11"
        )
        self.filial_a = Filial.objects.create(
            tenant=self.tenant_a, empresa=self.empresa_a, nome="Filial A", sigla="FA"
        )
        limpar_tenant_atual(token)

        token = definir_tenant_atual(self.tenant_b.id)
        self.empresa_b = Empresa.objects.create(
            tenant=self.tenant_b, razao_social="Empresa B", cnpj="22.222.222/0001-22"
        )
        self.filial_b = Filial.objects.create(
            tenant=self.tenant_b, empresa=self.empresa_b, nome="Filial B", sigla="FB"
        )
        limpar_tenant_atual(token)

    def test_queryset_so_enxerga_o_proprio_tenant(self):
        token = definir_tenant_atual(self.tenant_a.id)
        try:
            siglas = set(Filial.objects.values_list("sigla", flat=True))
        finally:
            limpar_tenant_atual(token)
        self.assertEqual(siglas, {"FA"})

    def test_sem_tenant_no_contexto_retorna_vazio(self):
        self.assertEqual(Filial.objects.count(), 0)

    def test_todos_os_tenants_enxerga_ambos(self):
        siglas = set(Filial.todos_os_tenants.values_list("sigla", flat=True))
        self.assertTrue({"FA", "FB"}.issubset(siglas))


from datetime import date, timedelta  # noqa: E402

from .calendario import datas_nao_uteis, feriados_nacionais, prazos_sla, somar_dias_uteis  # noqa: E402
from .models import ConfiguracaoSLA, PeriodoNaoUtil, TipoPeriodoNaoUtil  # noqa: E402
from .testing import TenantTestCase  # noqa: E402


class CalendarioDiasUteisTest(TenantTestCase):
    SEGUNDA = date(2026, 10, 26)

    def periodo(self, inicio, fim=None, **extra):
        return PeriodoNaoUtil.objects.create(
            tenant=self.tenant, nome=extra.pop("nome", "Folga"), inicio=inicio, fim=fim or inicio, **extra
        )

    def test_fim_de_semana_nao_conta(self):
        # segunda + 5 dias úteis = segunda seguinte
        self.assertEqual(somar_dias_uteis(self.SEGUNDA, 5, set()), date(2026, 11, 2))
        # sexta + 1 dia útil = segunda
        self.assertEqual(somar_dias_uteis(date(2026, 10, 30), 1, set()), date(2026, 11, 2))

    def test_feriado_empurra_o_prazo(self):
        self.periodo(date(2026, 10, 28), nome="Dia do Servidor")  # quarta
        nao_uteis = datas_nao_uteis(None, self.SEGUNDA)
        self.assertEqual(somar_dias_uteis(self.SEGUNDA, 3, nao_uteis), date(2026, 10, 30))

    def test_recesso_de_varios_dias(self):
        self.periodo(date(2026, 12, 21), date(2027, 1, 8), nome="Recesso de fim de ano",
                     tipo=TipoPeriodoNaoUtil.RECESSO)
        nao_uteis = datas_nao_uteis(None, date(2026, 12, 18), date(2027, 2, 1))
        self.assertEqual(somar_dias_uteis(date(2026, 12, 18), 1, nao_uteis), date(2027, 1, 11))

    def test_periodo_inativo_e_ignorado(self):
        self.periodo(date(2026, 10, 28), ativo=False)
        self.assertNotIn(date(2026, 10, 28), datas_nao_uteis(None, self.SEGUNDA))

    def test_periodo_de_uma_filial_so_vale_para_ela(self):
        cp = Filial.objects.get(sigla="CP")
        vc = Filial.objects.get(sigla="VC")
        self.periodo(date(2026, 10, 28), filial=cp)
        self.assertIn(date(2026, 10, 28), datas_nao_uteis(cp, self.SEGUNDA))
        self.assertNotIn(date(2026, 10, 28), datas_nao_uteis(vc, self.SEGUNDA))
        self.assertNotIn(date(2026, 10, 28), datas_nao_uteis(None, self.SEGUNDA))

    def test_prazos_usam_a_configuracao_ou_os_padroes(self):
        padrao = prazos_sla(None, self.SEGUNDA)
        self.assertEqual(padrao["estoque"], date(2026, 10, 29))  # 3 dias úteis
        self.assertEqual(padrao["compra"], date(2026, 11, 4))  # 7 dias úteis
        ConfiguracaoSLA.objects.create(tenant=self.tenant, prazo_estoque_dias=1, prazo_compra_dias=2)
        novo = prazos_sla(None, self.SEGUNDA)
        self.assertEqual(novo["estoque"], date(2026, 10, 27))
        self.assertEqual(novo["compra"], date(2026, 10, 28))

    def test_um_sla_por_tenant(self):
        from django.db import IntegrityError, transaction
        ConfiguracaoSLA.objects.create(tenant=self.tenant)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracaoSLA.objects.create(tenant=self.tenant)

    def test_feriados_nacionais_incluem_os_moveis(self):
        datas = dict(feriados_nacionais(2026))
        self.assertEqual(datas[date(2026, 4, 3)], "Sexta-feira Santa")
        self.assertEqual(datas[date(2026, 2, 16)], "Carnaval (segunda)")
        self.assertEqual(datas[date(2026, 6, 4)], "Corpus Christi")
        self.assertEqual(datas[date(2026, 9, 7)], "Independência do Brasil")
        self.assertEqual(len(datas), 13)
