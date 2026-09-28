from django.contrib import admin

from apps.tenancy.models import Almoxarifado, CentroCusto, Empresa, Filial, MotivoRequisicao


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nome_fantasia", "razao_social", "cnpj", "tenant")
    list_filter = ("tenant",)
    search_fields = ("nome_fantasia", "razao_social", "cnpj")


@admin.register(Filial)
class FilialAdmin(admin.ModelAdmin):
    list_display = ("nome", "empresa", "ativo", "tenant")
    list_filter = ("tenant", "empresa", "ativo")


@admin.register(Almoxarifado)
class AlmoxarifadoAdmin(admin.ModelAdmin):
    list_display = ("nome", "filial", "eh_central", "ativo", "tenant")
    list_filter = ("tenant", "filial", "eh_central", "ativo")


@admin.register(CentroCusto)
class CentroCustoAdmin(admin.ModelAdmin):
    list_display = ("nome", "filial", "centro_pai", "ativo", "tenant")
    list_filter = ("tenant", "filial", "ativo")


@admin.register(MotivoRequisicao)
class MotivoRequisicaoAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo", "tenant")
    list_filter = ("tenant", "ativo")
