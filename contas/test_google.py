from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from tenancy.testing import TenantTestCase

Usuario = get_user_model()

CONFIG = dict(GOOGLE_CLIENT_ID="id-teste", GOOGLE_CLIENT_SECRET="segredo-teste", GOOGLE_ALLOWED_DOMAINS=[])


def _token(email="ana@colegioperfil.com.br", verificado=True):
    return {"userinfo": {"email": email, "email_verified": verificado, "sub": "123", "name": "Ana"}}


@override_settings(**CONFIG)
class LoginGoogleTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@colegioperfil.com.br", password="x", tenant=self.tenant
        )

    def _callback(self, token):
        patcher = mock.patch(
            "contas.views_google.oauth.google.authorize_access_token", return_value=token
        )
        with patcher:
            return self.client.get(reverse("auth_google_callback"))

    def test_botao_aparece_so_quando_configurado(self):
        self.assertContains(self.client.get(reverse("login")), "Entrar com o Google")
        with override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET=""):
            self.assertNotContains(self.client.get(reverse("login")), "Entrar com o Google")

    def test_usuario_existente_entra(self):
        resposta = self._callback(_token())
        self.assertRedirects(resposta, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.usuario.pk)

    def test_email_case_insensitive(self):
        resposta = self._callback(_token("ANA@ColegioPerfil.com.br"))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_email_desconhecido_nao_cria_usuario(self):
        antes = Usuario.objects.count()
        resposta = self._callback(_token("intruso@gmail.com"))
        self.assertRedirects(resposta, reverse("login"), fetch_redirect_response=False)
        self.assertEqual(Usuario.objects.count(), antes)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_email_nao_verificado_recusado(self):
        self._callback(_token(verificado=False))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dominio_nao_permitido_recusado(self):
        with override_settings(GOOGLE_ALLOWED_DOMAINS=["outraescola.com.br"]):
            self._callback(_token())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_usuario_inativo_recusado(self):
        self.usuario.is_active = False
        self.usuario.save()
        self._callback(_token())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_usuario_desativado_no_sistema_recusado(self):
        self.usuario.ativo_no_sistema = False
        self.usuario.save()
        self._callback(_token())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_email_duplicado_recusado(self):
        Usuario.objects.create_user(
            username="ana2", email="ana@colegioperfil.com.br", password="x", tenant=self.tenant
        )
        self._callback(_token())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_falha_na_troca_do_token_volta_ao_login(self):
        with mock.patch(
            "contas.views_google.oauth.google.authorize_access_token", side_effect=Exception("state inválido")
        ):
            resposta = self.client.get(reverse("auth_google_callback"))
        self.assertRedirects(resposta, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_next_externo_e_ignorado(self):
        sessao = self.client.session
        sessao["google_login_next"] = "https://site-malicioso.com/"
        sessao.save()
        resposta = self._callback(_token())
        self.assertEqual(resposta["Location"], "/")

    def test_sem_configuracao_nao_inicia_fluxo(self):
        with override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET=""):
            resposta = self.client.get(reverse("auth_google_login"))
        self.assertRedirects(resposta, reverse("login"), fetch_redirect_response=False)
