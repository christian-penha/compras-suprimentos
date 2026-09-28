from django import forms
from django.forms import inlineformset_factory

from apps.cadastros.models import CategoriaProduto, ItemKitRequisicao, KitRequisicao, Produto


class CategoriaProdutoForm(forms.ModelForm):
    class Meta:
        model = CategoriaProduto
        fields = ["nome", "categoria_pai"]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields["categoria_pai"].queryset = CategoriaProduto.objects.filter(tenant=tenant)


class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = [
            "codigo_interno",
            "nome",
            "especificacao",
            "categoria",
            "unidade_base",
            "fator_embalagem_compra",
            "unidade_embalagem_compra",
            "reutilizavel",
            "fornecedor_padrao",
            "categoria_investimento",
            "estoque_minimo",
            "ponto_reposicao",
            "ativo",
        ]

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant is not None:
            self.fields["categoria"].queryset = CategoriaProduto.objects.filter(tenant=tenant)
            self.fields["fornecedor_padrao"].queryset = self.fields["fornecedor_padrao"].queryset.model.objects.filter(
                tenant=tenant
            )


class KitRequisicaoForm(forms.ModelForm):
    class Meta:
        model = KitRequisicao
        fields = ["nome", "ativo"]


class ItemKitRequisicaoForm(forms.ModelForm):
    class Meta:
        model = ItemKitRequisicao
        fields = ["produto", "quantidade_sugerida"]


def item_kit_formset_factory(tenant):
    FormSet = inlineformset_factory(
        KitRequisicao,
        ItemKitRequisicao,
        form=ItemKitRequisicaoForm,
        extra=1,
        can_delete=True,
    )

    class TenantScopedFormSet(FormSet):
        def add_fields(self, form, index):
            super().add_fields(form, index)
            form.fields["produto"].queryset = Produto.objects.filter(tenant=tenant)

    return TenantScopedFormSet
