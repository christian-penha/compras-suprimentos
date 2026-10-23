from django import forms
from django.utils import timezone

from tenancy.models import Almoxarifado, Evento, Filial, MotivoRequisicao

from cadastros.models import Produto

from .models import Pedido, PrioridadePedido, SugestaoProduto

CAMPO = (
    "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm "
    "focus:border-slate-900 focus:ring-1 focus:ring-slate-900 "
    "dark:bg-slate-800 dark:border-slate-700 dark:text-slate-100"
)


def filiais_de_destino(usuario):
    """Filiais para as quais o usuário pode pedir: as permitidas no cadastro dele, ou a
    própria filial como fallback. Filial sem almoxarifado de destino fica de fora."""
    candidatas = usuario.filiais_permitidas.filter(ativo=True)
    if not candidatas.exists() and usuario.filial_id:
        candidatas = Filial.objects.filter(pk=usuario.filial_id, ativo=True)
    ids = [f.pk for f in candidatas if f.almoxarifado_padrao()]
    return Filial.objects.filter(pk__in=ids).order_by("nome")


def almoxarifados_do_usuario(usuario):
    """Almoxarifados que o usuário enxerga (catálogo) e de onde pode pedir: os marcados no
    cadastro dele; sem nenhum, o almoxarifado padrão de cada unidade onde ele pode pedir."""
    marcados = list(
        usuario.almoxarifados.filter(ativo=True).select_related("filial").order_by("filial__nome", "nome")
    )
    if marcados:
        return marcados
    return [a for a in (f.almoxarifado_padrao() for f in filiais_de_destino(usuario)) if a]


class PedidoForm(forms.ModelForm):
    filial_destino = forms.ModelChoiceField(
        queryset=Filial.objects.none(), label="Unidade",
        empty_label="Selecione a unidade", widget=forms.Select(attrs={"class": CAMPO}),
    )
    almoxarifado_destino = forms.ModelChoiceField(
        queryset=Almoxarifado.objects.none(), label="Almoxarifado",
        empty_label="Selecione o almoxarifado", widget=forms.Select(attrs={"class": CAMPO}),
    )

    class Meta:
        model = Pedido
        fields = ["observacao", "prioridade", "data_desejada", "motivo", "evento"]
        labels = {
            "observacao": "Descrição",
            "prioridade": "Prioridade",
            "data_desejada": "Data desejada",
            "motivo": "Motivo",
            "evento": "Evento",
        }
        widgets = {
            "observacao": forms.Textarea(attrs={
                "class": CAMPO, "rows": 3,
                "placeholder": "Para que serve este pedido? Ajuda quem aprova e quem separa.",
            }),
            "prioridade": forms.RadioSelect(),
            "data_desejada": forms.DateInput(attrs={"class": CAMPO, "type": "date"}),
            "motivo": forms.Select(attrs={"class": CAMPO}),
            "evento": forms.Select(attrs={"class": CAMPO}),
        }

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.almoxarifados = almoxarifados_do_usuario(usuario)
        self.filiais = Filial.objects.filter(pk__in={a.filial_id for a in self.almoxarifados}).order_by("nome")
        self.fields["filial_destino"].queryset = self.filiais
        if self.filiais.count() == 1:
            self.fields["filial_destino"].initial = self.filiais.first()
        campo = self.fields["almoxarifado_destino"]
        campo.queryset = Almoxarifado.objects.filter(
            pk__in=[a.pk for a in self.almoxarifados]
        ).select_related("filial").order_by("filial__nome", "nome")
        campo.label_from_instance = lambda a: f"{a.filial.sigla} — {a.nome}"
        if len(self.almoxarifados) == 1:
            campo.initial = self.almoxarifados[0]
        self.fields["observacao"].required = True
        self.fields["data_desejada"].required = True
        self.fields["prioridade"].initial = PrioridadePedido.NORMAL
        self.fields["motivo"].queryset = MotivoRequisicao.objects.filter(ativo=True)
        self.fields["motivo"].empty_label = "Selecione o motivo"
        self.fields["evento"].queryset = Evento.objects.filter(
            ativo=True, filial__in=self.filiais
        ).select_related("filial")
        self.fields["evento"].required = False
        self.fields["evento"].empty_label = "Selecione o evento"

    def clean_data_desejada(self):
        data = self.cleaned_data["data_desejada"]
        if data < timezone.localdate():
            raise forms.ValidationError("A data desejada não pode estar no passado.")
        return data

    def clean(self):
        dados = super().clean()
        motivo, evento = dados.get("motivo"), dados.get("evento")
        filial, almoxarifado = dados.get("filial_destino"), dados.get("almoxarifado_destino")
        # uma unidade não pede no almoxarifado de outra
        if filial and almoxarifado and almoxarifado.filial_id != filial.pk:
            self.add_error("almoxarifado_destino", "Este almoxarifado não pertence à unidade escolhida.")
        if motivo and motivo.exige_evento:
            if not evento:
                self.add_error("evento", "Informe o evento deste pedido.")
            elif filial and evento.filial_id != filial.pk:
                self.add_error("evento", "Este evento não pertence à unidade escolhida.")
        elif motivo:
            dados["evento"] = None
        return dados

    def save(self, commit=True):
        pedido = super().save(commit=False)
        pedido.almoxarifado_destino = self.cleaned_data["almoxarifado_destino"]
        if commit:
            pedido.save()
        return pedido

    @property
    def dados_js(self):
        """Dados para o front mostrar/filtrar o campo Evento (a validação real é no servidor)."""
        return {
            "motivos_com_evento": list(
                self.fields["motivo"].queryset.filter(exige_evento=True).values_list("pk", flat=True)
            ),
            "evento_filial": {
                str(e.pk): e.filial_id for e in self.fields["evento"].queryset
            },
            "almoxarifado_filial": {str(a.pk): a.filial_id for a in self.almoxarifados},
        }


class SugestaoProdutoForm(forms.ModelForm):
    class Meta:
        model = SugestaoProduto
        fields = ["nome", "especificacao", "categoria", "justificativa"]
        labels = {"nome": "Nome do produto", "especificacao": "Especificação (marca, cor, tamanho…)",
                  "categoria": "Categoria (se souber)"}
        widgets = {f: forms.TextInput(attrs={"class": CAMPO}) for f in ("nome", "especificacao", "justificativa")}
        widgets["categoria"] = forms.Select(attrs={"class": CAMPO})


class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = [
            "nome", "especificacao", "codigo", "categoria", "grupo", "tipo", "unidade_medida",
            "fornecedor_padrao", "imagem", "imagem_url", "reutilizavel", "ativo",
        ]
        labels = {"especificacao": "Especificação", "codigo": "Código interno", "grupo": "Grupo",
                  "unidade_medida": "Unidade de medida", "fornecedor_padrao": "Fornecedor padrão",
                  "reutilizavel": "Reutilizável (exige devolução)"}
        widgets = {
            "nome": forms.TextInput(attrs={"class": CAMPO}),
            "especificacao": forms.TextInput(attrs={"class": CAMPO}),
            "codigo": forms.TextInput(attrs={"class": CAMPO}),
            "tipo": forms.TextInput(attrs={"class": CAMPO}),
            "categoria": forms.Select(attrs={"class": CAMPO}),
            "grupo": forms.Select(attrs={"class": CAMPO}),
            "unidade_medida": forms.Select(attrs={"class": CAMPO}),
            "fornecedor_padrao": forms.Select(attrs={"class": CAMPO}),
            "imagem_url": forms.URLInput(attrs={"class": CAMPO}),
        }
