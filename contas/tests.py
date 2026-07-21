from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from .models import Modulo, Papel, PapelUsuario

Usuario = get_user_model()


class CriacaoUsuarioPorPapelTest(TestCase):
    def criar(self, username, papel, modulo=None):
        usuario = Usuario.objects.create_user(username=username, password="senha-forte-123")
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
        usuario = Usuario.objects.create_user(username="invalido", password="senha-forte-123")
        with self.assertRaises(IntegrityError):
            PapelUsuario.objects.create(usuario=usuario, papel=Papel.ADMINISTRADOR)

    def test_papel_duplicado_rejeitado(self):
        usuario = self.criar("dulce", Papel.APROVADOR)
        with self.assertRaises(IntegrityError):
            PapelUsuario.objects.create(usuario=usuario, papel=Papel.APROVADOR)
