from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Papel


def requer_papel(*papeis):
    def decorador(view):
        @wraps(view)
        @login_required
        def interna(request, *args, **kwargs):
            usuario = request.user
            if usuario.is_superuser or usuario.tem_papel(Papel.SUPERADMIN):
                return view(request, *args, **kwargs)
            if any(usuario.tem_papel(p) for p in papeis):
                return view(request, *args, **kwargs)
            raise PermissionDenied
        return interna
    return decorador
