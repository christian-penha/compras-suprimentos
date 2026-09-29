from django.db import models

from .context import obter_tenant_atual_id


class TenantAwareManager(models.Manager):
    """Filtra automaticamente pelo tenant corrente (definido pelo TenantMiddleware).

    Sem tenant no contexto (fora de uma request autenticada — ex.: import de módulo,
    shell, management command), retorna queryset vazio em vez de vazar dados de
    outros tenants. Use `Model.todos_os_tenants` para consultas administrativas que
    precisam enxergar todos os tenants deliberadamente.
    """

    usa_contexto_de_tenant = True

    def get_queryset(self):
        tenant_id = obter_tenant_atual_id()
        qs = super().get_queryset()
        if tenant_id is None:
            return qs.none()
        return qs.filter(tenant_id=tenant_id)


class TenantModel(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.PROTECT, related_name="+")

    objects = TenantAwareManager()
    todos_os_tenants = models.Manager()

    class Meta:
        abstract = True


class Tenant(models.Model):
    nome = models.CharField(max_length=150)
    slug = models.SlugField(max_length=60, unique=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "tenant"
        verbose_name_plural = "tenants"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Empresa(TenantModel):
    razao_social = models.CharField(max_length=200)
    nome_fantasia = models.CharField(max_length=200, blank=True)
    cnpj = models.CharField(max_length=18)
    codigo_prodados = models.CharField(max_length=30, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        ordering = ["razao_social"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "cnpj"], name="cnpj_unico_por_tenant"),
        ]

    def __str__(self):
        return self.nome_fantasia or self.razao_social


class Filial(TenantModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="filiais")
    nome = models.CharField(max_length=100)
    sigla = models.CharField(max_length=10)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "filial"
        verbose_name_plural = "filiais"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sigla"], name="sigla_unica_por_tenant"),
        ]

    def __str__(self):
        return self.nome


class Almoxarifado(TenantModel):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="almoxarifados")
    nome = models.CharField(max_length=100)
    central = models.BooleanField(
        default=False, help_text="Estoque físico central (CSC) — único por tenant"
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "almoxarifado"
        verbose_name_plural = "almoxarifados"
        ordering = ["filial", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["filial", "nome"], name="almoxarifado_unico_por_filial"),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(central=True),
                name="apenas_um_almoxarifado_central_por_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.filial.sigla} — {self.nome}"


class CentroCusto(TenantModel):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="centros_custo")
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "centro de custo"
        verbose_name_plural = "centros de custo"
        ordering = ["filial", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["filial", "nome"], name="centro_unico_por_filial"),
        ]

    def __str__(self):
        return f"{self.filial.sigla} — {self.nome}"


class Subcentro(TenantModel):
    centro_custo = models.ForeignKey(
        CentroCusto, on_delete=models.PROTECT, related_name="subcentros"
    )
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "subcentro"
        verbose_name_plural = "subcentros"
        ordering = ["centro_custo", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["centro_custo", "nome"], name="subcentro_unico_por_centro"
            ),
        ]

    def __str__(self):
        return f"{self.centro_custo} / {self.nome}"


class MotivoRequisicao(TenantModel):
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "motivo de requisição"
        verbose_name_plural = "motivos de requisição"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "nome"], name="motivo_unico_por_tenant"),
        ]

    def __str__(self):
        return self.nome
