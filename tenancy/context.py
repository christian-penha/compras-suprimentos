from contextvars import ContextVar

_tenant_atual_id = ContextVar("tenant_atual_id", default=None)


def definir_tenant_atual(tenant_id):
    return _tenant_atual_id.set(tenant_id)


def limpar_tenant_atual(token):
    _tenant_atual_id.reset(token)


def obter_tenant_atual_id():
    return _tenant_atual_id.get()
