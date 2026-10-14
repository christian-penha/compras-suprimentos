from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.utils.http import urlencode
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from cadastros.models import Categoria, Produto
from contas.models import Papel
from contas.permissoes import requer_papel
from estoque.models import Movimentacao, SaldoEstoque, TipoMovimentacao
from estoque.services import EstoqueInsuficiente
from tenancy.models import Evento, Filial

from . import cart
from .forms import PedidoForm
from .models import ItemPedido, OrigemAtendimento, Pedido, StatusPedido
from .services import (
    SemPermissao,
    TransicaoInvalida,
    aprovar_como_lider,
    entregar,
    pode_aprovar,
    saldo_central,
    transicionar,
)


FILA_SUPRIMENTOS = [
    StatusPedido.APROVADO_UNIDADE, StatusPedido.AGUARDANDO_COMPRA,
    StatusPedido.EM_SEPARACAO, StatusPedido.ENTREGA_PARCIAL,
]


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
            status__in=FILA_SUPRIMENTOS, ativo=True
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
        .values_list("produto_id", "quantidade")
    )
    pagina = Paginator(produtos.order_by("nome"), 48).get_page(request.GET.get("pagina"))
    produtos = list(pagina)
    for p in produtos:
        p.saldo_central = saldos.get(p.pk, 0)

    return render(
        request, "pedidos/catalogo.html",
        {
            "produtos": produtos,
            "pagina": pagina,
            "qs": urlencode({k: v for k, v in request.GET.items() if v and k != "pagina"}),
            "categorias": Categoria.objects.filter(ativo=True),
            "busca": busca,
            "categoria_id": categoria_id,
        },
    )


@login_required
def carrinho_adicionar(request):
    if request.method == "POST":
        produto_id = request.POST.get("produto", "")
        quantidade = request.POST.get("quantidade", "1")
        if cart.adicionar(request.session, produto_id, quantidade):
            messages.success(request, "Item adicionado ao carrinho.")
        else:
            messages.error(request, "Quantidade inválida.")
    return redirect(request.POST.get("voltar") or "catalogo")


@login_required
def carrinho_ver(request):
    if request.method == "POST":
        acao = request.POST.get("acao")
        produto_id = request.POST.get("produto", "")
        if acao == "remover":
            cart.remover(request.session, produto_id)
        elif acao == "atualizar":
            cart.definir(request.session, produto_id, request.POST.get("quantidade", "0"))
        elif acao == "limpar":
            cart.limpar(request.session)
        return redirect("carrinho_ver")
    itens = cart.itens(request.session)
    for item in itens:
        item["saldo"] = saldo_central(item["produto"])
        item["sem_estoque"] = item["quantidade"] > item["saldo"]
    qtd_sem_estoque = sum(1 for i in itens if i["sem_estoque"])
    return render(
        request, "pedidos/carrinho.html",
        {
            "itens": itens,
            "qtd_sem_estoque": qtd_sem_estoque,
            "qtd_do_estoque": len(itens) - qtd_sem_estoque,
        },
    )


@login_required
def checkout(request):
    itens_carrinho = cart.itens(request.session)
    if not itens_carrinho:
        messages.error(request, "Seu carrinho está vazio.")
        return redirect("catalogo")

    form = PedidoForm(request.POST or None, usuario=request.user)
    if not form.filiais.exists():
        messages.error(
            request,
            "Você não tem nenhuma unidade de destino cadastrada — peça ao administrador "
            "para definir as unidades em que você pode fazer pedidos.",
        )
        return redirect("carrinho_ver")
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
        if pedido.status == StatusPedido.APROVADO_UNIDADE:
            messages.success(
                request,
                f"Pedido #{pedido.pk} enviado — como líder do setor, ele já segue para suprimentos.",
            )
        else:
            messages.success(request, f"Pedido #{pedido.pk} enviado para aprovação.")
        return redirect("pedido_detalhe", pk=pedido.pk)

    return render(request, "pedidos/checkout.html", {"form": form, "itens": itens_carrinho})


def _filtrar_pedidos(request, pedidos):
    """Busca por número ou requisitante e filtro da unidade de destino."""
    busca = request.GET.get("q", "").strip()
    filial_id = request.GET.get("filial", "")
    filial_id = filial_id if filial_id.isdigit() else ""
    if busca:
        filtro = (Q(requisitante__nome_completo__icontains=busca) | Q(requisitante__username__icontains=busca)
                  | Q(observacao__icontains=busca))
        pedidos = pedidos.filter(filtro | Q(pk=busca)) if busca.isdigit() else pedidos.filter(filtro)
    if filial_id:
        pedidos = pedidos.filter(almoxarifado_destino__filial_id=filial_id)
    filiais = Filial.objects.filter(ativo=True).order_by("nome")
    return pedidos, {"busca": busca, "filial_id": filial_id, "filiais": filiais}


@login_required
def pedido_lista(request):
    pedidos = Pedido.objects.filter(requisitante=request.user, ativo=True).select_related(
        "almoxarifado_destino__filial", "motivo"
    )
    pedidos, ctx = _filtrar_pedidos(request, pedidos)
    return render(request, "pedidos/lista.html", {"pedidos": pedidos, **ctx})


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
                quantidades = {}
                for item in pedido.itens.all():
                    bruto = request.POST.get(f"qtd_{item.pk}", "").strip().replace(",", ".")
                    try:
                        quantidades[item.pk] = Decimal(bruto) if bruto else Decimal("0")
                    except InvalidOperation:
                        raise TransicaoInvalida(f"Quantidade inválida para {item.produto}.")
                entregar(pedido, usuario, quantidades)
                if pedido.status == StatusPedido.ENTREGUE:
                    messages.success(request, f"Pedido #{pedido.pk} entregue por completo — baixa registrada.")
                else:
                    messages.success(request, f"Entrega parcial registrada no pedido #{pedido.pk}.")
            elif acao == "concluir":
                transicionar(pedido, StatusPedido.CONCLUIDO, usuario)
                messages.success(request, "Recebimento confirmado. Pedido concluído.")
        except (TransicaoInvalida, SemPermissao, EstoqueInsuficiente) as e:
            messages.error(request, str(e))
        return redirect("pedido_detalhe", pk=pedido.pk)

    return render(
        request, "pedidos/detalhe.html",
        {
            "pedido": pedido,
            "itens": pedido.itens.select_related("produto"),
            "tem_item_de_compra": pedido.itens.filter(
                origem_atendimento=OrigemAtendimento.COMPRA
            ).exists(),
            "itens_entrega": [
                {"item": i, "saldo": saldo_central(i.produto)}
                for i in pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE)
                .select_related("produto")
                if i.pendente > 0
            ],
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
    pedidos, ctx = _filtrar_pedidos(request, pedidos)
    return render(request, "pedidos/aprovacoes.html", {"pedidos": pedidos, **ctx})


@requer_papel(Papel.ADMINISTRADOR)
def fila_suprimentos(request):
    pedidos = Pedido.objects.filter(
        status__in=FILA_SUPRIMENTOS, ativo=True
    ).select_related("requisitante", "almoxarifado_destino__filial")
    pedidos, ctx = _filtrar_pedidos(request, pedidos)
    pedidos = list(pedidos)
    visao = request.GET.get("visao", "cards")
    colunas = [
        {"titulo": StatusPedido(st).label, "pedidos": [p for p in pedidos if p.status == st]}
        for st in FILA_SUPRIMENTOS
    ]
    return render(request, "estoque/fila.html", {
        "pedidos": pedidos, "colunas": colunas,
        "visao": visao if visao in ("cards", "lista", "kanban") else "cards", **ctx,
    })


@requer_papel(Papel.ADMINISTRADOR)
def relatorio_eventos(request):
    filial_id = request.GET.get("filial", "")
    eventos = Evento.objects.select_related("filial").order_by("filial", "nome")
    if filial_id:
        eventos = eventos.filter(filial_id=filial_id)
    eventos = list(eventos)

    validos = Pedido.objects.filter(
        evento_id__in=[e.pk for e in eventos], ativo=True
    ).exclude(status__in=[StatusPedido.RASCUNHO, StatusPedido.RECUSADO])
    entregas = Movimentacao.objects.filter(pedido__in=validos, tipo=TipoMovimentacao.TRANSFERENCIA)

    n_pedidos = dict(
        validos.order_by().values_list("evento_id").annotate(n=Count("pk"))
    )

    # Valor de compra: cotações aprovadas (itens + frete rateado). O que foi comprado e depois
    # entregue do estoque já tem esse custo embutido no custo médio; por isso fica fora do
    # custo de estoque, senão contaria duas vezes.
    from compras.models import ItemCotacao, StatusCotacao

    compra, comprados, bases = {}, set(), {}
    cotados = ItemCotacao.objects.filter(
        item_pedido__pedido__in=validos, cotacao__status=StatusCotacao.APROVADA
    ).select_related("cotacao", "item_pedido__pedido")
    for ic in cotados:
        base = bases.setdefault(ic.cotacao_id, ic.cotacao.valor_itens)
        frete = ic.cotacao.valor_frete * ic.subtotal / base if base else 0
        evento_id = ic.item_pedido.pedido.evento_id
        compra[evento_id] = compra.get(evento_id, 0) + ic.subtotal + frete
        comprados.add((ic.item_pedido.pedido_id, ic.item_pedido.produto_id))

    custo, sem_custo = {}, set()
    for evento_id, pedido_id, produto_id, qtd, valor in entregas.values_list(
        "pedido__evento_id", "pedido_id", "produto_id", "quantidade", "valor_unitario"
    ):
        if (pedido_id, produto_id) in comprados:
            continue
        custo[evento_id] = custo.get(evento_id, 0) + qtd * valor
        if valor == 0:
            sem_custo.add(evento_id)

    a_comprar = dict(
        ItemPedido.objects.filter(pedido__in=validos, origem_atendimento=OrigemAtendimento.COMPRA)
        .order_by().values_list("pedido__evento_id").annotate(n=Count("pk"))
    )

    linhas = [
        {
            "evento": e,
            "pedidos": n_pedidos.get(e.pk, 0),
            "custo_estoque": custo.get(e.pk) or 0,
            "valor_compra": compra.get(e.pk) or 0,
            "sem_custo": e.pk in sem_custo,
            "a_comprar": a_comprar.get(e.pk, 0),
        }
        for e in eventos
    ]
    return render(
        request, "relatorios/eventos.html",
        {
            "linhas": linhas,
            "filiais": Filial.objects.filter(ativo=True).order_by("nome"),
            "filial_id": filial_id,
            "total_estoque": sum(l["custo_estoque"] for l in linhas),
            "total_pedidos": sum(l["pedidos"] for l in linhas),
            "total_a_comprar": sum(l["a_comprar"] for l in linhas),
            "total_compra": sum(l["valor_compra"] for l in linhas),
            "alguma_sem_custo": any(l["sem_custo"] for l in linhas),
        },
    )
