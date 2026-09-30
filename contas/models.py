from django.contrib.auth.models import AbstractUser
from django.db import models


class Papel(models.TextChoices):
    SUPERADMIN = "SUPERADMIN", "SuperAdmin"
    ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
    APROVADOR = "APROVADOR", "Aprovador"
    SOLICITANTE = "SOLICITANTE", "Solicitante"


class Modulo(models.TextChoices):
    ESTOQUE = "ESTOQUE", "Estoque"
    COMPRAS = "COMPRAS", "Compras"
    FINANCEIRO = "FINANCEIRO", "Financeiro"


class CicloPedagogico(models.TextChoices):
    EDUCACAO_INFANTIL = "EDUCACAO_INFANTIL", "Educação Infantil"
    ANOS_INICIAIS = "ANOS_INICIAIS", "Anos Iniciais"
    ANOS_FINAIS = "ANOS_FINAIS", "Anos Finais"
    ENSINO_MEDIO = "ENSINO_MEDIO", "Ensino Médio"
    ADMINISTRATIVO = "ADMINISTRATIVO", "Administrativo"


class Usuario(AbstractUser):
    tenant = models.ForeignKey(
        "tenancy.Tenant", on_delete=models.PROTECT, related_name="usuarios"
    )
    filial = models.ForeignKey(
        "tenancy.Filial", on_delete=models.PROTECT, null=True, blank=True, related_name="usuarios"
    )
    cargo = models.CharField(max_length=100, blank=True, help_text="Cargo/função, ex.: Coordenadora Pedagógica")
    ciclo = models.CharField(max_length=20, choices=CicloPedagogico.choices, blank=True)
    nome_completo = models.CharField(max_length=150, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    ativo_no_sistema = models.BooleanField(default=True)

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self):
        return self.nome_completo or self.username

    def tem_papel(self, papel, modulo=None):
        qs = self.papeis.filter(papel=papel)
        if modulo is not None:
            qs = qs.filter(modulo=modulo)
        return qs.exists()

    @property
    def e_superadmin(self):
        return self.tem_papel(Papel.SUPERADMIN)


class PapelUsuario(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="papeis")
    papel = models.CharField(max_length=20, choices=Papel.choices)
    modulo = models.CharField(max_length=20, choices=Modulo.choices, blank=True, null=True)

    class Meta:
        verbose_name = "papel do usuário"
        verbose_name_plural = "papéis dos usuários"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "papel", "modulo"],
                name="papel_unico_por_usuario_modulo",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(papel="ADMINISTRADOR", modulo__isnull=False)
                    | ~models.Q(papel="ADMINISTRADOR")
                ),
                name="administrador_exige_modulo",
            ),
        ]

    def __str__(self):
        if self.modulo:
            return f"{self.usuario} — {self.get_papel_display()} ({self.get_modulo_display()})"
        return f"{self.usuario} — {self.get_papel_display()}"


class VinculoAprovacao(models.Model):
    requisitante = models.ForeignKey(
        Usuario, on_delete=models.CASCADE, related_name="vinculos_como_requisitante"
    )
    aprovador = models.ForeignKey(
        Usuario, on_delete=models.CASCADE, related_name="vinculos_como_aprovador"
    )
    ordem = models.PositiveSmallIntegerField(
        default=1, help_text="Sequência quando há mais de um aprovador"
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "vínculo de aprovação"
        verbose_name_plural = "vínculos de aprovação"
        ordering = ["requisitante", "ordem"]
        constraints = [
            models.UniqueConstraint(
                fields=["requisitante", "aprovador"], name="vinculo_unico"
            ),
            models.UniqueConstraint(
                fields=["requisitante", "ordem"],
                condition=models.Q(ativo=True),
                name="ordem_unica_por_requisitante_ativo",
            ),
            models.CheckConstraint(
                condition=~models.Q(requisitante=models.F("aprovador")),
                name="aprovador_diferente_do_requisitante",
            ),
        ]

    def __str__(self):
        return f"{self.requisitante} → {self.aprovador} (ordem {self.ordem})"


class PapelAlcada(models.TextChoices):
    AUTOMATICA = "AUTOMATICA", "Aprovação automática"
    DIRETORIA_UNIDADE = "DIRETORIA_UNIDADE", "Diretoria de Unidade"
    CFO = "CFO", "CFO"
    CEO = "CEO", "CEO"


class Alcada(models.Model):
    valor_min = models.DecimalField(max_digits=12, decimal_places=2)
    valor_max = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Vazio = sem teto (faixa mais alta)",
    )
    papel_aprovador = models.CharField(max_length=30, choices=PapelAlcada.choices)
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "alçada de aprovação"
        verbose_name_plural = "alçadas de aprovação"
        ordering = ["valor_min"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valor_max__isnull=True)
                | models.Q(valor_max__gt=models.F("valor_min")),
                name="valor_max_maior_que_min",
            ),
        ]

    def __str__(self):
        teto = f"R$ {self.valor_max}" if self.valor_max is not None else "sem teto"
        return f"R$ {self.valor_min} até {teto} — {self.get_papel_aprovador_display()}"

    @classmethod
    def para_valor(cls, valor):
        return (
            cls.objects.filter(ativo=True, valor_min__lte=valor)
            .filter(models.Q(valor_max__gte=valor) | models.Q(valor_max__isnull=True))
            .order_by("valor_min")
            .last()
        )
