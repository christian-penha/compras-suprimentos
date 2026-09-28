from django.contrib import admin

from apps.cadastros.models import CategoriaProduto, ItemKitRequisicao, KitRequisicao, Produto


@admin.register(CategoriaProduto)
class CategoriaProdutoAdmin(admin.ModelAdmin):
    list_display = ("nome", "categoria_pai", "tenant")
    list_filter = ("tenant",)


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ("codigo_interno", "nome", "categoria", "unidade_base", "ativo", "tenant")
    list_filter = ("tenant", "categoria", "ativo", "categoria_investimento")
    search_fields = ("codigo_interno", "nome")


class ItemKitRequisicaoInline(admin.TabularInline):
    model = ItemKitRequisicao
    extra = 1


@admin.register(KitRequisicao)
class KitRequisicaoAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo", "tenant")
    list_filter = ("tenant", "ativo")
    inlines = [ItemKitRequisicaoInline]
