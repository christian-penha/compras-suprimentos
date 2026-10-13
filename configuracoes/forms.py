from django import forms
from django.contrib.auth import get_user_model

from contas.models import (
    ModoAprovacaoSetor,
    Papel,
    PapelUsuario,
    RegraAprovacaoEmpresa,
    Setor,
)
from tenancy.models import Empresa, Evento, Filial

Usuario = get_user_model()

CAMPO = (
    "w-full rounded-xl border-slate-200 bg-white px-4 py-2.5 text-sm "
    "focus:border-blue-400 focus:ring-blue-400 dark:bg-slate-800 dark:border-slate-700 dark:text-slate-100"
)
CAMPO_CHECK = "rounded border-slate-300 text-slate-900 focus:ring-slate-900"


class UsuarioForm(forms.ModelForm):
    senha = forms.CharField(
        label="Senha", widget=forms.PasswordInput(attrs={"class": CAMPO}),
        required=False, help_text="Deixe em branco para manter a senha atual.",
    )
    papel = forms.ChoiceField(label="Papel", choices=Papel.choices, widget=forms.Select(attrs={"class": CAMPO}))

    class Meta:
        model = Usuario
        fields = [
            "username", "nome_completo", "email", "telefone",
            "filial", "filiais_permitidas", "setor", "cargo", "ciclo",
            "papel", "senha", "is_staff", "ativo_no_sistema",
        ]
        labels = {
            "username": "Usuário",
            "nome_completo": "Nome completo",
            "email": "E-mail",
            "telefone": "Telefone",
            "filial": "Filial/unidade",
            "setor": "Setor",
            "filiais_permitidas": "Unidades onde pode fazer pedidos",
            "cargo": "Cargo",
            "ciclo": "Ciclo/segmento",
            "is_staff": "Acesso ao admin do Django",
            "ativo_no_sistema": "Ativo",
        }
        widgets = {
            "username": forms.TextInput(attrs={"class": CAMPO}),
            "nome_completo": forms.TextInput(attrs={"class": CAMPO}),
            "email": forms.EmailInput(attrs={"class": CAMPO}),
            "telefone": forms.TextInput(attrs={"class": CAMPO}),
            "filial": forms.Select(attrs={"class": CAMPO}),
            "setor": forms.Select(attrs={"class": CAMPO}),
            "filiais_permitidas": forms.SelectMultiple(attrs={"class": CAMPO + " h-32"}),
            "cargo": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Coordenadora Pedagógica"}),
            "ciclo": forms.Select(attrs={"class": CAMPO}),
            "is_staff": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
            "ativo_no_sistema": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filial"].queryset = Filial.objects.filter(ativo=True)
        self.fields["filial"].required = False
        self.fields["filial"].empty_label = "— sem filial definida —"
        self.fields["filiais_permitidas"].queryset = Filial.objects.filter(ativo=True)
        self.fields["filiais_permitidas"].required = False
        self.fields["setor"].queryset = Setor.objects.filter(ativo=True).select_related("filial")
        self.fields["setor"].required = False
        self.fields["setor"].empty_label = "— sem setor definido —"
        self.fields["ciclo"].required = False
        if self.instance.pk:
            papel_atual = self.instance.papeis.first()
            if papel_atual:
                self.fields["papel"].initial = papel_atual.papel

    def save(self, commit=True, tenant=None):
        usuario = super().save(commit=False)
        if tenant is not None and not usuario.tenant_id:
            usuario.tenant = tenant
        senha = self.cleaned_data.get("senha")
        if senha:
            usuario.set_password(senha)
        if commit:
            usuario.save()
            self.save_m2m()
            papel = self.cleaned_data["papel"]
            PapelUsuario.objects.filter(usuario=usuario).exclude(papel=papel).delete()
            PapelUsuario.objects.get_or_create(usuario=usuario, papel=papel)
        return usuario


class SetorForm(forms.ModelForm):
    """Líderes e membros são marcados na própria tela do setor (checkboxes por pessoa)."""

    membros = forms.ModelMultipleChoiceField(
        queryset=Usuario.objects.none(), required=False, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Setor
        fields = ["filial", "nome", "modo_aprovacao", "lideres", "ativo"]
        labels = {
            "filial": "Filial/unidade",
            "nome": "Nome do setor",
            "modo_aprovacao": "Regra de aprovação",
            "lideres": "Líderes",
            "ativo": "Ativo",
        }
        widgets = {
            "filial": forms.Select(attrs={"class": CAMPO}),
            "nome": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Pedagógico"}),
            "modo_aprovacao": forms.Select(attrs={"class": CAMPO}),
            "lideres": forms.CheckboxSelectMultiple,
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pessoas = Usuario.objects.filter(ativo_no_sistema=True).select_related("setor__filial")
        pessoas = pessoas.order_by("nome_completo", "username")
        self.pessoas = list(pessoas)
        self.fields["filial"].queryset = Filial.objects.filter(ativo=True)
        self.fields["lideres"].queryset = pessoas
        self.fields["lideres"].required = False
        self.fields["membros"].queryset = pessoas
        self.fields["modo_aprovacao"].choices = [
            ("", "Usar a regra da empresa")
        ] + list(ModoAprovacaoSetor.choices)
        if self.instance.pk:
            self.initial["membros"] = list(self.instance.membros.values_list("pk", flat=True))
            self.initial["lideres"] = list(self.instance.lideres.values_list("pk", flat=True))
        self.membros_marcados = {str(pk) for pk in self.initial.get("membros", [])}
        self.lideres_marcados = {str(pk) for pk in self.initial.get("lideres", [])}
        if self.is_bound:
            self.membros_marcados = set(self.data.getlist("membros"))
            self.lideres_marcados = set(self.data.getlist("lideres"))

    def save(self, commit=True):
        setor = super().save(commit=commit)
        if commit:
            ids = [u.pk for u in self.cleaned_data["membros"]]
            Usuario.objects.filter(setor=setor).exclude(pk__in=ids).update(setor=None)
            Usuario.objects.filter(pk__in=ids).update(setor=setor)
        return setor


class EmpresaForm(forms.ModelForm):
    modo_aprovacao_setor = forms.ChoiceField(
        label="Regra de aprovação do setor",
        choices=ModoAprovacaoSetor.choices,
        widget=forms.Select(attrs={"class": CAMPO}),
        help_text="Como os pedidos dos colaboradores desta empresa são aprovados pelos líderes de setor.",
    )

    class Meta:
        model = Empresa
        fields = ["razao_social", "nome_fantasia", "cnpj", "codigo_prodados", "ativo"]
        labels = {
            "razao_social": "Razão social",
            "nome_fantasia": "Nome fantasia",
            "cnpj": "CNPJ",
            "codigo_prodados": "Código Prodados",
            "ativo": "Ativa",
        }
        widgets = {
            "razao_social": forms.TextInput(attrs={"class": CAMPO}),
            "nome_fantasia": forms.TextInput(attrs={"class": CAMPO}),
            "cnpj": forms.TextInput(attrs={"class": CAMPO, "placeholder": "00.000.000/0000-00"}),
            "codigo_prodados": forms.TextInput(attrs={"class": CAMPO}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            regra = RegraAprovacaoEmpresa.objects.filter(empresa=self.instance).first()
            if regra:
                self.fields["modo_aprovacao_setor"].initial = regra.modo_aprovacao_setor

    def save(self, commit=True):
        empresa = super().save(commit=commit)
        if commit:
            RegraAprovacaoEmpresa.objects.update_or_create(
                empresa=empresa,
                defaults={"modo_aprovacao_setor": self.cleaned_data["modo_aprovacao_setor"]},
            )
        return empresa


class FilialForm(forms.ModelForm):
    class Meta:
        model = Filial
        fields = ["empresa", "nome", "sigla", "ativo"]
        labels = {"empresa": "Empresa", "nome": "Nome", "sigla": "Sigla", "ativo": "Ativa"}
        widgets = {
            "empresa": forms.Select(attrs={"class": CAMPO}),
            "nome": forms.TextInput(attrs={"class": CAMPO}),
            "sigla": forms.TextInput(attrs={"class": CAMPO}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = Empresa.objects.filter(ativo=True)


class EventoForm(forms.ModelForm):
    class Meta:
        model = Evento
        fields = ["filial", "nome", "ativo"]
        labels = {"filial": "Unidade", "nome": "Nome do evento", "ativo": "Ativo"}
        widgets = {
            "filial": forms.Select(attrs={"class": CAMPO}),
            "nome": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Festa Junina 2026"}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filial"].queryset = Filial.objects.filter(ativo=True)
