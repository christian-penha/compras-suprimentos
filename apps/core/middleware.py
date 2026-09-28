"""
TenantMiddleware — resolve o tenant do usuário autenticado e o publica no
contextvar lido por TenantAwareManager em toda query da requisição.

Hoje (um tenant real), a resolução é simples: o usuário tem um tenant fixo
em `user.tenant_id` (ver apps/tenancy — o model de usuário/perfil carrega
essa FK). Isolado aqui para que, se um dia houver múltiplos tenants por
domínio/subdomínio, só este middleware muda — o resto da aplicação não
sabe como o tenant foi resolvido, só que ele está no contextvar.
"""
from __future__ import annotations

from apps.core.context import reset_current_tenant_id, set_current_tenant_id


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = self._resolve_tenant_id(request)
        token = set_current_tenant_id(tenant_id)
        try:
            response = self.get_response(request)
        finally:
            reset_current_tenant_id(token)
        return response

    @staticmethod
    def _resolve_tenant_id(request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        # Perfil de usuário (apps.tenancy) carrega a FK para Tenant. Acesso
        # defensivo: em Fase 0, antes de aprovacao/perfil existir, isso é
        # None e o manager retorna queryset vazio — falha fechada.
        return getattr(user, "tenant_id", None)
