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


class Usuario(AbstractUser):
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
