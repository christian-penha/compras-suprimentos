from decimal import Decimal

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models

from tenancy.models import TenantModel


def armazenamento_privado():
    # Chamável (e não instância) para a migration não gravar o caminho da máquina.
    return FileSystemStorage(location=settings.PRIVATE_ROOT)


class StatusCotacao(models.TextChoices):
    EM_COMPARACAO = "EM_COMPARACAO", "Em comparação"
    AGUARDANDO_ALCADA = "AGUARDANDO_ALCADA", "Aguardando alçada"
    APROVADA = "APROVADA", "Aprovada"
    RECUSADA = "RECUSADA", "Recusada"
    CANCELADA = "CANCELADA", "Cancelada"


class Cotacao(TenantModel):
    fornecedor = models.ForeignKey(
        "cadastros.Fornecedor", on_delete=models.PROTECT, related_name="cotacoes"
    )
    comprador = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cotacoes"
    )
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    alcada = models.ForeignKey(
        "contas.Alcada", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    status = models.CharField(
        max_length=20, choices=StatusCotacao.choices, default=StatusCotacao.AGUARDANDO_ALCADA
    )
    numero = models.CharField(
        max_length=60, blank=True,
        help_text="Herdado do(s) pedido(s) de origem: 9, 9.2 (2ª cotação do pedido 9), 9 + 11",
    )
    observacao = models.CharField(max_length=300, blank=True)
    decidida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decidida_em = models.DateTimeField(null=True, blank=True)
    motivo_recusa = models.CharField(max_length=300, blank=True)
    # Propostas de fornecedores concorrentes pelos mesmos itens compartilham a rodada.
    rodada = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cotação"
        verbose_name_plural = "cotações"
        ordering = ["-criada_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "numero"], condition=~models.Q(numero=""),
                name="numero_de_cotacao_unico_por_tenant",
            ),
        ]

    def __str__(self):
        return f"Cotação #{self.numero or self.pk} — {self.fornecedor}"

    @property
    def pedidos_origem(self):
        ids = sorted({i.item_pedido.pedido_id for i in self.itens.all()})
        return " + ".join(str(i) for i in ids)

    @property
    def valor_itens(self):
        return sum((i.subtotal for i in self.itens.all()), Decimal("0"))

    @property
    def ativa(self):
        return self.status in (
            StatusCotacao.EM_COMPARACAO, StatusCotacao.AGUARDANDO_ALCADA, StatusCotacao.APROVADA
        )


class ItemCotacao(models.Model):
    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name="itens")
    item_pedido = models.ForeignKey(
        "pedidos.ItemPedido", on_delete=models.PROTECT, related_name="itens_cotacao"
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    valor_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    quantidade_recebida = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))

    class Meta:
        verbose_name = "item da cotação"
        verbose_name_plural = "itens da cotação"
        constraints = [
            models.UniqueConstraint(fields=["cotacao", "item_pedido"], name="item_unico_por_cotacao"),
            models.CheckConstraint(condition=models.Q(quantidade__gt=0), name="itemcotacao_qtd_positiva"),
            models.CheckConstraint(
                condition=models.Q(valor_unitario__gte=0), name="itemcotacao_valor_nao_negativo"
            ),
        ]

    def __str__(self):
        return f"{self.quantidade} × {self.item_pedido.produto}"

    @property
    def subtotal(self):
        return self.quantidade * self.valor_unitario

    @property
    def pendente(self):
        return self.quantidade - self.quantidade_recebida


class StatusOrdem(models.TextChoices):
    ABERTA = "ABERTA", "Aberta"
    PARCIALMENTE_RECEBIDA = "PARCIALMENTE_RECEBIDA", "Parcialmente recebida"
    CONCLUIDA = "CONCLUIDA", "Concluída"
    CANCELADA = "CANCELADA", "Cancelada"


class OrdemCompra(TenantModel):
    cotacao = models.OneToOneField(Cotacao, on_delete=models.PROTECT, related_name="ordem")
    status = models.CharField(max_length=24, choices=StatusOrdem.choices, default=StatusOrdem.ABERTA)
    criada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ordem de compra"
        verbose_name_plural = "ordens de compra"
        ordering = ["-criada_em"]

    def __str__(self):
        return f"OC #{self.numero}"

    @property
    def numero(self):
        """A OC tem o mesmo número da cotação (e, portanto, do pedido de origem)."""
        return self.cotacao.numero or self.pk

    @property
    def aberta(self):
        return self.status in (StatusOrdem.ABERTA, StatusOrdem.PARCIALMENTE_RECEBIDA)


class NotaFiscal(TenantModel):
    ordem = models.ForeignKey(OrdemCompra, on_delete=models.PROTECT, related_name="notas")
    chave_acesso = models.CharField(max_length=44, unique=True, null=True, blank=True)
    numero = models.CharField(max_length=20, blank=True)
    emitente_cnpj = models.CharField(max_length=14, blank=True)
    destinatario_cnpj = models.CharField(max_length=14, blank=True)
    data_emissao = models.DateField(null=True, blank=True)
    valor_produtos = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    arquivo_xml = models.FileField(
        upload_to="nfe/", storage=armazenamento_privado, null=True, blank=True
    )
    registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "nota fiscal"
        verbose_name_plural = "notas fiscais"
        ordering = ["-criada_em"]

    def __str__(self):
        return f"NF {self.numero or self.chave_acesso}"


class Recebimento(TenantModel):
    ordem = models.ForeignKey(OrdemCompra, on_delete=models.PROTECT, related_name="recebimentos")
    nota = models.ForeignKey(
        NotaFiscal, on_delete=models.PROTECT, null=True, blank=True, related_name="recebimentos"
    )
    conferido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    observacao = models.CharField(max_length=300, blank=True)
    confirmado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "recebimento"
        verbose_name_plural = "recebimentos"
        ordering = ["-confirmado_em"]


class ItemRecebimento(models.Model):
    recebimento = models.ForeignKey(Recebimento, on_delete=models.CASCADE, related_name="itens")
    item_cotacao = models.ForeignKey(
        ItemCotacao, on_delete=models.PROTECT, related_name="recebimentos"
    )
    quantidade_nota = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    quantidade_conferida = models.DecimalField(max_digits=12, decimal_places=3)


class CorrespondenciaProdutoFornecedor(TenantModel):
    """Código do produto no fornecedor -> produto do catálogo; aprendida na primeira nota."""

    fornecedor = models.ForeignKey(
        "cadastros.Fornecedor", on_delete=models.CASCADE, related_name="correspondencias"
    )
    codigo_fornecedor = models.CharField(max_length=60)
    produto = models.ForeignKey(
        "cadastros.Produto", on_delete=models.CASCADE, related_name="correspondencias"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "fornecedor", "codigo_fornecedor"],
                name="correspondencia_unica_por_fornecedor",
            ),
        ]
