from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PapelUsuario, Usuario


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
