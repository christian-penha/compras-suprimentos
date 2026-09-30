from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from cadastros.models import Categoria, Produto
from contas.models import Papel
from contas.permissoes import requer_papel
from estoque.models import SaldoEstoque

from . import cart
from .forms import PedidoForm
from .models import ItemPedido, Pedido, StatusPedido
from .services import (
    SemPermissao,
    TransicaoInvalida,
    aprovar_como_lider,
    entregar,
    pode_aprovar,
    transicionar,
)


@login_required
def painel(request):
    usuario = request.user
    contexto = {
        "meus_pedidos": Pedido.objects.filter(requisitante=usuario, ativo=True)[:5],
        "qtd_meus_abertos": Pedido.objects.filter(requisitante=usuario, ativo=True)
        .exclude(status__in=[StatusPedido.CONCLUIDO, StatusPedido.RECUSADO]).count(),
    }
    setores_liderados = usuario.setores_liderados.values_list("id", flat=True)
    if setores_liderados or usuario.is_superuser:
        pedidos_para_aprovar = Pedido.objects.filter(status=StatusPedido.ENVIADO, ativo=True)
        if not usuario.is_superuser:
            pedidos_para_aprovar = pedidos_para_aprovar.filter(
                requisitante__setor_id__in=setores_liderados
            ).exclude(requisitante=usuario)
        contexto["qtd_aprovacoes"] = pedidos_para_aprovar.count()
    if usuario.tem_papel(Papel.ADMINISTRADOR) or usuario.is_superuser:
        contexto["qtd_fila"] = Pedido.objects.filter(
            status__in=[StatusPedido.APROVADO_UNIDADE, StatusPedido.EM_SEPARACAO], ativo=True
        ).count()
    return render(request, "painel.html", contexto)


@login_required
def catalogo(request):
    busca = request.GET.get("q", "").strip()
    categoria_id = request.GET.get("categoria", "")
    produtos = Produto.objects.filter(ativo=True).select_related("categoria")
    if busca:
        produtos = produtos.filter(nome__icontains=busca)
    if categoria_id:
        produtos = produtos.filter(categoria_id=categoria_id)

    saldos = dict(
        SaldoEstoque.objects.filter(almoxarifado__central=True)
        .values_list("produto__codigo", "quantidade")
    )
    produtos = produtos.order_by("nome")[:60]
    for p in produtos:
        p.saldo_central = saldos.get(p.codigo, 0)

    return render(
        request, "pedidos/catalogo.html",
        {
            "produtos": produtos,
            "categorias": Categoria.objects.filter(ativo=True),
            "busca": busca,
            "categoria_id": categoria_id,
        },
    )


@login_required
def carrinho_adicionar(request):
    if request.method == "POST":
        codigo = request.POST.get("codigo", "")
        quantidade = request.POST.get("quantidade", "1")
        if cart.adicionar(request.session, codigo, quantidade):
            messages.success(request, "Item adicionado ao carrinho.")
        else:
            messages.error(request, "Quantidade inválida.")
    return redirect(request.POST.get("voltar") or "catalogo")


@login_required
def carrinho_ver(request):
    if request.method == "POST":
        acao = request.POST.get("acao")
        codigo = request.POST.get("codigo", "")
        if acao == "remover":
            cart.remover(request.session, codigo)
        elif acao == "atualizar":
            cart.definir(request.session, codigo, request.POST.get("quantidade", "0"))
        elif acao == "limpar":
            cart.limpar(request.session)
        return redirect("carrinho_ver")
    return render(request, "pedidos/carrinho.html", {"itens": cart.itens(request.session)})


@login_required
def checkout(request):
    itens_carrinho = cart.itens(request.session)
    if not itens_carrinho:
        messages.error(request, "Seu carrinho está vazio.")
        return redirect("catalogo")

    form = PedidoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            pedido = form.save(commit=False)
            pedido.requisitante = request.user
            pedido.save()
            for item in itens_carrinho:
                ItemPedido.objects.create(
                    pedido=pedido, produto=item["produto"], quantidade=item["quantidade"]
                )
            try:
                transicionar(pedido, StatusPedido.ENVIADO, request.user)
            except (TransicaoInvalida, SemPermissao) as e:
                transaction.set_rollback(True)
                messages.error(request, str(e))
                return redirect("carrinho_ver")
        cart.limpar(request.session)
        messages.success(request, f"Pedido #{pedido.pk} enviado para aprovação.")
        return redirect("pedido_detalhe", pk=pedido.pk)

    return render(request, "pedidos/checkout.html", {"form": form, "itens": itens_carrinho})


@login_required
def pedido_lista(request):
    pedidos = Pedido.objects.filter(requisitante=request.user, ativo=True).select_related(
        "almoxarifado_destino__filial", "motivo"
    )
    return render(request, "pedidos/lista.html", {"pedidos": pedidos})


@login_required
def pedido_detalhe(request, pk):
    pedido = get_object_or_404(
        Pedido.objects.select_related("almoxarifado_destino__filial", "centro_custo", "motivo"),
        pk=pk, ativo=True,
    )
    usuario = request.user
    e_gestor = usuario.is_superuser or usuario.tem_papel(Papel.SUPERADMIN) or usuario.tem_papel(Papel.ADMINISTRADOR)
    if not (usuario == pedido.requisitante or pode_aprovar(usuario, pedido) or e_gestor):
        messages.error(request, "Você não tem acesso a esse pedido.")
        return redirect("painel")

    if request.method == "POST":
        acao = request.POST.get("acao")
        observacao = request.POST.get("observacao", "")
        try:
            if acao == "aprovar":
                aprovar_como_lider(pedido, usuario, observacao)
                if pedido.status == StatusPedido.ENVIADO:
                    messages.success(
                        request,
                        f"Aprovação registrada — pedido #{pedido.pk} aguarda os demais líderes.",
                    )
                else:
                    messages.success(request, f"Pedido #{pedido.pk} aprovado.")
            elif acao == "recusar":
                transicionar(pedido, StatusPedido.RECUSADO, usuario, observacao)
                messages.success(request, f"Pedido #{pedido.pk} recusado.")
            elif acao == "separar":
                transicionar(pedido, StatusPedido.EM_SEPARACAO, usuario)
                messages.success(request, f"Pedido #{pedido.pk} em separação.")
            elif acao == "entregar":
                entregar(pedido, usuario)
                messages.success(request, f"Pedido #{pedido.pk} entregue — baixa registrada.")
            elif acao == "concluir":
                transicionar(pedido, StatusPedido.CONCLUIDO, usuario)
                messages.success(request, "Recebimento confirmado. Pedido concluído.")
        except (TransicaoInvalida, SemPermissao) as e:
            messages.error(request, str(e))
        return redirect("pedido_detalhe", pk=pedido.pk)

    return render(
        request, "pedidos/detalhe.html",
        {
            "pedido": pedido,
            "itens": pedido.itens.select_related("produto"),
            "eventos": pedido.eventos.select_related("usuario"),
            "pode_aprovar": pode_aprovar(usuario, pedido),
            "e_gestor": e_gestor,
        },
    )


@login_required
def aprovacoes(request):
    pedidos = Pedido.objects.filter(status=StatusPedido.ENVIADO, ativo=True).select_related(
        "requisitante", "almoxarifado_destino__filial", "motivo"
    )
    if not request.user.is_superuser:
        pedidos = pedidos.filter(
            requisitante__setor_id__in=request.user.setores_liderados.values_list("id", flat=True)
        ).exclude(requisitante=request.user)
    return render(request, "pedidos/aprovacoes.html", {"pedidos": pedidos})


@requer_papel(Papel.ADMINISTRADOR)
def fila_suprimentos(request):
    pedidos = Pedido.objects.filter(
        status__in=[StatusPedido.APROVADO_UNIDADE, StatusPedido.EM_SEPARACAO], ativo=True
    ).select_related("requisitante", "almoxarifado_destino__filial")
    return render(request, "estoque/fila.html", {"pedidos": pedidos})
