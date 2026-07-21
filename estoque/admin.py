from django.contrib import admin

from .models import Almoxarifado, SaldoEstoque


@admin.register(Almoxarifado)
class AlmoxarifadoAdmin(admin.ModelAdmin):
    list_display = ["nome", "filial", "central", "ativo"]
    list_filter = ["filial", "central", "ativo"]


@admin.register(SaldoEstoque)
class SaldoEstoqueAdmin(admin.ModelAdmin):
    list_display = [
        "produto", "almoxarifado", "quantidade", "custo_medio",
        "estoque_minimo", "abaixo_do_minimo",
    ]
    list_filter = ["almoxarifado"]
    search_fields = ["produto__nome", "produto__especificacao"]
    autocomplete_fields = ["produto"]

    @admin.display(boolean=True, description="Abaixo do mínimo")
    def abaixo_do_minimo(self, obj):
        return obj.abaixo_do_minimo
