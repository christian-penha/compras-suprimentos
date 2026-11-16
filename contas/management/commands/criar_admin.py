import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tenancy.models import Tenant


class Command(BaseCommand):
    help = (
        "Cria o superusuário inicial no primeiro tenant (o createsuperuser padrão não "
        "serve: Usuario.tenant é obrigatório). Não altera um usuário que já existe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--usuario", default=os.environ.get("DJANGO_ADMIN_USER", ""))
        parser.add_argument("--email", default=os.environ.get("DJANGO_ADMIN_EMAIL", ""))
        parser.add_argument("--senha", default=os.environ.get("DJANGO_ADMIN_PASSWORD", ""))

    def handle(self, *args, usuario, email, senha, **opcoes):
        if not usuario or not senha:
            raise CommandError("Informe --usuario e --senha (ou DJANGO_ADMIN_USER / DJANGO_ADMIN_PASSWORD).")
        Usuario = get_user_model()
        if Usuario.objects.filter(username=usuario).exists():
            self.stdout.write(f"Usuário '{usuario}' já existe; nada a fazer.")
            return
        tenant = Tenant.objects.order_by("pk").first()
        if tenant is None:
            raise CommandError("Nenhum tenant cadastrado; rode as migrations antes.")
        Usuario.objects.create_superuser(username=usuario, email=email, password=senha, tenant=tenant)
        self.stdout.write(self.style.SUCCESS(f"Superusuário '{usuario}' criado em {tenant}."))
