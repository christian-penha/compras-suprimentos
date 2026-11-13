from decimal import Decimal

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
        help_text="Abaixo disso o item aparece como crítico na reposição",
    )
    ponto_reposicao = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        help_text="Quando o saldo chega aqui, é hora de pedir reposição",
    )
    estoque_maximo = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        help_text="Quantidade-alvo ao repor; acima disso há excesso",
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
            models.CheckConstraint(
                condition=(
                    models.Q(estoque_minimo__isnull=True) | models.Q(estoque_maximo__isnull=True)
                    | models.Q(estoque_minimo__lte=models.F("estoque_maximo"))
                ),
                name="saldo_minimo_ate_maximo",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ponto_reposicao__isnull=True) | models.Q(estoque_minimo__isnull=True)
                    | models.Q(ponto_reposicao__gte=models.F("estoque_minimo"))
                ),
                name="saldo_reposicao_acima_do_minimo",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ponto_reposicao__isnull=True) | models.Q(estoque_maximo__isnull=True)
                    | models.Q(ponto_reposicao__lte=models.F("estoque_maximo"))
                ),
                name="saldo_reposicao_ate_maximo",
            ),
        ]

    def __str__(self):
        return f"{self.produto} @ {self.almoxarifado}: {self.quantidade}"

    @property
    def abaixo_do_minimo(self):
        return self.estoque_minimo is not None and self.quantidade < self.estoque_minimo

    @property
    def acima_do_maximo(self):
        return self.estoque_maximo is not None and self.quantidade > self.estoque_maximo

    @property
    def precisa_repor(self):
        if self.abaixo_do_minimo:
            return True
        return self.ponto_reposicao is not None and self.quantidade <= self.ponto_reposicao

    @property
    def sugestao_reposicao(self):
        """Quanto pedir para voltar ao alvo (máximo; sem máximo, o ponto de reposição ou o mínimo)."""
        if not self.precisa_repor:
            return Decimal("0")
        alvo = self.estoque_maximo
        if alvo is None:
            alvos = [v for v in (self.ponto_reposicao, self.estoque_minimo) if v is not None]
            alvo = max(alvos) if alvos else Decimal("0")
        return max(alvo - self.quantidade, Decimal("0"))


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
    # Eixo consumo (RN-17): definido por suprimentos na entrega do pedido.
    centro_custo = models.ForeignKey(
        "tenancy.CentroCusto", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
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


class StatusBalanco(models.TextChoices):
    EM_CONTAGEM = "EM_CONTAGEM", "Em contagem"
    APLICADO = "APLICADO", "Aplicado"
    CANCELADO = "CANCELADO", "Cancelado"


class Balanco(models.Model):
    """Inventário de um almoxarifado: congela o saldo do sistema, recebe a contagem física e,
    ao aplicar, gera um AJUSTE por diferença (nada muda no saldo antes de aplicar)."""

    almoxarifado = models.ForeignKey(Almoxarifado, on_delete=models.PROTECT, related_name="balancos")
    status = models.CharField(max_length=12, choices=StatusBalanco.choices, default=StatusBalanco.EM_CONTAGEM)
    observacao = models.CharField(max_length=300, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    encerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    encerrado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "balanço de estoque"
        verbose_name_plural = "balanços de estoque"
        ordering = ["-criado_em", "-pk"]

    def __str__(self):
        return f"Balanço #{self.pk} — {self.almoxarifado}"


class ItemBalanco(models.Model):
    balanco = models.ForeignKey(Balanco, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="+")
    quantidade_sistema = models.DecimalField(max_digits=12, decimal_places=3)
    quantidade_contada = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    # diferença efetivamente ajustada na aplicação (contada − saldo do momento)
    diferenca_aplicada = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["produto__nome", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["balanco", "produto"], name="item_balanco_unico_por_produto"),
            models.CheckConstraint(
                condition=models.Q(quantidade_contada__isnull=True) | models.Q(quantidade_contada__gte=0),
                name="item_balanco_contagem_nao_negativa",
            ),
        ]

    @property
    def diferenca(self):
        if self.quantidade_contada is None:
            return None
        return self.quantidade_contada - self.quantidade_sistema
