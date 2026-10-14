from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Papel


def requer_papel(*papeis, modulo=None):
    def decorador(view):
        @wraps(view)
        @login_required
        def interna(request, *args, **kwargs):
            usuario = request.user
            if usuario.is_superuser or usuario.tem_papel(Papel.SUPERADMIN):
                return view(request, *args, **kwargs)
            modulos = modulo if isinstance(modulo, (list, tuple)) else [modulo]
            if any(usuario.tem_papel(p, m) for p in papeis for m in modulos):
                return view(request, *args, **kwargs)
            raise PermissionDenied
        return interna
    return decorador
