from django.db import models

from cadastros.models import Filial, Produto


class Almoxarifado(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="almoxarifados")
    nome = models.CharField(max_length=100)
    central = models.BooleanField(
        default=False, help_text="Estoque físico central (CSC) — único no sistema"
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "almoxarifado"
        verbose_name_plural = "almoxarifados"
        ordering = ["filial", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["filial", "nome"], name="almoxarifado_unico_por_filial"),
            models.UniqueConstraint(
                fields=["central"],
                condition=models.Q(central=True),
                name="apenas_um_almoxarifado_central",
            ),
        ]

    def __str__(self):
        return f"{self.filial.sigla} — {self.nome}"


class SaldoEstoque(models.Model):
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="saldos")
    almoxarifado = models.ForeignKey(
        Almoxarifado, on_delete=models.PROTECT, related_name="saldos"
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    custo_medio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estoque_minimo = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        help_text="Definido apenas no almoxarifado central",
    )
    ponto_reposicao = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )

    class Meta:
        verbose_name = "saldo de estoque"
        verbose_name_plural = "saldos de estoque"
        constraints = [
            models.UniqueConstraint(
                fields=["produto", "almoxarifado"], name="saldo_unico_por_produto_almoxarifado"
            ),
            models.CheckConstraint(
                condition=models.Q(quantidade__gte=0), name="saldo_nunca_negativo"
            ),
        ]

    def __str__(self):
        return f"{self.produto} @ {self.almoxarifado}: {self.quantidade}"

    @property
    def abaixo_do_minimo(self):
        return self.estoque_minimo is not None and self.quantidade < self.estoque_minimo
