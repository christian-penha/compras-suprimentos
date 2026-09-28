"""
Contextvar que guarda o tenant corrente da requisição em andamento.

Setado por TenantMiddleware a partir do usuário autenticado, lido por
TenantAwareManager em toda query. Contextvars (em vez de threading.local)
porque são seguros também sob ASGI/async, onde uma thread pode atender
requisições de tenants diferentes intercaladas.
"""
from __future__ import annotations

import contextvars
from typing import Optional

_current_tenant_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_current_tenant_id(tenant_id: Optional[int]) -> contextvars.Token:
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token: contextvars.Token) -> None:
    _current_tenant_id.reset(token)


def get_current_tenant_id() -> Optional[int]:
    return _current_tenant_id.get()


class tenant_context:
    """
    Context manager para setar o tenant corrente fora do ciclo request/response
    (management commands, testes, jobs). Uso:

        with tenant_context(tenant.id):
            Produto.objects.all()  # já escopado
    """

    def __init__(self, tenant_id: Optional[int]):
        self.tenant_id = tenant_id
        self._token: Optional[contextvars.Token] = None

    def __enter__(self):
        self._token = set_current_tenant_id(self.tenant_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            reset_current_tenant_id(self._token)
