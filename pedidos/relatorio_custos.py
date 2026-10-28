"""Dados do relatório de custo por centro: os dois eixos (desembolso e consumo) em linhas
comparáveis, para filtrar por unidade, setor e centro e agrupar nos gráficos."""
from collections import OrderedDict
from decimal import Decimal

from compras.models import NotaFiscal
from estoque.models import Movimentacao, TipoMovimentacao

CENTAVOS = Decimal("0.01")


def linhas_consumo(inicio=None, fim=None):
    """Eixo consumo: baixas ligadas a pedido, a custo médio. Uma linha por movimentação."""
    movs = Movimentacao.objects.filter(
        pedido__isnull=False, tipo__in=[TipoMovimentacao.SAIDA, TipoMovimentacao.TRANSFERENCIA]
    ).select_related(
        "centro_custo__filial", "pedido__requisitante__setor", "pedido__almoxarifado_destino__filial"
    )
    if inicio:
        movs = movs.filter(criado_em__date__gte=inicio)
    if fim:
        movs = movs.filter(criado_em__date__lte=fim)
    return [
        {
            "centro": m.centro_custo,
            "filial": m.pedido.almoxarifado_destino.filial,
            "setor": m.pedido.requisitante.setor,
            "valor": m.quantidade * m.valor_unitario,
            "data": m.criado_em.date(),
        }
        for m in movs
    ]


def linhas_desembolso(inicio=None, fim=None):
    """Eixo desembolso: o valor de cada nota distribuído pelos itens da cotação (por pedido,
    para saber centro, unidade e setor), proporcionalmente ao subtotal de cada item."""
    notas = NotaFiscal.objects.select_related("ordem__cotacao")
    if inicio:
        notas = notas.filter(data_emissao__gte=inicio)
    if fim:
        notas = notas.filter(data_emissao__lte=fim)
    linhas = []
    for nota in notas:
        itens = list(
            nota.ordem.cotacao.itens.select_related(
                "centro_custo__filial", "item_pedido__pedido__requisitante__setor",
                "item_pedido__pedido__almoxarifado_destino__filial",
            )
        )
        base = sum((i.subtotal for i in itens), Decimal("0"))
        data = nota.data_emissao or nota.criada_em.date()
        if not itens or base == 0:
            linhas.append({"centro": None, "filial": None, "setor": None, "valor": nota.valor_total, "data": data})
            continue
        for i in itens:
            pedido = i.item_pedido.pedido
            linhas.append({
                "centro": i.centro_custo,
                "filial": pedido.almoxarifado_destino.filial,
                "setor": pedido.requisitante.setor,
                "valor": (nota.valor_total * i.subtotal / base).quantize(CENTAVOS),
                "data": data,
            })
    return linhas


def filtrar(linhas, filial_id="", setor_id="", centros=()):
    saida = []
    for l in linhas:
        if filial_id and (l["filial"] is None or str(l["filial"].pk) != filial_id):
            continue
        if setor_id and (l["setor"] is None or str(l["setor"].pk) != setor_id):
            continue
        if centros and (l["centro"] is None or l["centro"].pk not in centros):
            continue
        saida.append(l)
    return saida


def agrupar(linhas, chave, rotulo, limite=None):
    """[{"rotulo", "valor", "pct"}] em ordem decrescente; pct relativo ao maior (largura da barra)."""
    totais = OrderedDict()
    for l in linhas:
        k = chave(l)
        totais[k] = totais.get(k, Decimal("0")) + l["valor"]
    itens = sorted(((rotulo(k), v) for k, v in totais.items()), key=lambda x: -x[1])
    if limite and len(itens) > limite:
        resto = sum((v for _, v in itens[limite:]), Decimal("0"))
        itens = itens[:limite] + [("Outros", resto)]
    maior = max((v for _, v in itens), default=Decimal("0")) or Decimal("1")
    total = sum((v for _, v in itens), Decimal("0")) or Decimal("1")
    return [
        {"rotulo": r, "valor": v, "pct": int(v / maior * 100), "participacao": int(round(v / total * 100))}
        for r, v in itens
    ]


def serie_mensal(linhas_a, linhas_b, meses=6, hoje=None):
    """Últimos `meses` meses: [{"mes", "a", "b", "pa", "pb"}] com alturas relativas (0-100)."""
    from datetime import date

    hoje = hoje or date.today()
    chaves = []
    ano, mes = hoje.year, hoje.month
    for _ in range(meses):
        chaves.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    chaves.reverse()

    def soma(linhas, k):
        return sum((l["valor"] for l in linhas if (l["data"].year, l["data"].month) == k), Decimal("0"))

    serie = [{"mes": f"{m:02d}/{str(a)[2:]}", "a": soma(linhas_a, (a, m)), "b": soma(linhas_b, (a, m))} for a, m in chaves]
    maior = max((max(s["a"], s["b"]) for s in serie), default=Decimal("0")) or Decimal("1")
    for s in serie:
        s["pa"], s["pb"] = int(s["a"] / maior * 100), int(s["b"] / maior * 100)
    return serie
