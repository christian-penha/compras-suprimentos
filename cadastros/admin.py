from django.contrib import admin

from .models import (
    Categoria,
    CentroCusto,
    Feriado,
    Filial,
    Fornecedor,
    MotivoRequisicao,
    Produto,
    Subcentro,
)


@admin.register(Filial)
class FilialAdmin(admin.ModelAdmin):
    list_display = ["nome", "sigla", "ativo"]
    list_filter = ["ativo"]


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "nome_fantasia", "cnpj", "telefone", "ativo"]
    list_filter = ["ativo"]
    search_fields = ["razao_social", "nome_fantasia", "cnpj"]


class SubcentroInline(admin.TabularInline):
    model = Subcentro
    extra = 0


@admin.register(CentroCusto)
class CentroCustoAdmin(admin.ModelAdmin):
    list_display = ["nome", "filial", "ativo"]
    list_filter = ["filial", "ativo"]
    inlines = [SubcentroInline]


@admin.register(MotivoRequisicao)
class MotivoRequisicaoAdmin(admin.ModelAdmin):
    list_display = ["nome", "ativo"]


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
