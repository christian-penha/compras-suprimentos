"""
TenantAwareManager — filtra automaticamente pelo tenant corrente.

Regra não-negociável (ver docs/08-Prompt-Inicial-do-Projeto.html, seção
"Segurança"): nenhuma view usa Model.all_objects nem monta queryset sem
passar por este manager. `all_objects` é o escape hatch explícito, de uso
restrito a management commands e ao Django admin quando justificado.

Se não houver tenant corrente setado (fora do ciclo request/response, ex.
um teste ou script esquecido de usar `tenant_context`), o manager retorna
queryset vazio em vez de vazar dado de todos os tenants — falhar fechado,
não aberto.
"""
from __future__ import annotations

from django.db import models

from apps.core.context import get_current_tenant_id


class TenantAwareManager(models.Manager):
    def get_queryset(self) -> models.QuerySet:
        tenant_id = get_current_tenant_id()
        qs = super().get_queryset()
        if tenant_id is None:
            return qs.none()
        return qs.filter(tenant_id=tenant_id)

    def unscoped(self) -> models.QuerySet:
        """Escape hatch explícito e nomeado, para uso deliberado e auditável
        dentro de código que já sabe que está operando fora do escopo de um
        tenant (ex. um job cross-tenant do superadmin). Ao contrário de
        `all_objects`, passa pelo mesmo manager — útil quando se quer manter
        a semântica de `objects` mas remover só o filtro de tenant."""
        return super().get_queryset()
