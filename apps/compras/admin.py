from django.contrib import admin

from apps.compras.models import Fornecedor


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ("razao_social", "cnpj", "ativo", "tenant")
    list_filter = ("tenant", "ativo")
    search_fields = ("razao_social", "cnpj")
