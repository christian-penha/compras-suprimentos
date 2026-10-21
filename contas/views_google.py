"""
Login com Google (OAuth2 / OpenID Connect) via Authlib.

Regra de negócio: só entra quem JÁ tem usuário, casando pelo e-mail verificado do
Google. O callback nunca cria usuário: tenant, filial, setor e papéis são decididos
por um administrador, não automaticamente. O login por usuário/senha continua
funcionando normalmente.
"""
import logging

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

SESSION_NEXT = "google_login_next"
MSG_ERRO_GENERICO = "Não foi possível entrar com o Google. Tente novamente ou use usuário e senha."

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile", "timeout": 10},
)


def google_configurado():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _para_login(request, mensagem):
    messages.error(request, mensagem)
    return redirect("login")


def _next_seguro(request, destino):
    return bool(destino) and url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def login_google(request):
    """Inicia o fluxo: redireciona para o Google (state e nonce gerados pelo Authlib)."""
    if not google_configurado():
        return _para_login(request, "Login com Google não está configurado neste ambiente.")
    proximo = request.GET.get("next", "")
    if _next_seguro(request, proximo):
        request.session[SESSION_NEXT] = proximo
    else:
        request.session.pop(SESSION_NEXT, None)
    redirect_uri = request.build_absolute_uri(reverse("auth_google_callback"))
    return oauth.google.authorize_redirect(request, redirect_uri)


def auth_google_callback(request):
    """Recebe o retorno do Google, valida o ID token e abre a sessão."""
    if not google_configurado():
        return _para_login(request, "Login com Google não está configurado neste ambiente.")

    if request.GET.get("error") == "access_denied":
        return _para_login(request, "Login com Google cancelado.")

    try:
        # Troca o code pelo token e valida state, nonce e assinatura do ID token.
        token = oauth.google.authorize_access_token(request)
        info = token.get("userinfo")
    except Exception:  # OAuthError, falha de rede, state inválido…
        logger.exception("Falha no callback do login com Google")
        return _para_login(request, MSG_ERRO_GENERICO)

    if not info:
        logger.error("Callback do Google sem userinfo no ID token")
        return _para_login(request, MSG_ERRO_GENERICO)

    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        return _para_login(request, "O e-mail da conta Google não está verificado.")

    dominios = settings.GOOGLE_ALLOWED_DOMAINS
    if dominios and email.rpartition("@")[2] not in dominios:
        return _para_login(request, "Este domínio de e-mail não tem permissão de acesso.")

    # Usuario.objects é global de propósito: o login precisa localizar o usuário antes
    # de existir um tenant corrente (o TenantMiddleware só age depois de autenticado).
    candidatos = list(get_user_model().objects.filter(email__iexact=email)[:2])
    if not candidatos:
        return _para_login(
            request,
            "Não há usuário cadastrado com este e-mail. Peça ao administrador para cadastrá-lo.",
        )
    if len(candidatos) > 1:
        logger.warning("Login Google recusado: e-mail duplicado entre usuários (%s)", email)
        return _para_login(request, "Há mais de um usuário com este e-mail. Procure o administrador.")

    user = candidatos[0]
    if not user.is_active or not user.ativo_no_sistema:
        return _para_login(request, "Este usuário está inativo. Procure o administrador.")

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    destino = request.session.pop(SESSION_NEXT, "")
    return redirect(destino if _next_seguro(request, destino) else settings.LOGIN_REDIRECT_URL)


class LoginComGoogleOpcional(LoginView):
    """Tela de login padrão (usuário/senha) + botão do Google quando configurado."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["google_login_ativo"] = google_configurado()
        return ctx
