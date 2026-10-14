from django import template

register = template.Library()

BADGES = {
    "RASCUNHO": "bg-slate-100 text-slate-600",
    "ENVIADO": "bg-amber-50 text-amber-700 border border-amber-200",
    "APROVADO_UNIDADE": "bg-blue-50 text-blue-700 border border-blue-200",
    "RECUSADO": "bg-red-50 text-red-700 border border-red-200",
    "AGUARDANDO_COMPRA": "bg-orange-50 text-orange-700 border border-orange-200",
    "EM_SEPARACAO": "bg-violet-50 text-violet-700 border border-violet-200",
    "ENTREGA_PARCIAL": "bg-sky-50 text-sky-700 border border-sky-200",
    "ENTREGUE": "bg-emerald-50 text-emerald-700 border border-emerald-200",
    "CONCLUIDO": "bg-emerald-100 text-emerald-800",
    "EMITIDA": "bg-slate-100 text-slate-700",
    "COMPRA_EFETUADA": "bg-blue-50 text-blue-700 border border-blue-200",
    "EM_TRANSITO": "bg-sky-50 text-sky-700 border border-sky-200",
    "RECEBIDA": "bg-emerald-50 text-emerald-700 border border-emerald-200",
    "EM_COMPARACAO": "bg-violet-50 text-violet-700 border border-violet-200",
    "AGUARDANDO_ALCADA": "bg-amber-50 text-amber-700 border border-amber-200",
    "APROVADA": "bg-emerald-50 text-emerald-700 border border-emerald-200",
    "RECUSADA": "bg-red-50 text-red-700 border border-red-200",
    "CANCELADA": "bg-slate-100 text-slate-600",
    "ABERTA": "bg-blue-50 text-blue-700 border border-blue-200",
    "PARCIALMENTE_RECEBIDA": "bg-sky-50 text-sky-700 border border-sky-200",
    "CONCLUIDA": "bg-emerald-100 text-emerald-800",
}


@register.filter
def badge_status(status):
    return BADGES.get(status, "bg-slate-100 text-slate-600")


@register.inclusion_tag("_paginacao.html", takes_context=True)
def paginacao(context, pagina, rotulo="registros"):
    """Navegação numerada (1 … 4 5 [6] 7 8 … 40), preservando os filtros da URL."""
    from urllib.parse import urlencode

    base = {k: v for k, v in context["request"].GET.items() if v and k != "pagina"}
    prefixo = urlencode(base)
    prefixo = prefixo + "&" if prefixo else ""
    paginator = pagina.paginator
    itens = []
    for n in paginator.get_elided_page_range(pagina.number, on_each_side=2, on_ends=1):
        itens.append({"n": n, "atual": n == pagina.number, "gap": n == paginator.ELLIPSIS})
    return {
        "pagina": pagina, "itens": itens, "prefixo": prefixo, "rotulo": rotulo,
        "total": paginator.count, "paginas": paginator.num_pages,
    }


@register.filter
def opcoes_estoque(texto):
    """'|Todos,com|Com estoque' -> [('', 'Todos'), ('com', 'Com estoque')]"""
    return [tuple(p.split("|", 1)) for p in texto.split(",")]
