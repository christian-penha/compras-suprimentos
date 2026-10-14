from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError

from tenancy.testing import TenantTestCase

from .models import Alcada, AprovadorAlcada, Modulo, Papel, PapelUsuario

Usuario = get_user_model()


class CriacaoUsuarioPorPapelTest(TenantTestCase):
    def criar(self, username, papel, modulo=None):
        usuario = Usuario.objects.create_user(
            username=username, password="senha-forte-123", tenant=self.tenant
        )
        PapelUsuario.objects.create(usuario=usuario, papel=papel, modulo=modulo)
        return usuario

    def test_superadmin(self):
        usuario = self.criar("wilson", Papel.SUPERADMIN)
        self.assertTrue(usuario.e_superadmin)

    def test_administrador_de_modulo(self):
        usuario = self.criar("stela", Papel.ADMINISTRADOR, Modulo.ESTOQUE)
        self.assertTrue(usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE))
        self.assertFalse(usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.FINANCEIRO))

    def test_aprovador(self):
        usuario = self.criar("zeila", Papel.APROVADOR)
        self.assertTrue(usuario.tem_papel(Papel.APROVADOR))

    def test_solicitante(self):
        usuario = self.criar("karlysson", Papel.SOLICITANTE)
        self.assertTrue(usuario.tem_papel(Papel.SOLICITANTE))

    def test_usuario_acumula_papeis(self):
        usuario = self.criar("osvaldo", Papel.ADMINISTRADOR, Modulo.ESTOQUE)
        PapelUsuario.objects.create(usuario=usuario, papel=Papel.ADMINISTRADOR, modulo=Modulo.COMPRAS)
        PapelUsuario.objects.create(usuario=usuario, papel=Papel.APROVADOR)
        self.assertEqual(usuario.papeis.count(), 3)

    def test_administrador_sem_modulo_rejeitado(self):
        usuario = Usuario.objects.create_user(
            username="invalido", password="senha-forte-123", tenant=self.tenant
        )
        with self.assertRaises(IntegrityError):
            PapelUsuario.objects.create(usuario=usuario, papel=Papel.ADMINISTRADOR)

    def test_papel_duplicado_rejeitado(self):
        usuario = self.criar("dulce", Papel.APROVADOR)
        with self.assertRaises(IntegrityError):
            PapelUsuario.objects.create(usuario=usuario, papel=Papel.APROVADOR)


class AlcadasEditaveisTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        from tenancy.models import Filial

        self.cp = Filial.objects.get(sigla="CP")
        self.vc = Filial.objects.get(sigla="VC")
        self.admin = Usuario.objects.create_user("adm", tenant=self.tenant, password=None)

    def salvar(self, linhas, filial=None):
        from .alcadas import salvar_tabela

        return salvar_tabela(tenant=self.tenant, filial=filial, linhas=linhas, usuario=self.admin)

    def test_validacao_da_tabela(self):
        from .alcadas import TabelaInvalida, ler_linhas

        with self.assertRaises(TabelaInvalida):  # limites fora de ordem
            ler_linhas(["500", "100", ""], ["AUTOMATICA", "CFO", "CEO"])
        with self.assertRaises(TabelaInvalida):  # última faixa sem aprovador
            ler_linhas(["500", ""], ["DIRETORIA_UNIDADE", "AUTOMATICA"])
        with self.assertRaises(TabelaInvalida):
            ler_linhas([], [])
        linhas = ler_linhas(["1.000,00", ""], ["AUTOMATICA", "CEO"])  # formato brasileiro
        self.assertEqual(linhas[0][0], Decimal("1000.00"))
        self.assertIsNone(linhas[1][0])

    def test_salvar_cria_nova_versao_e_preserva_a_anterior(self):
        antiga = Alcada.objects.get(papel_aprovador="DIRETORIA_UNIDADE", filial__isnull=True, ativo=True)
        self.assertTrue(self.salvar([(Decimal("500.00"), "AUTOMATICA"), (None, "CEO")]))
        antiga.refresh_from_db()
        self.assertFalse(antiga.ativo)  # cotações antigas continuam apontando para ela
        ativas = Alcada.objects.filter(ativo=True, filial__isnull=True).order_by("valor_min")
        self.assertEqual([(a.valor_min, a.valor_max) for a in ativas],
                         [(Decimal("0.00"), Decimal("500.00")), (Decimal("500.01"), None)])

    def test_tabela_igual_nao_cria_versao(self):
        atual = [(a.valor_max, a.papel_aprovador)
                 for a in Alcada.objects.filter(ativo=True, filial__isnull=True).order_by("valor_min")]
        self.assertFalse(self.salvar(atual))

    def test_aprovadores_acompanham_o_tipo_entre_versoes(self):
        diretoria = Alcada.objects.get(papel_aprovador="DIRETORIA_UNIDADE", filial__isnull=True, ativo=True)
        AprovadorAlcada.objects.create(alcada=diretoria, usuario=self.admin, filial=self.cp)
        self.salvar([(Decimal("1000.00"), "AUTOMATICA"), (Decimal("20000.00"), "DIRETORIA_UNIDADE"), (None, "CEO")])
        nova = Alcada.objects.get(papel_aprovador="DIRETORIA_UNIDADE", filial__isnull=True, ativo=True)
        self.assertNotEqual(nova.pk, diretoria.pk)
        self.assertEqual(nova.aprovadores.get().usuario, self.admin)

    def test_filial_com_tabela_propria_usa_a_dela_e_as_outras_a_do_grupo(self):
        self.salvar([(Decimal("1000.00"), "AUTOMATICA"), (None, "CEO")], filial=self.cp)
        self.assertEqual(Alcada.para_valor(Decimal("500"), [self.cp.pk]).papel_aprovador, "AUTOMATICA")
        self.assertEqual(Alcada.para_valor(Decimal("500"), [self.vc.pk]).papel_aprovador, "DIRETORIA_UNIDADE")

    def test_varias_filiais_vale_a_mais_restritiva(self):
        self.salvar([(Decimal("1000.00"), "AUTOMATICA"), (None, "CEO")], filial=self.cp)
        faixa = Alcada.para_valor(Decimal("500"), [self.cp.pk, self.vc.pk])
        self.assertEqual(faixa.papel_aprovador, "DIRETORIA_UNIDADE")  # a do grupo exige mais

    def test_voltar_ao_padrao(self):
        from .alcadas import voltar_ao_padrao

        self.salvar([(Decimal("1000.00"), "AUTOMATICA"), (None, "CEO")], filial=self.cp)
        voltar_ao_padrao(tenant=self.tenant, filial=self.cp)
        self.assertEqual(Alcada.para_valor(Decimal("500"), [self.cp.pk]).papel_aprovador, "DIRETORIA_UNIDADE")


class TelaDeAlcadasTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_superuser("root", tenant=self.tenant, password=None)
        self.client.force_login(self.admin)

    def test_tela_abre_com_os_cartoes_das_faixas(self):
        resposta = self.client.get("/configuracoes/alcadas/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Aprovação automática")
        self.assertContains(resposta, 'name="maximo"')

    def test_salvar_pela_tela_cria_nova_versao(self):
        resposta = self.client.post("/configuracoes/alcadas/", {
            "acao": "salvar",
            "maximo": ["800,00", ""],
            "papel": ["AUTOMATICA", "CFO"],
        })
        self.assertEqual(resposta.status_code, 302)
        ativas = Alcada.objects.filter(ativo=True, filial__isnull=True).order_by("valor_min")
        self.assertEqual([(a.valor_max, a.papel_aprovador) for a in ativas],
                         [(Decimal("800.00"), "AUTOMATICA"), (None, "CFO")])

    def test_tabela_invalida_volta_com_erro_e_nao_grava(self):
        antes = Alcada.objects.filter(ativo=True).count()
        resposta = self.client.post("/configuracoes/alcadas/", {
            "acao": "salvar", "maximo": ["500,00", ""], "papel": ["DIRETORIA_UNIDADE", "AUTOMATICA"],
        })
        self.assertEqual(resposta.status_code, 200)  # reexibe a tela com a mensagem
        self.assertEqual(Alcada.objects.filter(ativo=True).count(), antes)


class FormularioDeUsuarioTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        from tenancy.models import Filial

        self.root = Usuario.objects.create_superuser("root_u", tenant=self.tenant, password=None)
        PapelUsuario.objects.create(usuario=self.root, papel=Papel.SUPERADMIN)
        self.client.force_login(self.root)
        self.cp = Filial.objects.get(sigla="CP")
        self.vc = Filial.objects.get(sigla="VC")

    def dados(self, **extra):
        base = {"username": "novo", "senha": "senha-forte-123", "ativo_no_sistema": "on"}
        base.update(extra)
        return base

    def test_cria_usuario_com_modulos_e_varias_unidades(self):
        resposta = self.client.post("/configuracoes/usuarios/novo/", self.dados(
            modulos=["ESTOQUE", "COMPRAS"], eh_aprovador="on", filiais_permitidas=[self.cp.pk, self.vc.pk],
        ))
        self.assertEqual(resposta.status_code, 302)
        u = Usuario.objects.get(username="novo")
        self.assertTrue(u.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE))
        self.assertTrue(u.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS))
        self.assertFalse(u.tem_papel(Papel.ADMINISTRADOR, Modulo.FINANCEIRO))
        self.assertTrue(u.tem_papel(Papel.APROVADOR))
        self.assertTrue(u.tem_papel(Papel.SOLICITANTE))
        self.assertEqual(u.filiais_permitidas.count(), 2)

    def test_editar_remove_o_que_foi_desmarcado(self):
        self.client.post("/configuracoes/usuarios/novo/", self.dados(modulos=["ESTOQUE", "COMPRAS"]))
        u = Usuario.objects.get(username="novo")
        self.client.post(f"/configuracoes/usuarios/{u.pk}/editar/", self.dados(modulos=["COMPRAS"]))
        self.assertFalse(u.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE))
        self.assertTrue(u.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS))

    def test_novo_usuario_exige_senha(self):
        resposta = self.client.post("/configuracoes/usuarios/novo/", self.dados(senha=""))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Usuario.objects.filter(username="novo").exists())

    def test_nao_remove_o_proprio_superadmin(self):
        resposta = self.client.post(f"/configuracoes/usuarios/{self.root.pk}/editar/", {
            "username": "root_u", "ativo_no_sistema": "on",
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(self.root.tem_papel(Papel.SUPERADMIN))
