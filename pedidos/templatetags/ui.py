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
