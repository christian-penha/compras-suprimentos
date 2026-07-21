from django import template

register = template.Library()

BADGES = {
    "RASCUNHO": "bg-slate-100 text-slate-600",
    "ENVIADO": "bg-amber-50 text-amber-700 border border-amber-200",
    "APROVADO_UNIDADE": "bg-blue-50 text-blue-700 border border-blue-200",
    "RECUSADO": "bg-red-50 text-red-700 border border-red-200",
    "EM_SEPARACAO": "bg-violet-50 text-violet-700 border border-violet-200",
    "ENTREGUE": "bg-emerald-50 text-emerald-700 border border-emerald-200",
    "CONCLUIDO": "bg-emerald-100 text-emerald-800",
}


@register.filter
def badge_status(status):
    return BADGES.get(status, "bg-slate-100 text-slate-600")
