import os

from django.conf import settings


def asset_versao(request):
    """Versão do CSS = data de modificação do arquivo, para o navegador nunca usar uma cópia velha."""
    try:
        return {"asset_versao": int(os.path.getmtime(os.path.join(settings.BASE_DIR, "static", "css", "app.css")))}
    except OSError:
        return {"asset_versao": 0}
