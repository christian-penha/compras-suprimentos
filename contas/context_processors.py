from .models import Modulo, Papel


def menu(request):
    """Flags de visibilidade do menu (a autorização real continua nas views)."""
    u = request.user
    if not u.is_authenticated:
        return {}
    superior = u.is_superuser or u.tem_papel(Papel.SUPERADMIN)
    return {
        "menu_compras": superior or u.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS),
        "menu_estoque": superior or u.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE),
        "menu_aprova_compras": u.alcadas_que_aprova.exists(),
    }
