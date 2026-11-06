"""Vínculo manual entre produtos similares (substitutos quando falta estoque)."""
from django.db.models import Q

from .models import Produto, ProdutoSimilar


class SimilarInvalido(Exception):
    pass


def _par(a, b):
    return (a, b) if a.pk < b.pk else (b, a)


def vincular(a, b, usuario=None):
    if a.pk == b.pk:
        raise SimilarInvalido("Um produto não pode ser similar de si mesmo.")
    menor, maior = _par(a, b)
    vinculo, criado = ProdutoSimilar.objects.get_or_create(
        produto=menor, similar=maior, defaults={"criado_por": usuario}
    )
    if not criado:
        raise SimilarInvalido("Esses produtos já estão vinculados.")
    return vinculo


def desvincular(a, b):
    menor, maior = _par(a, b)
    ProdutoSimilar.objects.filter(produto=menor, similar=maior).delete()


def similares_de(produto, apenas_ativos=True):
    """Produtos vinculados a `produto` (nos dois sentidos do par)."""
    ids = set(ProdutoSimilar.objects.filter(produto=produto).values_list("similar_id", flat=True))
    ids |= set(ProdutoSimilar.objects.filter(similar=produto).values_list("produto_id", flat=True))
    produtos = Produto.objects.filter(pk__in=ids).select_related("categoria")
    return produtos.filter(ativo=True) if apenas_ativos else produtos
