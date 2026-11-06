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
from tenancy.models import (
    Almoxarifado, CentroCusto, ConfiguracaoSLA, Empresa, Evento, Filial, ModoOrcamento, PeriodoNaoUtil,
)

Usuario = get_user_model()

CAMPO = (
    "w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 shadow-sm "
    "placeholder:text-slate-400 hover:border-slate-400 focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20 "
    "focus:outline-none dark:bg-slate-800 dark:border-slate-600 dark:text-slate-100 "
    "dark:hover:border-slate-500 dark:focus:border-teal-400 dark:focus:ring-teal-400/20"
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
            "filial", "filiais_permitidas", "almoxarifados", "setor", "cargo", "ciclo",
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
            "almoxarifados": forms.CheckboxSelectMultiple,
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
        self.fields["almoxarifados"].queryset = (
            Almoxarifado.objects.filter(ativo=True).select_related("filial").order_by("filial__nome", "nome")
        )
        self.fields["almoxarifados"].required = False
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

    @property
    def setor_filial(self):
        """Para o front filtrar o setor pela unidade escolhida."""
        return {str(s.pk): s.filial_id for s in self.fields["setor"].queryset}

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
        fields = ["filial", "pai", "nome", "ativo", "orcamento_modo", "orcamento_valor", "orcamento_meses", "orcamento_inicio"]
        labels = {
            "filial": "Filial", "pai": "Conta pai (opcional)", "nome": "Nome do centro de custo", "ativo": "Ativo",
            "orcamento_modo": "Orçamento", "orcamento_valor": "Valor (R$)",
            "orcamento_meses": "Quantos meses",
        }
        widgets = {
            "filial": forms.Select(attrs={"class": CAMPO}),
            "pai": forms.Select(attrs={"class": CAMPO}),
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
        # só contas de topo podem ser pai (um nível de subconta)
        pais = CentroCusto.objects.filter(pai__isnull=True, ativo=True).select_related("filial")
        if self.instance.pk:
            pais = pais.exclude(pk=self.instance.pk)
        self.fields["pai"].queryset = self.pais = pais

    def _validar_pai(self, dados):
        pai = dados.get("pai")
        if pai is None:
            return
        if pai.filial_id != dados["filial"].pk:
            self.add_error("pai", "A conta pai precisa ser da mesma filial.")
        elif self.instance.pk and self.instance.subcontas.exists():
            self.add_error("pai", "Este centro já tem subcontas; não pode ser subconta de outro.")

    def _centro_simulado(self, dados):
        """Centro só com os campos de orçamento digitados, para calcular sem salvar."""
        return CentroCusto(
            orcamento_modo=dados["orcamento_modo"], orcamento_valor=dados.get("orcamento_valor"),
            orcamento_inicio=dados.get("orcamento_inicio"), orcamento_meses=dados.get("orcamento_meses"),
        )

    def _ler_meses(self, dados):
        """Valores digitados na grade mês a mês (campos `mes_AAAA-MM`), só dos meses da janela."""
        from datetime import date
        from decimal import Decimal, InvalidOperation

        from tenancy.orcamento import janela_de_meses

        proprios = {}
        for ano, mes in janela_de_meses(self._centro_simulado(dados)):
            bruto = (self.data.get(f"mes_{ano}-{mes:02d}") or "").strip().replace(",", ".")
            if not bruto:
                continue
            try:
                valor = Decimal(bruto)
            except InvalidOperation:
                self.add_error(None, f"Valor inválido em {mes:02d}/{ano}.")
                continue
            if valor < 0:
                self.add_error(None, f"O valor de {mes:02d}/{ano} não pode ser negativo.")
                continue
            proprios[date(ano, mes, 1)] = valor.quantize(Decimal("0.01"))
        return proprios

    def _validar_meses(self, dados, proprios):
        if dados["orcamento_modo"] != ModoOrcamento.TOTAL_RATEADO or not proprios:
            return
        from tenancy.orcamento import janela_de_meses

        soma, total = sum(proprios.values()), dados["orcamento_valor"]
        todos = len(proprios) == len(janela_de_meses(self._centro_simulado(dados)))
        if soma > total or (todos and soma != total):
            self.add_error(None, (
                f"Os meses somam R$ {soma:.2f} e o total é R$ {total:.2f}: "
                + ("a soma dos meses não pode passar do total." if soma > total
                   else "com todos os meses preenchidos a soma precisa fechar no total (deixe um mês em branco para ele absorver a diferença).")
            ))

    def _validar_soma_das_subcontas(self, dados, proprios):
        """Em cada mês, a soma dos orçamentos das subcontas não passa do orçamento da conta pai."""
        from datetime import date

        from tenancy.orcamento import janela_de_meses, orcamento_do_mes

        pai = dados.get("pai")
        if pai is None or pai.orcamento_valor is None:
            return
        proprio = self._centro_simulado(dados)
        hoje = date.today()
        meses = {(hoje.year, hoje.month), *janela_de_meses(proprio)}
        irmas = list(pai.subcontas.exclude(pk=self.instance.pk))
        for ano, mes in sorted(meses):
            limite = orcamento_do_mes(pai, ano, mes)
            if limite is None:
                continue
            soma = (orcamento_do_mes(proprio, ano, mes, proprios) or 0) + sum(
                (orcamento_do_mes(irma, ano, mes) or 0 for irma in irmas), 0)
            if soma > limite:
                self.add_error(None, f"Em {mes:02d}/{ano} a soma das subcontas (R$ {soma:.2f}) passa do orçamento da conta pai (R$ {limite:.2f}).")
                return

    def clean(self):
        dados = super().clean()
        self.proprios = {}
        if self.errors:
            return dados
        self._validar_pai(dados)
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
        elif not (meses and inicio):
            # MENSAL sem período: vale o valor padrão em qualquer mês, sem grade mês a mês
            dados["orcamento_meses"] = dados["orcamento_inicio"] = None
        if dados.get("orcamento_inicio"):
            dados["orcamento_inicio"] = dados["orcamento_inicio"].replace(day=1)
        if not self.errors:
            self.proprios = self._ler_meses(dados)
        if not self.errors:
            self._validar_meses(dados, self.proprios)
        if not self.errors:
            self._validar_soma_das_subcontas(dados, self.proprios)
        return dados

    def save(self, commit=True):
        centro = super().save(commit=commit)
        if commit:
            self.salvar_meses(centro)
        return centro

    def salvar_meses(self, centro):
        """Troca os valores próprios do centro pelos digitados (sem orçamento = nenhum)."""
        from tenancy.models import OrcamentoMes

        centro.orcamentos_mes.all().delete()
        OrcamentoMes.objects.bulk_create([
            OrcamentoMes(tenant_id=centro.tenant_id, centro=centro, ano_mes=ano_mes, valor=valor)
            for ano_mes, valor in self.proprios.items()
        ])


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


class ConfiguracaoSLAForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoSLA
        fields = ["prazo_estoque_dias", "prazo_compra_dias"]
        labels = {
            "prazo_estoque_dias": "Pedido atendido pelo estoque (dias úteis)",
            "prazo_compra_dias": "Pedido que precisa de compra (dias úteis)",
        }
        widgets = {
            "prazo_estoque_dias": forms.NumberInput(attrs={"class": CAMPO, "min": "0", "max": "90"}),
            "prazo_compra_dias": forms.NumberInput(attrs={"class": CAMPO, "min": "0", "max": "90"}),
        }

    def clean(self):
        dados = super().clean()
        estoque, compra = dados.get("prazo_estoque_dias"), dados.get("prazo_compra_dias")
        if estoque is not None and compra is not None and compra < estoque:
            self.add_error("prazo_compra_dias", "O prazo de compra não pode ser menor que o do estoque.")
        return dados


class PeriodoNaoUtilForm(forms.ModelForm):
    fim = forms.DateField(
        label="Último dia", required=False,
        widget=forms.DateInput(attrs={"class": CAMPO, "type": "date"}, format="%Y-%m-%d"),
        help_text="Deixe em branco para um dia só.",
    )

    class Meta:
        model = PeriodoNaoUtil
        fields = ["nome", "tipo", "inicio", "fim", "filial", "ativo"]
        labels = {
            "nome": "Nome", "tipo": "Tipo", "inicio": "Primeiro dia",
            "filial": "Vale para", "ativo": "Ativo",
        }
        widgets = {
            "nome": forms.TextInput(attrs={"class": CAMPO, "placeholder": "ex.: Recesso de julho, Emenda do Corpus Christi"}),
            "tipo": forms.Select(attrs={"class": CAMPO}),
            "inicio": forms.DateInput(attrs={"class": CAMPO, "type": "date"}, format="%Y-%m-%d"),
            "filial": forms.Select(attrs={"class": CAMPO}),
            "ativo": forms.CheckboxInput(attrs={"class": CAMPO_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filial"].queryset = Filial.objects.filter(ativo=True)
        self.fields["filial"].required = False
        self.fields["filial"].empty_label = "Todas as filiais"

    def clean(self):
        dados = super().clean()
        inicio = dados.get("inicio")
        if inicio and not dados.get("fim"):
            dados["fim"] = inicio
        if inicio and dados.get("fim") and dados["fim"] < inicio:
            self.add_error("fim", "O último dia não pode ser antes do primeiro.")
        return dados
