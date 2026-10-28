from django import template

register = template.Library()

BADGES = {
    "RASCUNHO": "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-200",
    "ENVIADO": "bg-amber-50 text-amber-700 border border-amber-200 dark:text-amber-400 dark:bg-amber-950 dark:border-amber-800",
    "APROVADO_UNIDADE": "bg-blue-50 text-blue-700 border border-blue-200 dark:text-blue-400 dark:bg-blue-950 dark:border-blue-800",
    "RECUSADO": "bg-red-50 text-red-700 border border-red-200 dark:text-red-400 dark:bg-red-950 dark:border-red-800",
    "AGUARDANDO_COMPRA": "bg-orange-50 text-orange-700 border border-orange-200 dark:text-orange-400 dark:bg-orange-950 dark:border-orange-800",
    "EM_SEPARACAO": "bg-violet-50 text-violet-700 border border-violet-200 dark:text-violet-400 dark:bg-violet-950 dark:border-violet-800",
    "ENTREGA_PARCIAL": "bg-sky-50 text-sky-700 border border-sky-200 dark:text-sky-400 dark:bg-sky-950 dark:border-sky-800",
    "ENTREGUE": "bg-emerald-50 text-emerald-700 border border-emerald-200 dark:text-emerald-400 dark:bg-emerald-950 dark:border-emerald-800",
    "CONCLUIDO": "bg-emerald-100 text-emerald-800 dark:text-emerald-300 dark:bg-emerald-900/50",
    "EMITIDA": "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200",
    "COMPRA_EFETUADA": "bg-blue-50 text-blue-700 border border-blue-200 dark:text-blue-400 dark:bg-blue-950 dark:border-blue-800",
    "EM_TRANSITO": "bg-sky-50 text-sky-700 border border-sky-200 dark:text-sky-400 dark:bg-sky-950 dark:border-sky-800",
    "RECEBIDA": "bg-emerald-50 text-emerald-700 border border-emerald-200 dark:text-emerald-400 dark:bg-emerald-950 dark:border-emerald-800",
    "EM_COMPARACAO": "bg-violet-50 text-violet-700 border border-violet-200 dark:text-violet-400 dark:bg-violet-950 dark:border-violet-800",
    "AGUARDANDO_ALCADA": "bg-amber-50 text-amber-700 border border-amber-200 dark:text-amber-400 dark:bg-amber-950 dark:border-amber-800",
    "APROVADA": "bg-emerald-50 text-emerald-700 border border-emerald-200 dark:text-emerald-400 dark:bg-emerald-950 dark:border-emerald-800",
    "RECUSADA": "bg-red-50 text-red-700 border border-red-200 dark:text-red-400 dark:bg-red-950 dark:border-red-800",
    "CANCELADA": "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-200",
    "ABERTA": "bg-blue-50 text-blue-700 border border-blue-200 dark:text-blue-400 dark:bg-blue-950 dark:border-blue-800",
    "PARCIALMENTE_RECEBIDA": "bg-sky-50 text-sky-700 border border-sky-200 dark:text-sky-400 dark:bg-sky-950 dark:border-sky-800",
    "CONCLUIDA": "bg-emerald-100 text-emerald-800 dark:text-emerald-300 dark:bg-emerald-900/50",
}


EVENTOS = {
    "RASCUNHO": "Rascunho criado",
    "ENVIADO": "Enviado para aprovação",
    "APROVADO_UNIDADE": "Aprovado, enviado para suprimentos",
    "AGUARDANDO_COMPRA": "Enviado para compra",
    "RECUSADO": "Recusado",
    "EM_SEPARACAO": "Separação iniciada",
    "ENTREGA_PARCIAL": "Entregue parcialmente",
    "ENTREGUE": "Entregue",
    "CONCLUIDO": "Concluído",
}


@register.filter
def evento_rotulo(status):
    """Histórico conta o que aconteceu (ação), não o estado em que o pedido ficou."""
    return EVENTOS.get(status, status)


@register.filter
def badge_status(status):
    return BADGES.get(status, "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-200")


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
