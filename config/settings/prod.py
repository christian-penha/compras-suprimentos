import os

from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")

# Servidor interno, atrás de proxy reverso — ajustar quando a topologia real
# de deploy for definida (ver docs/01-Briefing.html, "Hospedagem").
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
