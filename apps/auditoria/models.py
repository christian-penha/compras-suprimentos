# App completo quando a primeira ação sensível existir para auditar (Fase 1+).
# Ver docs/07-Modelo-de-Dados.html, seção 8 — RegistroAuditoria com
# tenant_id como IntegerField (não FK) de propósito, e UPDATE/DELETE
# revogados a nível de banco (ver apps/core/management/commands/
# revogar_escrita_auditoria.py).
