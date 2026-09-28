from django.db import models

from apps.core.models import TenantModel


class CategoriaProduto(TenantModel):
    """Grupo → Categoria → Tipo, self-FK de 3 níveis."""

    nome = models.CharField(max_length=80)
    categoria_pai = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="subcategorias"
    )

    class Meta:
        verbose_name_plural = "categorias de produto"

    def __str__(self) -> str:
        return self.nome


class Produto(TenantModel):
    """
    UM produto por item real de consumo — nunca duplicar por unidade de
    compra (caixa e resma são o MESMO produto; ver docs/07-Modelo-de-Dados.html
    seção 2).

    unidade_base = menor unidade de consumo (a que aparece no saldo e no
    custo médio). fator_embalagem_compra = quantas unidades_base cabem na
    embalagem usada para comprar (ex. caixa de papel A4 = 10 resmas). Valor 1
    quando não há distinção — decisão de trabalhar só com unidade por ora,
    mas o campo fica pronto; não expor a conversão na tela até haver caso
    real.
    """

    codigo_interno = models.CharField(max_length=30, unique=True)
    nome = models.CharField(max_length=200)
    especificacao = models.TextField(
        help_text=(
            "Obrigatório. Cor, gramatura etc. — elimina o 'pedido vago'. "
            "O requisitante escolhe o item já especificado, nunca digita "
            "livre no pedido (exceto via ItemLivreRequisicao, fora do "
            "escopo da Fase 0)."
        )
    )
    categoria = models.ForeignKey(CategoriaProduto, on_delete=models.PROTECT, related_name="produtos")
    unidade_base = models.CharField(max_length=10)  # UN, RESMA, LT, KG...
    fator_embalagem_compra = models.PositiveIntegerField(default=1)
    unidade_embalagem_compra = models.CharField(max_length=10, blank=True)  # "CX", "FD"...
    reutilizavel = models.BooleanField(
        default=False,
        help_text=(
            "Flag informativa apenas. Controle de devolução está fora do "
            "MVP (ver docs/06-Glossario-e-Fora-de-Escopo.html) — não "
            "bloqueia conclusão de pedido."
        ),
    )
    fornecedor_padrao = models.ForeignKey(
        "compras.Fornecedor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="produtos_padrao",
    )
    categoria_investimento = models.BooleanField(
        default=False,
        help_text=(
            "Bens duráveis (notebook, mobiliário). Separa relatório de "
            "consumo de gasto operacional. Sem controle patrimonial no MVP."
        ),
    )
    estoque_minimo = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    ponto_reposicao = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    ativo = models.BooleanField(default=True)

    aprovado_por_suprimentos = models.BooleanField(
        default=True,
        help_text=(
            "False apenas para produtos criados a partir de "
            "ItemLivreRequisicao ainda não confirmados por suprimentos. "
            "Produto assim não pode ser movimentado em estoque nem comprado."
        ),
    )

    def __str__(self) -> str:
        return self.nome


class KitRequisicao(TenantModel):
    """
    Modelo de requisição, NÃO produto físico: lista salva que explode em
    itens, não produto montado com saldo próprio — evita saldo duplicado e
    movimentação de montagem que ninguém pediu.
    """

    nome = models.CharField(max_length=120)  # "Kit Professor", "Kit Gráfica"
    ativo = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.nome


class ItemKitRequisicao(models.Model):
    kit = models.ForeignKey(KitRequisicao, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade_sugerida = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        unique_together = [("kit", "produto")]
