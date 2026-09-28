import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.core.managers import TenantAwareManager


class Tenant(models.Model):
    """O grupo econômico (Grupo Perfil de Educação). Uma linha só hoje,
    preparado para mais — ver decisão de multi-tenancy linha a linha em
    docs/07-Modelo-de-Dados.html, seção 0."""

    nome = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core"

    def __str__(self) -> str:
        return self.nome


class TenantModel(models.Model):
    """Mixin abstrato: todo model de negócio herda daqui.

    `objects` filtra automaticamente pelo tenant corrente (via contextvar,
    setado por TenantMiddleware). `all_objects` é o escape hatch explícito
    — seu uso fora de management commands/admin é o que o teste automatizado
    de escopo (apps/core/tests/test_tenant_scope.py) procura pegar.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)

    objects = TenantAwareManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True


class Usuario(AbstractUser):
    """
    settings.AUTH_USER_MODEL do projeto. Existe (em vez do User padrão do
    Django) só porque TenantMiddleware precisa de `tenant` em toda requisição
    autenticada para popular o contextvar que o TenantAwareManager lê — e
    trocar AUTH_USER_MODEL depois do primeiro `migrate` exige reescrever
    todo o histórico de migrations. Decisão tomada aqui, na Fase 0, para não
    fechar essa porta depois (não estava explícita nos documentos de
    levantamento).

    `tenant` fica null=True só para comportar o superusuário criado por
    `createsuperuser` antes de qualquer Tenant existir; todo usuário real do
    sistema (papéis de negócio) tem tenant obrigatório em nível de validação
    de formulário/admin, não de banco.
    """

    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        app_label = "core"


class Feriado(TenantModel):
    """Feriado nacional ou recesso próprio do grupo, usado pelo calendário de
    dias úteis (apps/core/calendario.py) em todo cálculo de SLA e alçada.
    Por tenant porque o recesso escolar é do Grupo Perfil, não um dado
    universal — mantido pelo admin/superadmin sem precisar de deploy."""

    data = models.DateField()
    descricao = models.CharField(max_length=120)

    class Meta:
        app_label = "core"
        unique_together = [("tenant", "data")]
        ordering = ["data"]

    def __str__(self) -> str:
        return f"{self.data.isoformat()} — {self.descricao}"


class UUIDPublicIdMixin(models.Model):
    """IDs públicos como UUID, não sequenciais — reduz enumeração (ver
    docs/08-Prompt-Inicial-do-Projeto.html, seção Segurança). Usar em todo
    model exposto em URL ou API; a PK interna (BigAutoField) continua
    sequencial para performance de índice/FK."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        abstract = True
