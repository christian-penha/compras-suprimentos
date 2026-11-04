"""Previsão financeira dos centros de custo (eixo desembolso).

O gasto de um mês é o valor das cotações aprovadas naquele mês, no centro e nas subcontas
dele (frete não entra: não tem centro). No orçamento TOTAL o limite dos meses seguintes é
recalculado quando um mês estoura: R$ 1.200 em 12 meses = R$ 100/mês; gastou R$ 200 no 8º
(tendo gasto R$ 100 até o 7º), o saldo de R$ 300 se divide nos 4 meses restantes = R$ 75.
Mês que gastou menos que o limite não devolve saldo aos seguintes (só o estouro redistribui).
"""
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum

from .models import ModoOrcamento
from .orcamento import CENTAVOS


def _ids_do_centro(centro):
    return [centro.pk, *centro.subcontas.values_list("pk", flat=True)]


def gasto_do_mes(centro, ano, mes):
    from compras.models import ItemCotacao, StatusCotacao

    total = ItemCotacao.objects.filter(
        cotacao__status=StatusCotacao.APROVADA, centro_custo_id__in=_ids_do_centro(centro),
        cotacao__decidida_em__year=ano, cotacao__decidida_em__month=mes,
    ).aggregate(
        t=Sum(ExpressionWrapper(F("quantidade") * F("valor_unitario"), output_field=DecimalField()))
    )["t"]
    return total or Decimal("0")


def _meses(inicio, n):
    for i in range(n):
        m = inicio.month - 1 + i
        yield inicio.year + m // 12, m % 12 + 1


def plano(centro, hoje=None):
    """Mês a mês: [{'ano','mes','base','limite','gasto'}]. Vazio se não há orçamento."""
    hoje = hoje or date.today()
    if centro.orcamento_valor is None:
        return []
    if centro.orcamento_modo == ModoOrcamento.MENSAL:
        return [
            {"ano": hoje.year, "mes": hoje.month, "base": centro.orcamento_valor,
             "limite": centro.orcamento_valor, "gasto": gasto_do_mes(centro, hoje.year, hoje.month)}
        ]
    if centro.orcamento_modo != ModoOrcamento.TOTAL_RATEADO or not (centro.orcamento_inicio and centro.orcamento_meses):
        return []
    n, total = centro.orcamento_meses, centro.orcamento_valor
    base = (total / n).quantize(CENTAVOS, rounding=ROUND_DOWN)
    linhas, ja_gasto = [], Decimal("0")
    for i, (ano, mes) in enumerate(_meses(centro.orcamento_inicio, n)):
        restante = n - i
        limite = max(total - ja_gasto, Decimal("0"))
        limite = limite if restante == 1 else (limite / restante).quantize(CENTAVOS, rounding=ROUND_DOWN)
        gasto = gasto_do_mes(centro, ano, mes) if (ano, mes) <= (hoje.year, hoje.month) else Decimal("0")
        linhas.append({"ano": ano, "mes": mes, "base": base, "limite": limite, "gasto": gasto})
        ja_gasto += max(gasto, limite)
    return linhas


def limite_do_mes(centro, ano, mes):
    """Limite do mês já recalculado; None se não há orçamento nele."""
    return next((l["limite"] for l in plano(centro) if (l["ano"], l["mes"]) == (ano, mes)), None)


def avisos_de_estouro(pares, hoje=None):
    """`pares`: [(centro, valor)] de uma compra. Textos dos centros (e contas pai) cujo limite do
    mês passa a ser ultrapassado com ela; vazio se tudo cabe."""
    hoje = hoje or date.today()
    por_centro = {}
    for centro, valor in pares:
        if centro is None:
            continue
        por_centro[centro.pk] = (centro, por_centro.get(centro.pk, (centro, 0))[1] + valor)
        if centro.pai_id:
            pai = centro.pai
            por_centro[pai.pk] = (pai, por_centro.get(pai.pk, (pai, 0))[1] + valor)
    avisos = []
    for centro, valor in por_centro.values():
        limite = limite_do_mes(centro, hoje.year, hoje.month)
        if limite is None:
            continue
        previsto = gasto_do_mes(centro, hoje.year, hoje.month) + Decimal(valor)
        if previsto > limite:
            avisos.append(f"{centro}: limite do mês R$ {limite:.2f}, com esta compra R$ {previsto:.2f}")
    return avisos
