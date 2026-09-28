from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.core.models import Feriado, Tenant, Usuario


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("nome", "slug", "ativo")


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Tenant", {"fields": ("tenant",)}),)
    list_display = UserAdmin.list_display + ("tenant",)


@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    list_display = ("data", "descricao", "tenant")
    list_filter = ("tenant",)
