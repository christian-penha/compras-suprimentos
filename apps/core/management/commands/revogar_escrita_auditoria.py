"""
Revoga UPDATE/DELETE na tabela de auditoria a nível de banco (não só de
Django model) — ver docs/07-Modelo-de-Dados.html, seção 8, e "Definição de
pronto para a Fase 0": "Auditoria append-only com permissão de banco
revogada, testada manualmente (tentar um UPDATE direto no banco e confirmar
que falha)".

Roda depois do primeiro `migrate` da app `auditoria`. Idempotente — pode
rodar de novo sem efeito colateral se já revogado.

Uso: python manage.py revogar_escrita_auditoria
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Revoga UPDATE/DELETE na tabela auditoria_registroauditoria para o usuário de aplicação."

    def handle(self, *args, **options):
        tabela = "auditoria_registroauditoria"
        usuario_app = settings.DATABASES["default"]["USER"]

        with connection.cursor() as cursor:
            cursor.execute(f'REVOKE UPDATE, DELETE ON "{tabela}" FROM "{usuario_app}";')

        self.stdout.write(
            self.style.SUCCESS(
                f"UPDATE/DELETE revogado em {tabela} para o usuário {usuario_app}."
            )
        )
