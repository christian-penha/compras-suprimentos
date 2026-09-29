from django.test import TestCase

from .constants import TENANT_PADRAO_SLUG
from .context import definir_tenant_atual, limpar_tenant_atual
from .models import Tenant


class TenantTestCase(TestCase):
    """TestCase que garante o tenant semeado pelas migrations ativo no ContextVar
    durante o teste, já que o TenantMiddleware não roda fora do ciclo request/response.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tenant = Tenant.objects.get(slug=TENANT_PADRAO_SLUG)

    def setUp(self):
        super().setUp()
        self._tenant_token = definir_tenant_atual(self.tenant.id)
        self.addCleanup(limpar_tenant_atual, self._tenant_token)
