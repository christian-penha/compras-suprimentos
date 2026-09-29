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
