from django.conf import settings
from django.db import models

from cadastros.models import Produto
from tenancy.models import Almoxarifado, CentroCusto, MotivoRequisicao, Subcentro


class StatusPedido(models.TextChoices):
    RASCUNHO = "RASCUNHO", "Rascunho"
    ENVIADO = "ENVIADO", "Aguardando aprovação"
    APROVADO_UNIDADE = "APROVADO_UNIDADE", "Aprovado — aguardando suprimentos"
    RECUSADO = "RECUSADO", "Recusado"
    EM_SEPARACAO = "EM_SEPARACAO", "Em separação"
    ENTREGUE = "ENTREGUE", "Entregue"
    CONCLUIDO = "CONCLUIDO", "Concluído"


class OrigemAtendimento(models.TextChoices):
    ESTOQUE = "ESTOQUE", "Estoque"
    COMPRA = "COMPRA", "Compra"


class Pedido(models.Model):
    requisitante = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pedidos"
    )
    almoxarifado_destino = models.ForeignKey(
        Almoxarifado, on_delete=models.PROTECT, related_name="pedidos"
    )
    centro_custo = models.ForeignKey(CentroCusto, on_delete=models.PROTECT, related_name="pedidos")
    subcentro = models.ForeignKey(
        Subcentro, on_delete=models.PROTECT, null=True, blank=True, related_name="pedidos"
    )
    motivo = models.ForeignKey(MotivoRequisicao, on_delete=models.PROTECT, related_name="pedidos")
    status = models.CharField(
        max_length=20, choices=StatusPedido.choices, default=StatusPedido.RASCUNHO
    )
    observacao = models.CharField(max_length=500, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["status", "ativo"]),
            models.Index(fields=["requisitante", "status"]),
        ]

    def __str__(self):
        return f"Pedido #{self.pk} — {self.requisitante} → {self.almoxarifado_destino}"

    @property
    def filial(self):
        return self.almoxarifado_destino.filial


class ItemPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="itens_pedido")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    origem_atendimento = models.CharField(
        max_length=10, choices=OrigemAtendimento.choices, null=True, blank=True
    )
    quantidade_entregue = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    observacao = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "item do pedido"
        verbose_name_plural = "itens do pedido"
        constraints = [
            models.UniqueConstraint(fields=["pedido", "produto"], name="produto_unico_por_pedido"),
            models.CheckConstraint(
                condition=models.Q(quantidade__gt=0), name="item_quantidade_positiva"
            ),
        ]

    def __str__(self):
        return f"{self.quantidade} × {self.produto}"


class EventoPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="eventos")
    de_status = models.CharField(max_length=20, choices=StatusPedido.choices, blank=True)
    para_status = models.CharField(max_length=20, choices=StatusPedido.choices)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="eventos_pedido"
    )
    observacao = models.CharField(max_length=500, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "evento do pedido"
        verbose_name_plural = "eventos do pedido"
        ordering = ["criado_em"]

    def __str__(self):
        return f"#{self.pedido_id}: {self.de_status or '—'} → {self.para_status}"


class AprovacaoLider(models.Model):
    """Voto de aprovação de um líder de setor. No modo TODOS_LIDERES o pedido só avança
    quando todos os líderes ativos do setor tiverem registrado o seu."""

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="aprovacoes_lider")
    lider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="aprovacoes_como_lider"
    )
    aprovado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "aprovação de líder"
        verbose_name_plural = "aprovações de líder"
        ordering = ["aprovado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["pedido", "lider"], name="aprovacao_lider_unica_por_pedido"
            ),
        ]

    def __str__(self):
        return f"#{self.pedido_id} aprovado por {self.lider}"
