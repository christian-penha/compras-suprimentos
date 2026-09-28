"""
Settings base — compartilhado por dev e prod.

Segredos NUNCA aqui (princípio "Segredos fora do settings.py desde o dia 1",
ver docs/08-Prompt-Inicial-do-Projeto.html). Tudo sensível vem de variável de
ambiente, carregada via python-dotenv em desenvolvimento e pelo processo do
servidor em produção.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    # apps do domínio
    "apps.core",
    "apps.tenancy",
    "apps.cadastros",
    "apps.aprovacao",
    "apps.requisicoes",
    "apps.estoque",
    "apps.compras",
    "apps.relatorios",
    "apps.auditoria",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Resolve o tenant do usuário autenticado e o disponibiliza via contextvar
    # para o TenantAwareManager. Deve vir depois de AuthenticationMiddleware
    # (precisa de request.user) e antes de qualquer middleware que acesse o ORM.
    "apps.core.middleware.TenantMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "compras_suprimentos"),
        "USER": os.environ.get("DB_USER", "compras_suprimentos"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "cadastros:produto_lista"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Anexos e XMLs de nota fiscal: fora do webroot, servidos por view autenticada
# e escopada (ver apps/compras). MEDIA_ROOT aqui é o destino em disco; a
# ausência de MEDIA_URL público é proposital — não existe rota estática
# servindo este diretório diretamente (ver docs/08-Prompt-Inicial-do-Projeto.html).
MEDIA_ROOT = BASE_DIR / "media_privado"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "core.Usuario"

# IDs públicos como UUID, não sequenciais — aplicado por model conforme
# necessário (ver apps/core/models.py UUIDPublicIdMixin).
