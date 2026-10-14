"""Cadastro de produtos (suprimentos) e sugestão de produto novo (requisitante)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import urlencode

from cadastros.models import Categoria, Produto
from contas.models import Modulo, Papel
from contas.permissoes import requer_papel

from .forms import ProdutoForm, SugestaoProdutoForm
from .models import StatusSugestao, SugestaoProduto


@login_required
def sugerir_produto(request):
    form = SugestaoProdutoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sugestao = form.save(commit=False)
        sugestao.tenant = request.user.tenant
        sugestao.solicitante = request.user
        sugestao.save()
        messages.success(request, "Sugestão enviada ao suprimentos. Você acompanha o andamento aqui.")
        return redirect("sugerir_produto")
    minhas = SugestaoProduto.objects.filter(solicitante=request.user).select_related("produto")[:20]
    return render(request, "pedidos/sugerir_produto.html", {"form": form, "minhas": minhas})


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def sugestoes_lista(request):
    status = request.GET.get("status", StatusSugestao.PENDENTE)
    sugestoes = SugestaoProduto.objects.select_related("solicitante", "categoria", "produto")
    if status in StatusSugestao.values:
        sugestoes = sugestoes.filter(status=status)
    return render(request, "pedidos/sugestoes.html", {
        "sugestoes": sugestoes, "status": status, "status_opcoes": StatusSugestao.choices,
        "pendentes": SugestaoProduto.objects.filter(status=StatusSugestao.PENDENTE).count(),
    })


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def sugestao_recusar(request, pk):
    sugestao = get_object_or_404(SugestaoProduto, pk=pk, status=StatusSugestao.PENDENTE)
    motivo = request.POST.get("motivo", "").strip()
    if request.method != "POST" or not motivo:
        messages.error(request, "Informe o motivo da recusa.")
    else:
        sugestao.status = StatusSugestao.RECUSADO
        sugestao.motivo_recusa = motivo[:300]
        sugestao.tratada_por, sugestao.tratada_em = request.user, timezone.now()
        sugestao.save()
        messages.success(request, "Sugestão recusada.")
    return redirect("sugestoes_lista")


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def produto_lista(request):
    busca = request.GET.get("q", "").strip()
    categoria_id = request.GET.get("categoria", "")
    situacao = request.GET.get("situacao", "")
    produtos = Produto.objects.select_related("categoria", "fornecedor_padrao")
    if busca:
        produtos = produtos.filter(
            Q(nome__icontains=busca) | Q(codigo__icontains=busca) | Q(especificacao__icontains=busca)
        )
    if categoria_id.isdigit():
        produtos = produtos.filter(categoria_id=categoria_id)
    if situacao in ("ativo", "inativo"):
        produtos = produtos.filter(ativo=situacao == "ativo")
    filtros = {k: v for k, v in request.GET.items() if v and k != "pagina"}
    return render(request, "pedidos/produto_lista.html", {
        "pagina": Paginator(produtos, 48).get_page(request.GET.get("pagina")),
        "categorias": Categoria.objects.filter(ativo=True),
        "busca": busca, "categoria_id": categoria_id, "situacao": situacao,
        "qs": urlencode(filtros),
        "pendentes": SugestaoProduto.objects.filter(status=StatusSugestao.PENDENTE).count(),
    })


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def produto_form(request, pk=None):
    instancia = get_object_or_404(Produto, pk=pk) if pk else None
    sugestao = None
    sugestao_id = request.GET.get("sugestao") or request.POST.get("sugestao") or ""
    if instancia is None and sugestao_id.isdigit():
        sugestao = SugestaoProduto.objects.filter(pk=sugestao_id, status=StatusSugestao.PENDENTE).first()
    inicial = (
        {"nome": sugestao.nome, "especificacao": sugestao.especificacao, "categoria": sugestao.categoria}
        if sugestao else None
    )
    form = ProdutoForm(request.POST or None, request.FILES or None, instance=instancia, initial=inicial)
    if request.method == "POST" and form.is_valid():
        produto = form.save()
        if sugestao:
            sugestao.status, sugestao.produto = StatusSugestao.CADASTRADO, produto
            sugestao.tratada_por, sugestao.tratada_em = request.user, timezone.now()
            sugestao.save()
        messages.success(request, "Produto salvo.")
        return redirect("sugestoes_lista" if sugestao else "produto_lista")
    return render(request, "pedidos/produto_form.html", {"form": form, "instancia": instancia, "sugestao": sugestao})
