from django.conf import settings
from django.db import models

from cadastros.models import Produto
from tenancy.models import Almoxarifado


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


class TipoMovimentacao(models.TextChoices):
    ENTRADA = "ENTRADA", "Entrada"
    SAIDA = "SAIDA", "Saída/Consumo"
    TRANSFERENCIA = "TRANSFERENCIA", "Transferência"
    DEVOLUCAO = "DEVOLUCAO", "Devolução"
    AJUSTE = "AJUSTE", "Ajuste"


class Movimentacao(models.Model):
    tipo = models.CharField(max_length=20, choices=TipoMovimentacao.choices)
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="movimentacoes")
    origem = models.ForeignKey(
        Almoxarifado, on_delete=models.PROTECT, null=True, blank=True,
        related_name="movimentacoes_saida",
    )
    destino = models.ForeignKey(
        Almoxarifado, on_delete=models.PROTECT, null=True, blank=True,
        related_name="movimentacoes_entrada",
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    valor_unitario = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pedido = models.ForeignKey(
        "pedidos.Pedido", on_delete=models.PROTECT, null=True, blank=True,
        related_name="movimentacoes",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="movimentacoes"
    )
    arquivo_origem = models.CharField(max_length=255, blank=True)
    observacao = models.CharField(max_length=300, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "movimentação"
        verbose_name_plural = "movimentações"
        ordering = ["-criado_em"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantidade__gt=0), name="movimentacao_quantidade_positiva"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(tipo__in=["ENTRADA", "DEVOLUCAO"], origem__isnull=True, destino__isnull=False)
                    | models.Q(tipo="SAIDA", origem__isnull=False, destino__isnull=True)
                    | models.Q(tipo="TRANSFERENCIA", origem__isnull=False, destino__isnull=False)
                    | models.Q(tipo="AJUSTE")
                ),
                name="movimentacao_origem_destino_coerentes",
            ),
            models.CheckConstraint(
                condition=~models.Q(tipo="TRANSFERENCIA", origem=models.F("destino")),
                name="transferencia_origem_diferente_destino",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.quantidade} × {self.produto}"


class DebitoAlmoxarifado(models.Model):
    devedor = models.ForeignKey(
        Almoxarifado, on_delete=models.PROTECT, related_name="debitos_devidos"
    )
    credor = models.ForeignKey(
        Almoxarifado, on_delete=models.PROTECT, related_name="debitos_a_receber"
    )
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="+")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        verbose_name = "débito entre almoxarifados"
        verbose_name_plural = "débitos entre almoxarifados"
        constraints = [
            models.UniqueConstraint(
                fields=["devedor", "credor", "produto"], name="debito_unico_por_par_produto"
            ),
            models.CheckConstraint(
                condition=~models.Q(devedor=models.F("credor")), name="debito_devedor_diferente_credor"
            ),
        ]

    def __str__(self):
        return f"{self.devedor} deve {self.quantidade} × {self.produto} a {self.credor}"
