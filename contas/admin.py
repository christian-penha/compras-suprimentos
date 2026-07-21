from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Alcada, PapelUsuario, Usuario, VinculoAprovacao


class PapelUsuarioInline(admin.TabularInline):
    model = PapelUsuario
    extra = 1


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    inlines = [PapelUsuarioInline]
    list_display = ["username", "nome_completo", "email", "ativo_no_sistema", "listar_papeis"]
    fieldsets = UserAdmin.fieldsets + (
        ("Dados do sistema", {"fields": ("nome_completo", "telefone", "ativo_no_sistema")}),
    )

    @admin.display(description="Papéis")
    def listar_papeis(self, obj):
        return ", ".join(str(p.get_papel_display()) for p in obj.papeis.all()) or "—"


@admin.register(PapelUsuario)
class PapelUsuarioAdmin(admin.ModelAdmin):
    list_display = ["usuario", "papel", "modulo"]
    list_filter = ["papel", "modulo"]


@admin.register(VinculoAprovacao)
class VinculoAprovacaoAdmin(admin.ModelAdmin):
    list_display = ["requisitante", "aprovador", "ordem", "ativo"]
    list_filter = ["ativo"]
    search_fields = ["requisitante__username", "aprovador__username"]


@admin.register(Alcada)
class AlcadaAdmin(admin.ModelAdmin):
    list_display = ["valor_min", "valor_max", "papel_aprovador", "ativo", "atualizado_em"]
    list_filter = ["ativo"]

    def save_model(self, request, obj, form, change):
        obj.atualizado_por = request.user
        super().save_model(request, obj, form, change)
