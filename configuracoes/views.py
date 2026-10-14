from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from cadastros.models import Fornecedor
from contas.models import Alcada, AprovadorAlcada, PapelAlcada, Papel, Setor
from contas.alcadas import (
    TabelaInvalida, criar_tabela_propria, ler_linhas, salvar_tabela, tabela_ativa, voltar_ao_padrao,
)
from contas.permissoes import requer_papel
from tenancy.models import Empresa, Evento, Filial

from .forms import EmpresaForm, EventoForm, FilialForm, FornecedorForm, SetorForm, UsuarioForm

Usuario = get_user_model()


def _filtros(request):
    """Busca por nome e filtro de unidade, comuns às telas de lista."""
    filial_id = request.GET.get("filial", "")
    return request.GET.get("q", "").strip(), filial_id if filial_id.isdigit() else ""


def _ctx(busca, filial_id):
    return {"busca": busca, "filial_id": filial_id, "filiais": Filial.objects.filter(ativo=True).order_by("nome")}


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
    usuarios = Usuario.objects.all().select_related("filial", "setor").prefetch_related("papeis")
    usuarios = usuarios.order_by("nome_completo", "username")
    busca, filial_id = _filtros(request)
    if busca:
        usuarios = usuarios.filter(
            Q(nome_completo__icontains=busca) | Q(username__icontains=busca) | Q(cargo__icontains=busca)
        )
    if filial_id:
        usuarios = usuarios.filter(filial_id=filial_id)
    return render(request, "configuracoes/usuario_lista.html", {"usuarios": usuarios, **_ctx(busca, filial_id)})


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
    busca, _ = _filtros(request)
    if busca:
        empresas = empresas.filter(
            Q(razao_social__icontains=busca) | Q(nome_fantasia__icontains=busca) | Q(cnpj__icontains=busca)
        )
    return render(request, "configuracoes/empresa_lista.html", {"empresas": empresas, "busca": busca})


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
    filial_id = request.GET.get("filial", "")
    busca = request.GET.get("q", "").strip()
    situacao = request.GET.get("situacao", "ativos")

    setores = Setor.objects.select_related("filial").prefetch_related("lideres", "membros")
    if filial_id:
        setores = setores.filter(filial_id=filial_id)
    if situacao == "ativos":
        setores = setores.filter(ativo=True)
    if busca:
        setores = setores.filter(
            Q(nome__icontains=busca)
            | Q(lideres__nome_completo__icontains=busca)
            | Q(lideres__username__icontains=busca)
            | Q(membros__nome_completo__icontains=busca)
            | Q(membros__username__icontains=busca)
        ).distinct()
    return render(
        request, "configuracoes/setor_lista.html",
        {
            "setores": setores.order_by("filial__nome", "nome"),
            "filiais": Filial.objects.filter(ativo=True).order_by("nome"),
            "filial_id": filial_id,
            "busca": busca,
            "situacao": situacao,
        },
    )


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
    busca, filial_id = _filtros(request)
    if busca:
        filiais = filiais.filter(Q(nome__icontains=busca) | Q(sigla__icontains=busca))
    if filial_id:
        filiais = filiais.filter(pk=filial_id)
    return render(request, "configuracoes/filial_lista.html", {"filiais": filiais, **_ctx(busca, filial_id)})


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


@requer_papel(Papel.SUPERADMIN)
def evento_lista(request):
    eventos = Evento.objects.select_related("filial").order_by("filial", "nome")
    busca, filial_id = _filtros(request)
    if busca:
        eventos = eventos.filter(nome__icontains=busca)
    if filial_id:
        eventos = eventos.filter(filial_id=filial_id)
    return render(request, "configuracoes/evento_lista.html", {"eventos": eventos, **_ctx(busca, filial_id)})


@requer_papel(Papel.SUPERADMIN)
def evento_form(request, pk=None):
    instancia = get_object_or_404(Evento, pk=pk) if pk else None
    form = EventoForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        if not form.instance.tenant_id:
            form.instance.tenant = request.user.tenant
        form.save()
        messages.success(request, "Evento salvo com sucesso.")
        return redirect("config_evento_lista")
    return render(request, "configuracoes/evento_form.html", {"form": form, "instancia": instancia})


@requer_papel(Papel.SUPERADMIN)
def alcada_lista(request):
    """Tabela de alçadas editável: padrão do grupo ou tabela própria de uma filial."""
    tenant = request.user.tenant
    filial_id = request.POST.get("filial") or request.GET.get("filial") or ""
    filial = Filial.objects.filter(pk=filial_id).first() if filial_id.isdigit() else None
    linhas_form = None

    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "salvar":
                linhas_form = list(zip(request.POST.getlist("maximo"), request.POST.getlist("papel")))
                linhas = ler_linhas(request.POST.getlist("maximo"), request.POST.getlist("papel"))
                mudou = salvar_tabela(tenant=tenant, filial=filial, linhas=linhas, usuario=request.user)
                messages.success(
                    request, "Tabela de alçadas salva." if mudou else "Nenhuma alteração na tabela."
                )
            elif acao == "criar_propria" and filial:
                criar_tabela_propria(tenant=tenant, filial=filial, usuario=request.user)
                messages.success(request, f"{filial.nome} agora tem tabela própria (cópia da do grupo).")
            elif acao == "voltar_padrao" and filial:
                voltar_ao_padrao(tenant=tenant, filial=filial)
                messages.success(request, f"{filial.nome} voltou a seguir a tabela padrão do grupo.")
            return redirect(f"{request.path}?filial={filial.pk}" if filial else request.path)
        except TabelaInvalida as e:
            messages.error(request, str(e))

    propria = tabela_ativa(tenant, filial) if filial else []
    usa_padrao = filial is not None and not propria
    tabela = tabela_ativa(tenant, None) if usa_padrao or filial is None else propria
    return render(request, "configuracoes/alcada_lista.html", {
        "filial": filial,
        "filiais": Filial.objects.filter(ativo=True).order_by("nome"),
        "tabela": tabela,
        "usa_padrao": usa_padrao,
        "linhas_form": linhas_form,
        "papeis": [(v, r) for v, r in PapelAlcada.choices],
        "filiais_com_tabela": set(
            Alcada.objects.filter(ativo=True, filial__isnull=False).values_list("filial_id", flat=True)
        ),
    })


@requer_papel(Papel.SUPERADMIN)
def alcada_form(request, pk):
    alcada = get_object_or_404(Alcada, pk=pk)
    por_filial = alcada.papel_aprovador == PapelAlcada.DIRETORIA_UNIDADE
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "remover":
            AprovadorAlcada.objects.filter(
                pk=request.POST.get("aprovador") or 0, alcada=alcada
            ).delete()
            messages.success(request, "Aprovador removido da faixa.")
        elif acao == "adicionar":
            usuario = Usuario.objects.filter(pk=request.POST.get("usuario") or 0, ativo_no_sistema=True).first()
            filial = Filial.objects.filter(pk=request.POST.get("filial") or 0).first() if por_filial else None
            if usuario is None:
                messages.error(request, "Escolha o usuário aprovador.")
            elif por_filial and filial is None:
                messages.error(request, "Diretoria de unidade exige informar a filial do diretor.")
            else:
                _, criado = AprovadorAlcada.objects.get_or_create(
                    alcada=alcada, usuario=usuario, filial=filial
                )
                messages.success(request, "Aprovador adicionado." if criado else "Esse aprovador já estava na faixa.")
        return redirect("config_alcada_editar", pk=alcada.pk)
    return render(request, "configuracoes/alcada_form.html", {
        "alcada": alcada,
        "por_filial": por_filial,
        "aprovadores": alcada.aprovadores.select_related("usuario", "filial"),
        "usuarios": Usuario.objects.filter(ativo_no_sistema=True).order_by("nome_completo", "username"),
        "filiais": Filial.objects.filter(ativo=True).order_by("nome"),
    })


@requer_papel(Papel.SUPERADMIN)
def fornecedor_lista(request):
    busca = request.GET.get("q", "").strip()
    fornecedores = Fornecedor.objects.all()
    if busca:
        fornecedores = fornecedores.filter(
            Q(razao_social__icontains=busca) | Q(nome_fantasia__icontains=busca) | Q(cnpj__icontains=busca)
        )
    return render(request, "configuracoes/fornecedor_lista.html", {"fornecedores": fornecedores, "busca": busca})


@requer_papel(Papel.SUPERADMIN)
def fornecedor_form(request, pk=None):
    instancia = get_object_or_404(Fornecedor, pk=pk) if pk else None
    form = FornecedorForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fornecedor salvo com sucesso.")
        return redirect("config_fornecedor_lista")
    return render(request, "configuracoes/fornecedor_form.html", {"form": form, "instancia": instancia})
