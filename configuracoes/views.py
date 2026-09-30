from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render

from contas.models import Papel, Setor
from contas.permissoes import requer_papel
from tenancy.models import Empresa, Filial

from .forms import EmpresaForm, FilialForm, SetorForm, UsuarioForm

Usuario = get_user_model()


@requer_papel(Papel.SUPERADMIN)
def painel(request):
    return render(
        request, "configuracoes/painel.html",
        {
            "qtd_usuarios": Usuario.objects.filter(ativo_no_sistema=True).count(),
            "qtd_empresas": Empresa.objects.filter(ativo=True).count(),
            "qtd_filiais": Filial.objects.filter(ativo=True).count(),
            "qtd_setores": Setor.objects.filter(ativo=True).count(),
        },
    )


@requer_papel(Papel.SUPERADMIN)
def usuario_lista(request):
    usuarios = Usuario.objects.all().prefetch_related("papeis").order_by("nome_completo", "username")
    return render(request, "configuracoes/usuario_lista.html", {"usuarios": usuarios})


@requer_papel(Papel.SUPERADMIN)
def usuario_form(request, pk=None):
    instancia = get_object_or_404(Usuario, pk=pk) if pk else None
    form = UsuarioForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save(tenant=request.user.tenant)
        messages.success(request, "Usuário salvo com sucesso.")
        return redirect("config_usuario_lista")
    return render(request, "configuracoes/usuario_form.html", {"form": form, "instancia": instancia})


@requer_papel(Papel.SUPERADMIN)
def empresa_lista(request):
    empresas = Empresa.objects.all().order_by("razao_social")
    return render(request, "configuracoes/empresa_lista.html", {"empresas": empresas})


@requer_papel(Papel.SUPERADMIN)
def empresa_form(request, pk=None):
    instancia = get_object_or_404(Empresa, pk=pk) if pk else None
    form = EmpresaForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        if not form.instance.tenant_id:
            form.instance.tenant = request.user.tenant
        form.save()
        messages.success(request, "Empresa salva com sucesso.")
        return redirect("config_empresa_lista")
    return render(request, "configuracoes/empresa_form.html", {"form": form, "instancia": instancia})


@requer_papel(Papel.SUPERADMIN)
def setor_lista(request):
    setores = Setor.objects.select_related("filial").prefetch_related("lideres").order_by("filial", "nome")
    return render(request, "configuracoes/setor_lista.html", {"setores": setores})


@requer_papel(Papel.SUPERADMIN)
def setor_form(request, pk=None):
    instancia = get_object_or_404(Setor, pk=pk) if pk else None
    form = SetorForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        if not form.instance.tenant_id:
            form.instance.tenant = request.user.tenant
        form.save()
        messages.success(request, "Setor salvo com sucesso.")
        return redirect("config_setor_lista")
    return render(request, "configuracoes/setor_form.html", {"form": form, "instancia": instancia})


@requer_papel(Papel.SUPERADMIN)
def filial_lista(request):
    filiais = Filial.objects.select_related("empresa").order_by("nome")
    return render(request, "configuracoes/filial_lista.html", {"filiais": filiais})


@requer_papel(Papel.SUPERADMIN)
def filial_form(request, pk=None):
    instancia = get_object_or_404(Filial, pk=pk) if pk else None
    form = FilialForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        filial = form.save(commit=False)
        if not filial.tenant_id:
            filial.tenant = request.user.tenant
        filial.save()
        messages.success(request, "Filial salva com sucesso.")
        return redirect("config_filial_lista")
    return render(request, "configuracoes/filial_form.html", {"form": form, "instancia": instancia})
