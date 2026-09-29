from .context import definir_tenant_atual, limpar_tenant_atual


class TenantMiddleware:
    """Define o tenant corrente (do usuário logado) no ContextVar usado pelo
    TenantAwareManager. Deve rodar depois do AuthenticationMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            tenant_id = user.tenant_id

        token = definir_tenant_atual(tenant_id)
        try:
            response = self.get_response(request)
        finally:
            limpar_tenant_atual(token)
        return response
