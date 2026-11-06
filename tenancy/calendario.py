"""Dias úteis, feriados e recessos.

Dia útil = segunda a sexta que não cai em nenhum PeriodoNaoUtil ativo (do grupo todo ou da
filial informada). Usado para calcular o prazo mínimo da data desejada de um pedido (SLA).
"""
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import ConfiguracaoSLA, PeriodoNaoUtil

# Recesso muito longo não é infinito: limita a busca por dia útil (evita laço sem fim).
LIMITE_DIAS = 800


def datas_nao_uteis(filial=None, inicio=None, fim=None):
    """Conjunto de datas sem trabalho dentro da janela [inicio, fim]."""
    inicio = inicio or timezone.localdate()
    fim = fim or inicio + timedelta(days=LIMITE_DIAS)
    periodos = PeriodoNaoUtil.objects.filter(ativo=True, inicio__lte=fim, fim__gte=inicio)
    periodos = periodos.filter(Q(filial__isnull=True) | Q(filial=filial)) if filial else periodos.filter(filial__isnull=True)
    datas = set()
    for p in periodos:
        d = max(p.inicio, inicio)
        while d <= min(p.fim, fim):
            datas.add(d)
            d += timedelta(days=1)
    return datas


def eh_dia_util(d, nao_uteis):
    return d.weekday() < 5 and d not in nao_uteis


def somar_dias_uteis(inicio, dias, nao_uteis):
    """Data após `dias` dias úteis a partir de `inicio` (o próprio dia de início não conta)."""
    d, restantes = inicio, dias
    for _ in range(LIMITE_DIAS):
        if restantes <= 0:
            return d
        d += timedelta(days=1)
        if eh_dia_util(d, nao_uteis):
            restantes -= 1
    return d


def prazos_sla(filial=None, hoje=None):
    """Datas mínimas aceitas para a data desejada: {'estoque': date, 'compra': date}."""
    hoje = hoje or timezone.localdate()
    sla = ConfiguracaoSLA.atual()
    nao_uteis = datas_nao_uteis(filial, hoje)
    return {
        "estoque": somar_dias_uteis(hoje, sla.prazo_estoque_dias, nao_uteis),
        "compra": somar_dias_uteis(hoje, sla.prazo_compra_dias, nao_uteis),
    }


def _pascoa(ano):
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def feriados_nacionais(ano):
    """[(data, nome)] dos feriados nacionais do ano, incluindo os móveis (Carnaval, Sexta-feira
    Santa e Corpus Christi, que dependem da Páscoa)."""
    p = _pascoa(ano)
    fixos = [
        (date(ano, 1, 1), "Confraternização Universal"), (date(ano, 4, 21), "Tiradentes"),
        (date(ano, 5, 1), "Dia do Trabalho"), (date(ano, 9, 7), "Independência do Brasil"),
        (date(ano, 10, 12), "Nossa Senhora Aparecida"), (date(ano, 11, 2), "Finados"),
        (date(ano, 11, 15), "Proclamação da República"), (date(ano, 11, 20), "Consciência Negra"),
        (date(ano, 12, 25), "Natal"),
    ]
    moveis = [
        (p - timedelta(days=48), "Carnaval (segunda)"), (p - timedelta(days=47), "Carnaval (terça)"),
        (p - timedelta(days=2), "Sexta-feira Santa"), (p + timedelta(days=60), "Corpus Christi"),
    ]
    return sorted(fixos + moveis)
