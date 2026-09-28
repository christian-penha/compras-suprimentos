"""
Calendário de dias úteis — usado em todo cálculo de SLA (3/7 dias úteis de
atendimento, prazo de confirmação de entrega, regularização de urgência) e
de alçada.

Feriados são dados de configuração (nacional + recesso próprio do grupo),
não lógica — ficam num model (`Feriado`) para o admin/superadmin manter sem
deploy, em vez de uma lista hardcoded no código (ver "Definição de pronto
para a Fase 0": calendário carregado com feriados nacionais e o calendário
do grupo).
"""
from __future__ import annotations

import datetime


def eh_dia_util(data: datetime.date, feriados: set[datetime.date]) -> bool:
    return data.weekday() < 5 and data not in feriados


def somar_dias_uteis(
    data_inicial: datetime.date, quantidade: int, feriados: set[datetime.date]
) -> datetime.date:
    """Soma `quantidade` dias úteis a `data_inicial`. `quantidade` negativo
    anda para trás (não usado hoje, mas mantém a função simétrica)."""
    passo = 1 if quantidade >= 0 else -1
    restante = abs(quantidade)
    data = data_inicial
    while restante > 0:
        data += datetime.timedelta(days=passo)
        if eh_dia_util(data, feriados):
            restante -= 1
    return data


def dias_uteis_entre(
    data_inicial: datetime.date, data_final: datetime.date, feriados: set[datetime.date]
) -> int:
    """Conta dias úteis estritamente entre duas datas (exclusive-exclusive),
    usado para medir SLA decorrido. Retorna 0 se data_final <= data_inicial."""
    if data_final <= data_inicial:
        return 0
    total = 0
    data = data_inicial
    while data < data_final:
        data += datetime.timedelta(days=1)
        if eh_dia_util(data, feriados):
            total += 1
    return total
