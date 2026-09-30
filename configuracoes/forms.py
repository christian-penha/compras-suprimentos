from django import forms
from django.contrib.auth import get_user_model

from contas.models import Papel, PapelUsuario
from tenancy.models import Empresa, Filial

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
            "filial", "cargo", "ciclo",
            "papel", "senha", "is_staff", "ativo_no_sistema",
        ]
        labels = {
            "username": "Usuário",
            "nome_completo": "Nome completo",
            "email": "E-mail",
            "telefone": "Telefone",
            "filial": "Filial/unidade",
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
            papel = self.cleaned_data["papel"]
            PapelUsuario.objects.filter(usuario=usuario).exclude(papel=papel).delete()
            PapelUsuario.objects.get_or_create(usuario=usuario, papel=papel)
        return usuario


class EmpresaForm(forms.ModelForm):
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
