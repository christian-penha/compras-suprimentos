from django.contrib import admin

from .models import Almoxarifado, CentroCusto, Empresa, Filial, MotivoRequisicao, Subcentro, Tenant


class SubcentroInline(admin.TabularInline):
    model = Subcentro
    extra = 0


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("nome", "slug", "ativo")
    search_fields = ("nome", "slug")


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nome_fantasia", "razao_social", "cnpj", "ativo")
    search_fields = ("razao_social", "nome_fantasia", "cnpj")


@admin.register(Filial)
class FilialAdmin(admin.ModelAdmin):
    list_display = ("nome", "sigla", "empresa", "ativo")
    search_fields = ("nome", "sigla")
    list_filter = ("empresa", "ativo")


@admin.register(Almoxarifado)
class AlmoxarifadoAdmin(admin.ModelAdmin):
    list_display = ("nome", "filial", "central", "ativo")
    list_filter = ("filial", "central", "ativo")
    search_fields = ("nome",)


@admin.register(CentroCusto)
class CentroCustoAdmin(admin.ModelAdmin):
    list_display = ("nome", "filial", "ativo")
    list_filter = ("filial", "ativo")
    search_fields = ("nome",)
    inlines = [SubcentroInline]


@admin.register(MotivoRequisicao)
class MotivoRequisicaoAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo")
    search_fields = ("nome",)
