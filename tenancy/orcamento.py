"""Orçamento dos centros de custo.

Dois modos: valor TOTAL dividido em N meses (a última parcela absorve os centavos que
sobram, para a soma fechar exatamente no total) ou valor MENSAL. Em ambos, um mês pode ter valor
próprio (OrcamentoMes): no TOTAL, os meses sem valor próprio dividem o que sobra. O "consumido" é o
eixo consumo (RN-17): baixas de estoque dos pedidos, a custo médio, no mês.

`situacao_do_mes` e `excede_orcamento` existem para as regras de aprovação poderem usar o
orçamento (ex.: pedido que estoura o saldo do mês sobe de nível). Hoje nenhuma regra de
aprovação consulta estes valores — só as telas de configuração.
"""
from datetime import date
from decimal import ROUND_DOWN, Decimal

from .models import ModoOrcamento

CENTAVOS = Decimal("0.01")


def primeiro_dia(d):
    return date(d.year, d.month, 1)


def _indice_do_mes(inicio, ano, mes):
    return (ano - inicio.year) * 12 + (mes - inicio.month)


def meses_do_periodo(inicio, quantidade):
    """[(ano, mes)] a partir de `inicio` (dia 1) por `quantidade` meses."""
    return [((inicio.year * 12 + inicio.month - 1 + i) // 12, (inicio.year * 12 + inicio.month - 1 + i) % 12 + 1)
            for i in range(quantidade)]


def janela_de_meses(centro):
    """Meses que aparecem na grade mês a mês: o período do valor total, ou, no orçamento mensal,
    o período informado (sem período, nenhum)."""
    if centro.orcamento_modo in (ModoOrcamento.TOTAL_RATEADO, ModoOrcamento.MENSAL) \
            and centro.orcamento_inicio and centro.orcamento_meses:
        return meses_do_periodo(centro.orcamento_inicio, centro.orcamento_meses)
    return []


def valores_proprios(centro):
    """{date(ano, mes, 1): Decimal} dos meses com valor próprio já salvos."""
    if centro.pk is None:
        return {}
    return {o.ano_mes: o.valor for o in centro.orcamentos_mes.all()}


def parcelas_do_total(valor_total, meses, proprios):
    """{(ano, mes): valor} do orçamento TOTAL: meses com valor próprio ficam como informados e os
    demais dividem o que sobra do total (a última parcela absorve os centavos, e a soma fecha no
    total quando algum mês é livre)."""
    parcelas = {(a, m): proprios[date(a, m, 1)] for a, m in meses if date(a, m, 1) in proprios}
    livres = [am for am in meses if am not in parcelas]
    if livres:
        restante = max(valor_total - sum(parcelas.values(), Decimal("0")), Decimal("0"))
        base = (restante / len(livres)).quantize(CENTAVOS, rounding=ROUND_DOWN)
        for am in livres[:-1]:
            parcelas[am] = base
        parcelas[livres[-1]] = restante - base * (len(livres) - 1)
    return parcelas


def orcamento_do_mes(centro, ano, mes, proprios=None):
    """Orçamento do centro para o mês, ou None se não há orçamento nele (sem orçamento, ou mês
    fora do período do valor total). `proprios` permite simular valores ainda não salvos."""
    proprios = valores_proprios(centro) if proprios is None else proprios
    if centro.orcamento_modo == ModoOrcamento.MENSAL and centro.orcamento_valor is not None:
        return proprios.get(date(ano, mes, 1), centro.orcamento_valor)
    if (
        centro.orcamento_modo == ModoOrcamento.TOTAL_RATEADO
        and centro.orcamento_valor is not None
        and centro.orcamento_inicio and centro.orcamento_meses
    ):
        meses = meses_do_periodo(centro.orcamento_inicio, centro.orcamento_meses)
        if (ano, mes) not in meses:
            return None
        return parcelas_do_total(centro.orcamento_valor, meses, proprios)[(ano, mes)]
    return None


def grade_mes_a_mes(centro, proprios=None):
    """Linhas da grade mês a mês: [{'ano','mes','valor','proprio'}] (valor = o efetivo do mês)."""
    proprios = valores_proprios(centro) if proprios is None else proprios
    return [
        {"ano": a, "mes": m, "valor": orcamento_do_mes(centro, a, m, proprios), "proprio": date(a, m, 1) in proprios}
        for a, m in janela_de_meses(centro)
    ]


def consumo_do_mes(centro, ano, mes):
    from estoque.models import Movimentacao, TipoMovimentacao

    movs = Movimentacao.objects.filter(
        centro_custo=centro, pedido__isnull=False,
        tipo__in=[TipoMovimentacao.SAIDA, TipoMovimentacao.TRANSFERENCIA],
        criado_em__year=ano, criado_em__month=mes,
    )
    return sum((m.quantidade * m.valor_unitario for m in movs), Decimal("0"))


def situacao_do_mes(centro, ano=None, mes=None):
    """{'orcamento', 'consumido', 'saldo', 'percentual'} do mês; None se não há orçamento."""
    hoje = date.today()
    ano, mes = ano or hoje.year, mes or hoje.month
    orcamento = orcamento_do_mes(centro, ano, mes)
    if orcamento is None:
        return None
    consumido = consumo_do_mes(centro, ano, mes)
    percentual = int(consumido * 100 / orcamento) if orcamento > 0 else (100 if consumido > 0 else 0)
    return {"orcamento": orcamento, "consumido": consumido, "saldo": orcamento - consumido, "percentual": percentual}


def excede_orcamento(centro, valor, ano=None, mes=None):
    """True se somar `valor` ao consumo do mês passa do orçamento. False se não há orçamento."""
    situacao = situacao_do_mes(centro, ano, mes)
    return situacao is not None and situacao["consumido"] + Decimal(valor) > situacao["orcamento"]
