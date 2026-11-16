import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from contas.models import Papel, PapelUsuario
from tenancy.models import Almoxarifado, Filial, Tenant


class Command(BaseCommand):
    help = (
        "Cria o administrador do aplicativo (papel SuperAdmin: todas as telas e o menu completo). "
        "Não dá acesso ao painel /admin/ do Django. Nunca altera um usuário que já existe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--usuario", default=os.environ.get("DJANGO_ADMIN_USER", "") or "admin")
        parser.add_argument("--email", default=os.environ.get("DJANGO_ADMIN_EMAIL", ""))
        parser.add_argument("--senha", default=os.environ.get("DJANGO_ADMIN_PASSWORD", ""))
        parser.add_argument(
            "--garantir",
            action="store_true",
            help="Só cria se ainda não houver nenhum SuperAdmin. Sem senha informada, gera uma "
            "aleatória e mostra no log uma única vez.",
        )

    @transaction.atomic
    def handle(self, *args, usuario, email, senha, garantir, **opcoes):
        Usuario = get_user_model()
        usuario = usuario or "admin"
        if garantir and PapelUsuario.objects.filter(papel=Papel.SUPERADMIN).exists():
            self.stdout.write("Administrador do aplicativo já existe; nada a fazer.")
            return
        if Usuario.objects.filter(username=usuario).exists():
            self.stdout.write(f"Usuário '{usuario}' já existe; nada a fazer.")
            return
        gerada = False
        if not senha:
            if not garantir:
                raise CommandError("Informe --senha (ou DJANGO_ADMIN_PASSWORD).")
            senha = secrets.token_urlsafe(12)
            gerada = True
        tenant = Tenant.objects.order_by("pk").first()
        if tenant is None:
            raise CommandError("Nenhum tenant cadastrado; rode as migrations antes.")

        # Fora de uma request o manager padrão não enxerga nada: usa todos_os_tenants filtrando pelo tenant.
        filiais = list(Filial.todos_os_tenants.filter(tenant=tenant, ativo=True).order_by("pk"))
        admin = Usuario.objects.create_user(
            username=usuario,
            email=email,
            password=senha,
            tenant=tenant,
            filial=filiais[0] if filiais else None,
            nome_completo="Administrador do sistema",
        )
        PapelUsuario.objects.create(usuario=admin, papel=Papel.SUPERADMIN)
        admin.filiais_permitidas.set(filiais)
        admin.almoxarifados.set(Almoxarifado.todos_os_tenants.filter(tenant=tenant))

        self.stdout.write(self.style.SUCCESS(f"Administrador do aplicativo '{usuario}' criado em {tenant}."))
        if gerada:
            self.stdout.write(self.style.WARNING(
                f"*** Senha gerada para '{usuario}': {senha} — anote e troque no primeiro acesso. ***"
            ))
