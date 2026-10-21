from django import forms
from django.db.models import Q
from django.contrib.auth import get_user_model

from contas.models import (
    Modulo,
    ModoAprovacaoSetor,
    Papel,
    PapelUsuario,
    RegraAprovacaoEmpresa,
    Setor,
)
from cadastros.models import Fornecedor
from tenancy.models import Almoxarifado, CentroCusto, Empresa, Evento, Filial, ModoOrcamento

Usuario = get_user_model()

CAMPO = (
    "w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 shadow-sm "
    "placeholder:text-slate-400 hover:border-slate-400 focus:border-slate-900 focus:ring-2 focus:ring-slate-900/10 "
    "focus:outline-none dark:bg-slate-800 dark:border-slate-600 dark:text-slate-100 "
    "dark:hover:border-slate-500 dark:focus:border-white dark:focus:ring-white/20"
)
CAMPO_CHECK = "rounded border-slate-300 text-slate-900 focus:ring-slate-900"


class UsuarioForm(forms.ModelForm):
    senha = forms.CharField(
        label="Senha", widget=forms.PasswordInput(attrs={"class": CAMPO}),
        required=False, help_text="Deixe em branco para manter a senha atual.",
    )
    # Permissões: cada caixa vira um PapelUsuario. Solicitante é sempre concedido.
    eh_aprovador = forms.BooleanField(
        label="Aprovador", required=False,
        widget=forms.CheckboxInput(attrs={"class": CAMPO_CHECK + " mt-0.5 h-4 w-4"}),
    )
    eh_superadmin = forms.BooleanField(
        label="Superadmin", required=False,
        widget=forms.CheckboxInput(attrs={"class": CAMPO_CHECK + " mt-0.5 h-4 w-4"}),
    )
    modulos = forms.MultipleChoiceField(
        label="Módulos que administra", choices=Modulo.choices, required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Usuario
        fields = [
            "username", "nome_completo", "email", "telefone",
            "filial", "filiais_permitidas", "setor", "cargo", "ciclo",
            "senha", "is_staff", "ativo_no_sistema",
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
            "filiais_permitidas": forms.CheckboxSelectMultiple,
            "cargo": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Coordenadora Pedagógica"}),
            "ciclo": forms.Select(attrs={"class": CAMPO}),
            "is_staff": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
            "ativo_no_sistema": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
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
            papeis = list(self.instance.papeis.all())
            self.initial["eh_aprovador"] = any(p.papel == Papel.APROVADOR for p in papeis)
            self.initial["eh_superadmin"] = any(p.papel == Papel.SUPERADMIN for p in papeis)
            self.initial["modulos"] = [p.modulo for p in papeis if p.papel == Papel.ADMINISTRADOR]

    def clean_email(self):
        # O login com Google localiza o usuário pelo e-mail: dois usuários com o mesmo
        # e-mail (ignorando maiúsculas) tornariam o login ambíguo e seria recusado.
        email = (self.cleaned_data.get("email") or "").strip()
        if email:
            repetido = Usuario.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
            if repetido.exists():
                raise forms.ValidationError("Já existe um usuário com este e-mail.")
        return email

    def clean(self):
        dados = super().clean()
        if not self.instance.pk and not dados.get("senha"):
            self.add_error("senha", "Defina a senha inicial do usuário.")
        # evita o administrador se trancar para fora do sistema
        if self.editor is not None and self.instance.pk == self.editor.pk:
            if not dados.get("eh_superadmin") and self.instance.papeis.filter(papel=Papel.SUPERADMIN).exists():
                self.add_error("eh_superadmin", "Você não pode remover o seu próprio acesso de superadmin.")
            if dados.get("ativo_no_sistema") is False:
                self.add_error("ativo_no_sistema", "Você não pode desativar o seu próprio usuário.")
        return dados

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
            desejados = {(Papel.SOLICITANTE, None)}
            if self.cleaned_data["eh_aprovador"]:
                desejados.add((Papel.APROVADOR, None))
            if self.cleaned_data["eh_superadmin"]:
                desejados.add((Papel.SUPERADMIN, None))
            desejados |= {(Papel.ADMINISTRADOR, m) for m in self.cleaned_data["modulos"]}
            for p in usuario.papeis.all():
                if (p.papel, p.modulo) not in desejados:
                    p.delete()
            for papel, modulo in desejados:
                PapelUsuario.objects.get_or_create(usuario=usuario, papel=papel, modulo=modulo)
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


class CentroCustoForm(forms.ModelForm):
    orcamento_inicio = forms.DateField(
        label="Primeiro mês", required=False, input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(attrs={"class": CAMPO, "type": "month"}, format="%Y-%m"),
    )

    class Meta:
        model = CentroCusto
        fields = ["filial", "nome", "ativo", "orcamento_modo", "orcamento_valor", "orcamento_meses", "orcamento_inicio"]
        labels = {
            "filial": "Filial", "nome": "Nome do centro de custo", "ativo": "Ativo",
            "orcamento_modo": "Orçamento", "orcamento_valor": "Valor (R$)",
            "orcamento_meses": "Dividir em quantos meses",
        }
        widgets = {
            "filial": forms.Select(attrs={"class": CAMPO}),
            "nome": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Pedagógico, Manutenção"}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
            "orcamento_modo": forms.Select(attrs={"class": CAMPO}),
            "orcamento_valor": forms.NumberInput(attrs={"class": CAMPO, "step": "0.01", "min": "0"}),
            "orcamento_meses": forms.NumberInput(attrs={"class": CAMPO, "min": "1", "max": "120"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filial"].queryset = Filial.objects.filter(ativo=True)
        self.fields["orcamento_modo"].required = False  # ausente = sem orçamento

    def clean(self):
        dados = super().clean()
        if self.errors:
            return dados
        modo = dados["orcamento_modo"] = dados.get("orcamento_modo") or ModoOrcamento.SEM_ORCAMENTO
        valor, meses, inicio = dados.get("orcamento_valor"), dados.get("orcamento_meses"), dados.get("orcamento_inicio")
        if modo == ModoOrcamento.SEM_ORCAMENTO:
            dados["orcamento_valor"] = dados["orcamento_meses"] = dados["orcamento_inicio"] = None
            return dados
        if valor is None or valor <= 0:
            self.add_error("orcamento_valor", "Informe um valor maior que zero.")
        if modo == ModoOrcamento.TOTAL_RATEADO:
            if not meses or meses < 1:
                self.add_error("orcamento_meses", "Informe em quantos meses o valor será dividido.")
            if inicio is None:
                self.add_error("orcamento_inicio", "Informe o primeiro mês do orçamento.")
        else:
            dados["orcamento_meses"] = dados["orcamento_inicio"] = None
        if dados.get("orcamento_inicio"):
            dados["orcamento_inicio"] = dados["orcamento_inicio"].replace(day=1)
        return dados


class FornecedorForm(forms.ModelForm):
    class Meta:
        model = Fornecedor
        fields = [
            "razao_social", "nome_fantasia", "cnpj", "contato", "telefone", "email",
            "condicoes_pagamento", "ativo",
        ]
        labels = {
            "razao_social": "Razão social", "nome_fantasia": "Nome fantasia", "cnpj": "CNPJ",
            "contato": "Contato", "telefone": "Telefone", "email": "E-mail",
            "condicoes_pagamento": "Condições de pagamento", "ativo": "Ativo",
        }
        widgets = {
            "razao_social": forms.TextInput(attrs={"class": CAMPO}),
            "nome_fantasia": forms.TextInput(attrs={"class": CAMPO}),
            "cnpj": forms.TextInput(attrs={"class": CAMPO, "placeholder": "00.000.000/0000-00"}),
            "contato": forms.TextInput(attrs={"class": CAMPO}),
            "telefone": forms.TextInput(attrs={"class": CAMPO}),
            "email": forms.EmailInput(attrs={"class": CAMPO}),
            "condicoes_pagamento": forms.TextInput(attrs={"class": CAMPO}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }


class AlmoxarifadoForm(forms.ModelForm):
    class Meta:
        model = Almoxarifado
        fields = ["filial", "nome", "central", "ativo"]
        labels = {
            "filial": "Filial",
            "nome": "Nome do almoxarifado",
            "central": "Estoque central (CSC)",
            "ativo": "Ativo",
        }
        widgets = {
            "filial": forms.Select(attrs={"class": CAMPO}),
            "nome": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Geral, Educação Infantil"}),
            "central": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        filiais = Filial.objects.filter(ativo=True)
        if self.instance.pk:  # mantém a filial atual na lista mesmo que tenha sido desativada
            filiais = Filial.objects.filter(Q(ativo=True) | Q(pk=self.instance.filial_id))
        self.fields["filial"].queryset = filiais

    def clean(self):
        dados = super().clean()
        if self.errors:
            return dados
        central, ativo = dados.get("central"), dados.get("ativo")
        # Os pedidos buscam "o" almoxarifado central (Almoxarifado.objects.get(central=True)):
        # precisa existir exatamente um, e ativo. Validado aqui porque a restrição do banco
        # envolve o tenant, que não é campo do formulário.
        outro_central = Almoxarifado.objects.filter(central=True).exclude(pk=self.instance.pk)
        if central and outro_central.exists():
            self.add_error("central", "Já existe um almoxarifado central — só pode haver um.")
        if self.instance.pk and self.instance.central and not central:
            self.add_error("central", "O sistema precisa de um almoxarifado central. Marque outro como central primeiro.")
        if central and not ativo:
            self.add_error("ativo", "O almoxarifado central não pode ser desativado.")
        return dados
