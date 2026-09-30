from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Alcada, PapelUsuario, RegraAprovacaoEmpresa, Setor, Usuario


class PapelUsuarioInline(admin.TabularInline):
    model = PapelUsuario
    extra = 1


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    inlines = [PapelUsuarioInline]
    list_display = ["username", "nome_completo", "filial", "cargo", "ciclo", "ativo_no_sistema", "listar_papeis"]
    list_filter = UserAdmin.list_filter + ("filial", "ciclo")
    fieldsets = UserAdmin.fieldsets + (
        ("Dados do sistema", {
            "fields": ("tenant", "filial", "cargo", "ciclo", "nome_completo", "telefone", "ativo_no_sistema"),
        }),
    )

    @admin.display(description="Papéis")
    def listar_papeis(self, obj):
        return ", ".join(str(p.get_papel_display()) for p in obj.papeis.all()) or "—"


@admin.register(PapelUsuario)
class PapelUsuarioAdmin(admin.ModelAdmin):
    list_display = ["usuario", "papel", "modulo"]
    list_filter = ["papel", "modulo"]


@admin.register(Setor)
class SetorAdmin(admin.ModelAdmin):
    list_display = ["nome", "filial", "listar_lideres", "ativo"]
    list_filter = ["filial", "ativo"]
    search_fields = ["nome"]
    filter_horizontal = ["lideres"]

    @admin.display(description="Líderes")
    def listar_lideres(self, obj):
        return ", ".join(str(u) for u in obj.lideres.all()) or "—"


@admin.register(RegraAprovacaoEmpresa)
class RegraAprovacaoEmpresaAdmin(admin.ModelAdmin):
    list_display = ["empresa", "modo_aprovacao_setor"]
    list_filter = ["modo_aprovacao_setor"]


@admin.register(Alcada)
class AlcadaAdmin(admin.ModelAdmin):
    list_display = ["valor_min", "valor_max", "papel_aprovador", "ativo", "atualizado_em"]
    list_filter = ["ativo"]

    def save_model(self, request, obj, form, change):
        obj.atualizado_por = request.user
        super().save_model(request, obj, form, change)
