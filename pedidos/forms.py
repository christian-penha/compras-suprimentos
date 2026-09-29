from django import forms

from tenancy.models import Almoxarifado, CentroCusto, MotivoRequisicao, Subcentro

from .models import Pedido

CAMPO = (
    "w-full rounded-xl border-slate-200 bg-white px-4 py-2.5 text-sm "
    "focus:border-blue-400 focus:ring-blue-400"
)


class PedidoForm(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = ["almoxarifado_destino", "centro_custo", "subcentro", "motivo", "observacao"]
        widgets = {
            "almoxarifado_destino": forms.Select(attrs={"class": CAMPO}),
            "centro_custo": forms.Select(attrs={"class": CAMPO}),
            "subcentro": forms.Select(attrs={"class": CAMPO}),
            "motivo": forms.Select(attrs={"class": CAMPO}),
            "observacao": forms.TextInput(
                attrs={"class": CAMPO, "placeholder": "Opcional — contexto do pedido"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["almoxarifado_destino"].queryset = Almoxarifado.objects.filter(
            ativo=True, central=False
        ).select_related("filial")
        self.fields["centro_custo"].queryset = CentroCusto.objects.filter(
            ativo=True
        ).select_related("filial")
        self.fields["subcentro"].queryset = Subcentro.objects.filter(ativo=True)
        self.fields["subcentro"].required = False
        self.fields["motivo"].queryset = MotivoRequisicao.objects.filter(ativo=True)
