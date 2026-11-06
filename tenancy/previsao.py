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
from .orcamento import CENTAVOS, janela_de_meses, meses_do_periodo, orcamento_do_mes, parcelas_do_total, valores_proprios


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


def plano(centro, hoje=None):
    """Mês a mês: [{'ano','mes','base','limite','gasto'}]. Vazio se não há orçamento.

    No orçamento TOTAL, mês com valor próprio mantém esse limite; os meses livres dividem o que
    sobra depois do que já foi consumido (o estouro de um mês redistribui para os livres seguintes).
    No MENSAL cada mês vale o valor próprio ou o padrão; sem período definido, só o mês atual."""
    hoje = hoje or date.today()
    if centro.orcamento_valor is None:
        return []
    proprios = valores_proprios(centro)

    def gasto(ano, mes):
        return gasto_do_mes(centro, ano, mes) if (ano, mes) <= (hoje.year, hoje.month) else Decimal("0")

    if centro.orcamento_modo == ModoOrcamento.MENSAL:
        meses = janela_de_meses(centro) or [(hoje.year, hoje.month)]
        return [
            {"ano": a, "mes": m, "base": centro.orcamento_valor,
             "limite": orcamento_do_mes(centro, a, m, proprios), "gasto": gasto(a, m)}
            for a, m in meses
        ]
    if centro.orcamento_modo != ModoOrcamento.TOTAL_RATEADO or not (centro.orcamento_inicio and centro.orcamento_meses):
        return []

    total = centro.orcamento_valor
    meses = meses_do_periodo(centro.orcamento_inicio, centro.orcamento_meses)
    base = parcelas_do_total(total, meses, proprios)
    linhas, ja_gasto = [], Decimal("0")
    for i, (ano, mes) in enumerate(meses):
        if date(ano, mes, 1) in proprios:
            limite = proprios[date(ano, mes, 1)]
        else:
            livres = [am for am in meses[i:] if date(am[0], am[1], 1) not in proprios]
            fixos_futuros = sum((proprios[date(a, m, 1)] for a, m in meses[i + 1:] if date(a, m, 1) in proprios), Decimal("0"))
            disponivel = max(total - ja_gasto - fixos_futuros, Decimal("0"))
            limite = disponivel if len(livres) == 1 else (disponivel / len(livres)).quantize(CENTAVOS, rounding=ROUND_DOWN)
        g = gasto(ano, mes)
        linhas.append({"ano": ano, "mes": mes, "base": base[(ano, mes)], "limite": limite, "gasto": g})
        ja_gasto += max(g, limite)
    return linhas


def limite_do_mes(centro, ano, mes):
    """Limite do mês já recalculado; None se não há orçamento nele."""
    if centro.orcamento_modo == ModoOrcamento.MENSAL:
        return orcamento_do_mes(centro, ano, mes)
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
