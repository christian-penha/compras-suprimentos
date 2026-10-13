from django.contrib.auth import get_user_model
from django.http import QueryDict

from contas.models import ModoAprovacaoSetor, Setor
from tenancy.models import Filial
from tenancy.testing import TenantTestCase

from .forms import SetorForm

Usuario = get_user_model()


class SetorFormTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.filial = Filial.objects.get(sigla="CP")
        self.ana = Usuario.objects.create_user("ana", password="x", tenant=self.tenant)
        self.bia = Usuario.objects.create_user("bia", password="x", tenant=self.tenant)
        self.caio = Usuario.objects.create_user("caio", password="x", tenant=self.tenant)

    def salvar(self, instancia=None, **dados):
        base = {"filial": self.filial.pk, "nome": "Financeiro", "ativo": "on", "modo_aprovacao": ""}
        base.update(dados)
        dados = QueryDict(mutable=True)
        for chave, valor in base.items():
            dados.setlist(chave, valor if isinstance(valor, list) else [valor])
        form = SetorForm(dados, instance=instancia)
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.tenant = self.tenant
        return form.save()

    def test_marca_membros_e_lideres_na_tela_do_setor(self):
        setor = self.salvar(membros=[self.ana.pk, self.bia.pk], lideres=[self.ana.pk])
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.setor, setor)
        self.assertEqual(list(setor.lideres.all()), [self.ana])
        self.assertEqual(setor.membros.count(), 2)

    def test_desmarcar_remove_do_setor_e_marcar_move_de_outro(self):
        setor = self.salvar(membros=[self.ana.pk, self.bia.pk])
        outro = Setor.objects.create(tenant=self.tenant, filial=self.filial, nome="TI")
        self.caio.setor = outro
        self.caio.save()

        self.salvar(instancia=setor, membros=[self.bia.pk, self.caio.pk])
        for u in (self.ana, self.bia, self.caio):
            u.refresh_from_db()
        self.assertIsNone(self.ana.setor)
        self.assertEqual(self.bia.setor, setor)
        self.assertEqual(self.caio.setor, setor)

    def test_regra_do_setor_e_salva(self):
        setor = self.salvar(modo_aprovacao=ModoAprovacaoSetor.TODOS_LIDERES)
        self.assertEqual(setor.modo_aprovacao, ModoAprovacaoSetor.TODOS_LIDERES)
