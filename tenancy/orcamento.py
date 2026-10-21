"""Orçamento dos centros de custo.

Dois modos: valor TOTAL dividido em N meses (a última parcela absorve os centavos que
sobram, para a soma fechar exatamente no total) ou valor MENSAL fixo. O "consumido" é o
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


def orcamento_do_mes(centro, ano, mes):
    """Orçamento do centro para o mês, ou None se não há orçamento nele (sem orçamento,
    ou mês fora do período do valor total)."""
    if centro.orcamento_modo == ModoOrcamento.MENSAL and centro.orcamento_valor is not None:
        return centro.orcamento_valor
    if (
        centro.orcamento_modo == ModoOrcamento.TOTAL_RATEADO
        and centro.orcamento_valor is not None
        and centro.orcamento_inicio and centro.orcamento_meses
    ):
        i = _indice_do_mes(centro.orcamento_inicio, ano, mes)
        if not 0 <= i < centro.orcamento_meses:
            return None
        parcela = (centro.orcamento_valor / centro.orcamento_meses).quantize(CENTAVOS, rounding=ROUND_DOWN)
        if i == centro.orcamento_meses - 1:
            return centro.orcamento_valor - parcela * (centro.orcamento_meses - 1)
        return parcela
    return None


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
