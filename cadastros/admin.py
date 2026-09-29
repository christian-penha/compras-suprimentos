from django.contrib import admin

from .models import Categoria, Feriado, Fornecedor, Produto


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "nome_fantasia", "cnpj", "telefone", "ativo"]
    list_filter = ["ativo"]
    search_fields = ["razao_social", "nome_fantasia", "cnpj"]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ["nome", "ativo"]


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = [
        "nome", "especificacao", "categoria", "grupo",
        "unidade_medida", "reutilizavel", "fornecedor_padrao", "ativo",
    ]
    list_filter = ["categoria", "grupo", "reutilizavel", "ativo"]
    search_fields = ["nome", "especificacao", "tipo"]
    autocomplete_fields = ["fornecedor_padrao"]


@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    list_display = ["data", "descricao"]
