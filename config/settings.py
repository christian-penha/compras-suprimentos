from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
# O healthcheck do container chama o app por localhost.
for _h in ("localhost", "127.0.0.1"):
    if _h not in ALLOWED_HOSTS and "*" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_h)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tenancy",
    "contas",
    "cadastros",
    "estoque",
    "pedidos",
    "compras",
    "configuracoes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tenancy.middleware.TenantMiddleware",
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
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pedidos.context_processors.carrinho",
                "contas.context_processors.menu",
                "config.context_processors.asset_versao",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}

AUTH_USER_MODEL = "contas.Usuario"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Bahia"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))
# Fora de MEDIA_ROOT: o Django serve MEDIA_ROOT em DEBUG, e XML de nota fiscal não pode ser público.
PRIVATE_ROOT = env("PRIVATE_ROOT", default=str(BASE_DIR / "privado"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Login com Google (OAuth2/OpenID Connect via Authlib). Sem CLIENT_ID/SECRET o botão
# não aparece e as rotas respondem com aviso.
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
# Domínios permitidos (ex.: colegioperfil.com.br). Vazio = qualquer conta Google já cadastrada.
GOOGLE_ALLOWED_DOMAINS = [
    d.strip().lower() for d in env.list("GOOGLE_ALLOWED_DOMAINS", default=[]) if d.strip()
]

# Cookies não isolam por porta: sem nomes próprios, outro app Django em localhost
# (ex.: Diário de Bordo) sobrescreve a sessão e o CSRF deste.
SESSION_COOKIE_NAME = "compras_sessionid"
CSRF_COOKIE_NAME = "compras_csrftoken"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "painel"
LOGOUT_REDIRECT_URL = "login"


# ---------------------------------------------------------------------------
# Produção (Docker/Portainer)
# ---------------------------------------------------------------------------
# Estáticos servidos pelo próprio app (WhiteNoise), com hash no nome do arquivo.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Imagens de produto (MEDIA_ROOT) são servidas pelo app quando não há nginx na frente.
# O XML de nota fiscal fica em PRIVATE_ROOT e nunca passa por aqui.
SERVIR_MEDIA = env.bool("SERVIR_MEDIA", default=True)

# Origens confiáveis para POST (formulários) atrás de proxy/HTTPS.
# Ex.: CSRF_TRUSTED_ORIGINS=https://compras.exemplo.com.br,http://192.168.0.10:8001
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Defina HTTPS=True só quando o acesso for por HTTPS (proxy reverso com certificado).
# Em HTTP puro (rede interna), deixe False, senão o login deixa de funcionar.
HTTPS = env.bool("HTTPS", default=False)
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_REDIRECT_EXEMPT = [r"^saude/$"]
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
