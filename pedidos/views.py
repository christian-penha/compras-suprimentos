from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render

from cadastros.models import Categoria, Produto
from contas.models import Modulo, Papel
from contas.permissoes import requer_papel
from estoque.models import Movimentacao, SaldoEstoque, TipoMovimentacao
from estoque.services import EstoqueInsuficiente
from tenancy.models import Almoxarifado, CentroCusto, Evento, Filial, centros_da_filial

from . import cart
from .forms import PedidoForm, almoxarifados_do_usuario
from .models import EventoPedido, ItemPedido, OrigemAtendimento, Pedido, StatusPedido
from .services import (
    SemPermissao,
    TransicaoInvalida,
    aprovar_como_lider,
    entregar,
    pode_aprovar,
    saldo_no_almoxarifado,
    validar_centro_da_unidade,
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
    if usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE) or usuario.is_superuser:
        contexto["qtd_fila"] = Pedido.objects.filter(
            status__in=FILA_SUPRIMENTOS, ativo=True
        ).count()
    from compras.models import Cotacao, OrdemCompra, StatusCotacao, StatusOrdem
    from compras.services import itens_para_cotar, pode_decidir_cotacao

    e_estoque = usuario.is_superuser or usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE)
    e_compras = usuario.is_superuser or usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS)
    contexto.update({"e_estoque": e_estoque, "e_compras": e_compras})
    if e_compras:
        contexto["qtd_cotar"] = itens_para_cotar().count()
        contexto["qtd_ordens_abertas"] = OrdemCompra.objects.filter(
            status__in=[StatusOrdem.ABERTA, StatusOrdem.PARCIALMENTE_RECEBIDA]
        ).count()
    if usuario.alcadas_que_aprova.exists() or usuario.setores_liderados.exists() or usuario.is_superuser:
        contexto["qtd_alcada"] = sum(
            1 for c in Cotacao.objects.filter(status=StatusCotacao.AGUARDANDO_ALCADA)
            if pode_decidir_cotacao(usuario, c)
        )
    return render(request, "painel.html", contexto)


def _escopo_de_estoque(request):
    """Almoxarifados do usuário e, se ele filtrou por um deles, só esse. Sem nenhum cadastrado
    (ex.: superadmin), vale o estoque central como referência."""
    meus = almoxarifados_do_usuario(request.user) or list(Almoxarifado.objects.filter(central=True))
    sel = request.GET.get("almoxarifado", "")
    escolhido = [a for a in meus if str(a.pk) == sel]
    return meus, escolhido


def _saldos_por_produto(almoxarifados):
    """{produto_id: soma dos saldos nos almoxarifados}"""
    return {
        pk: total for pk, total in
        SaldoEstoque.objects.filter(almoxarifado__in=almoxarifados).order_by()
        .values_list("produto_id").annotate(total=Sum("quantidade"))
    }


def _estoque_dos_itens(itens, almoxarifados):
    """Para o resumo: saldo de cada produto em cada almoxarifado do usuário, e quantos produtos
    cada almoxarifado atenderia por inteiro. Preenche item["estoques"] e devolve a cobertura."""
    saldos = {}
    for pk, almox_id, quantidade in SaldoEstoque.objects.filter(
        almoxarifado__in=almoxarifados, produto__in=[i["produto"].pk for i in itens]
    ).values_list("produto_id", "almoxarifado_id", "quantidade"):
        saldos[(pk, almox_id)] = quantidade
    cobertura = {a.pk: 0 for a in almoxarifados}
    for item in itens:
        item["estoques"] = []
        for a in almoxarifados:
            saldo = saldos.get((item["produto"].pk, a.pk), 0)
            item["estoques"].append({"almoxarifado": a, "saldo": saldo, "atende": saldo >= item["quantidade"]})
            if saldo >= item["quantidade"]:
                cobertura[a.pk] += 1
    return [{"almoxarifado": a, "atende": cobertura[a.pk], "total": len(itens)} for a in almoxarifados]


@login_required
def catalogo(request):
    busca = request.GET.get("q", "").strip()
    categoria_id = request.GET.get("categoria", "")
    produtos = Produto.objects.filter(ativo=True).select_related("categoria")
    if busca:
        produtos = produtos.filter(nome__icontains=busca)
    if categoria_id:
        produtos = produtos.filter(categoria_id=categoria_id)

    meus, escolhido = _escopo_de_estoque(request)
    saldos = _saldos_por_produto(escolhido or meus)
    estoque = request.GET.get("estoque", "")
    com_saldo = [pk for pk, q in saldos.items() if q > 0]
    if estoque == "com":
        produtos = produtos.filter(pk__in=com_saldo)
    elif estoque == "sem":
        produtos = produtos.exclude(pk__in=com_saldo)
    else:
        estoque = ""
    pagina = Paginator(produtos.order_by("nome"), 48).get_page(request.GET.get("pagina"))
    produtos = list(pagina)
    for p in produtos:
        p.saldo = saldos.get(p.pk, 0)

    return render(
        request, "pedidos/catalogo.html",
        {
            "produtos": produtos,
            "pagina": pagina,
            "qs": urlencode({k: v for k, v in request.GET.items() if v and k != "pagina"}),
            "categorias": Categoria.objects.filter(ativo=True),
            "busca": busca,
            "categoria_id": categoria_id,
            "estoque": estoque,
            "meus_almoxarifados": meus,
            "almoxarifado_sel": escolhido[0] if escolhido else None,
            "qs_estoque": urlencode({k: v for k, v in request.GET.items() if v and k not in ("pagina", "estoque")}),
        },
    )


@login_required
def carrinho_adicionar(request):
    if request.method == "POST":
        produto_id = request.POST.get("produto", "")
        quantidade = request.POST.get("quantidade", "1")
        adicionado = cart.adicionar(request.session, produto_id, quantidade)
        if request.headers.get("x-requested-with") == "fetch":
            # o catálogo envia sem recarregar a página (mantém a posição de rolagem)
            return JsonResponse({
                "ok": bool(adicionado),
                "mensagem": "Item adicionado ao carrinho." if adicionado else "Quantidade inválida.",
                "qtd": cart.quantidade_total(request.session),
            }, status=200 if adicionado else 400)
        if adicionado:
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
    meus = almoxarifados_do_usuario(request.user)
    cobertura = _estoque_dos_itens(itens, meus)
    saldos = _saldos_por_produto(meus)
    for item in itens:
        item["saldo"] = saldos.get(item["produto"].pk, 0)
        item["sem_estoque"] = item["quantidade"] > item["saldo"]
    qtd_sem_estoque = sum(1 for i in itens if i["sem_estoque"])
    return render(
        request, "pedidos/carrinho.html",
        {
            "itens": itens,
            "cobertura": cobertura,
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
    if not form.almoxarifados:
        messages.error(
            request,
            "Você não tem nenhum almoxarifado cadastrado — peça ao administrador "
            "para definir os almoxarifados de onde você pode fazer pedidos.",
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

    cobertura = _estoque_dos_itens(itens_carrinho, form.almoxarifados)
    return render(request, "pedidos/checkout.html", {
        "form": form, "itens": itens_carrinho, "cobertura": cobertura,
        "estoque_json": {
            str(a.pk): {str(i["produto"].pk): float(e["saldo"]) for i in itens_carrinho for e in i["estoques"] if e["almoxarifado"].pk == a.pk}
            for a in form.almoxarifados
        },
    })


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


def _eventos_sem_repeticao(pedido):
    """Passos internos (cotação, alçada, OC) repetem o mesmo status; o histórico mostra só a mudança."""
    saida = []
    for e in pedido.eventos.select_related("usuario"):
        if not saida or saida[-1].para_status != e.para_status:
            saida.append(e)
    return saida


_ETAPAS = ["Enviado", "Aprovado", "Compra", "Separação", "Entrega", "Concluído"]
_ETAPA_ATUAL = {
    "ENVIADO": 0, "APROVADO_UNIDADE": 1, "AGUARDANDO_COMPRA": 2, "EM_SEPARACAO": 3,
    "ENTREGA_PARCIAL": 4, "ENTREGUE": 4, "CONCLUIDO": 6,
}


def _etapas_do_pedido(pedido, compras, tem_compra):
    """Trilha para o requisitante: o que já passou, onde está e o que falta."""
    if pedido.status not in _ETAPA_ATUAL:
        return []
    atual = _ETAPA_ATUAL[pedido.status]
    detalhe = {
        0: "Aguardando aprovação",
        2: compras[-1].get_andamento_display() if compras else "Em cotação",
        4: "Parcial" if pedido.status == "ENTREGA_PARCIAL" else "",
    }
    etapas = []
    for i, nome in enumerate(_ETAPAS):
        if i == 2 and not (tem_compra or atual == 2):
            continue
        estado = "feito" if i < atual else "atual" if i == atual else "pendente"
        etapas.append({"nome": nome, "estado": estado, "detalhe": detalhe.get(i, "") if estado == "atual" else ""})
    return etapas


def _compras_do_pedido(pedido):
    """Ordens de compra que atendem itens deste pedido, com o andamento informado pelo comprador."""
    from compras.models import OrdemCompra

    return list(
        OrdemCompra.objects.filter(cotacao__itens__item_pedido__pedido=pedido)
        .select_related("cotacao__fornecedor").distinct()
    )


@login_required
def pedido_detalhe(request, pk):
    pedido = get_object_or_404(
        Pedido.objects.select_related("almoxarifado_destino__filial", "centro_custo", "motivo"),
        pk=pk, ativo=True,
    )
    usuario = request.user
    # separar, entregar e definir o centro de consumo são do módulo Estoque; compras só consulta
    e_gestor = (
        usuario.is_superuser or usuario.tem_papel(Papel.SUPERADMIN)
        or usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE)
    )
    pode_consultar = e_gestor or usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS)
    if not (usuario == pedido.requisitante or pode_aprovar(usuario, pedido) or pode_consultar):
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
                if pedido.centro_custo_id is None and request.POST.get("centro_custo"):
                    centro = CentroCusto.objects.filter(pk=request.POST["centro_custo"], ativo=True).first()
                    validar_centro_da_unidade(centro, pedido.almoxarifado_destino.filial_id)
                    if centro is None:
                        raise TransicaoInvalida("Escolha o centro de custo.")
                    pedido.centro_custo = centro
                    pedido.save(update_fields=["centro_custo"])
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
            elif acao == "definir_centro" and e_gestor:
                if pedido.status in (StatusPedido.ENTREGUE, StatusPedido.CONCLUIDO):
                    raise TransicaoInvalida("O pedido já foi entregue; o centro de custo não pode mais mudar.")
                centro = CentroCusto.objects.filter(pk=request.POST.get("centro_custo") or 0, ativo=True).first()
                if centro is None:
                    raise TransicaoInvalida("Escolha o centro de custo.")
                validar_centro_da_unidade(centro, pedido.almoxarifado_destino.filial_id)
                antes = pedido.centro_custo
                pedido.centro_custo = centro
                pedido.save(update_fields=["centro_custo"])
                EventoPedido.objects.create(
                    pedido=pedido, de_status=pedido.status, para_status=pedido.status, usuario=usuario,
                    observacao=f"Centro de custo (consumo): {antes or 'sem centro'} → {centro}.",
                )
                messages.success(request, "Centro de custo do pedido definido.")
            elif acao == "concluir":
                transicionar(pedido, StatusPedido.CONCLUIDO, usuario)
                messages.success(request, "Recebimento confirmado. Pedido concluído.")
        except (TransicaoInvalida, SemPermissao, EstoqueInsuficiente) as e:
            messages.error(request, str(e))
        volta = request.POST.get("next", "")
        if volta and url_has_allowed_host_and_scheme(volta, allowed_hosts={request.get_host()}):
            return redirect(volta)
        return redirect("pedido_detalhe", pk=pedido.pk)

    compras = _compras_do_pedido(pedido)
    tem_compra = pedido.itens.filter(origem_atendimento=OrigemAtendimento.COMPRA).exists()
    return render(
        request, "pedidos/detalhe.html",
        {
            "pedido": pedido,
            "itens": pedido.itens.select_related("produto"),
            "tem_item_de_compra": tem_compra,
            "itens_entrega": [
                {"item": i, "saldo": saldo_no_almoxarifado(i.produto, pedido.almoxarifado_destino)}
                for i in pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE)
                .select_related("produto")
                if i.pendente > 0
            ],
            "eventos": _eventos_sem_repeticao(pedido),
            "pode_aprovar": pode_aprovar(usuario, pedido),
            "compras_do_pedido": compras,
            "etapas": _etapas_do_pedido(pedido, compras, tem_compra),
            "e_gestor": e_gestor,
            "ve_fornecedor": e_gestor or usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS),
            "centros": (
                centros_da_filial(pedido.almoxarifado_destino.filial_id, pedido.centro_custo_id) if e_gestor else []
            ),
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


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def pedido_mover(request, pk):
    """Arrastar no kanban. Só o que não precisa de informação muda direto; entrega pede dados
    (quantidades e centro de custo) numa janela, e o resto é definido pelo sistema."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "mensagem": "Método não permitido."}, status=405)
    pedido = get_object_or_404(Pedido, pk=pk, ativo=True)
    destino = request.POST.get("destino", "")
    try:
        if destino == StatusPedido.EM_SEPARACAO and pedido.status == StatusPedido.APROVADO_UNIDADE:
            transicionar(pedido, StatusPedido.EM_SEPARACAO, request.user)
            return JsonResponse({"ok": True, "mensagem": f"Pedido #{pedido.pk} em separação."})
        if destino in (StatusPedido.ENTREGA_PARCIAL, StatusPedido.ENTREGUE) and pedido.status in (
            StatusPedido.EM_SEPARACAO, StatusPedido.ENTREGA_PARCIAL,
        ):
            return JsonResponse({"ok": True, "modal": reverse("pedido_entrega_modal", args=[pedido.pk])})
        if destino == pedido.status:
            return JsonResponse({"ok": True, "mensagem": ""})
    except (TransicaoInvalida, SemPermissao) as e:
        return JsonResponse({"ok": False, "mensagem": str(e)}, status=400)
    if destino in (StatusPedido.AGUARDANDO_COMPRA, StatusPedido.APROVADO_UNIDADE):
        motivo = "Esse status é definido pelo sistema (aprovação e compra), não por arrastar."
    elif pedido.status == StatusPedido.APROVADO_UNIDADE:
        motivo = "Antes de entregar, inicie a separação (arraste para \"Em separação\")."
    else:
        motivo = "Esse movimento não é permitido para o status atual do pedido."
    return JsonResponse({"ok": False, "mensagem": motivo}, status=400)


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def pedido_entrega_modal(request, pk):
    """Trecho de HTML com o formulário de entrega (quantidades + centro de custo, se faltar)."""
    pedido = get_object_or_404(
        Pedido.objects.select_related("almoxarifado_destino__filial", "centro_custo"), pk=pk, ativo=True
    )
    return render(request, "estoque/_entrega_modal.html", {
        "pedido": pedido,
        "itens_entrega": [
            {
                "item": i, "saldo": saldo,
                "sugerido": min(i.pendente, saldo) if saldo > 0 else 0,
            }
            for i in pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE).select_related("produto")
            if i.pendente > 0
            for saldo in [saldo_no_almoxarifado(i.produto, pedido.almoxarifado_destino)]
        ],
        "centros": centros_da_filial(pedido.almoxarifado_destino.filial_id, pedido.centro_custo_id),
        "voltar": request.GET.get("voltar", ""),
    })


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def fila_suprimentos(request):
    pedidos = Pedido.objects.filter(
        status__in=FILA_SUPRIMENTOS, ativo=True
    ).select_related("requisitante", "almoxarifado_destino__filial")
    pedidos, ctx = _filtrar_pedidos(request, pedidos)
    pedidos = list(pedidos)
    visao = request.GET.get("visao", "cards")
    if visao == "kanban":  # a última coluna mostra o que acabou de ser entregue
        recentes = Pedido.objects.filter(status=StatusPedido.ENTREGUE, ativo=True).select_related(
            "requisitante", "almoxarifado_destino__filial"
        ).order_by("-atualizado_em")[:15]
        pedidos += list(recentes)
    colunas = [
        {"status": st, "titulo": StatusPedido(st).label, "pedidos": [p for p in pedidos if p.status == st]}
        for st in FILA_SUPRIMENTOS + ([StatusPedido.ENTREGUE] if visao == "kanban" else [])
    ]
    return render(request, "estoque/fila.html", {
        "pedidos": pedidos, "colunas": colunas,
        "visao": visao if visao in ("cards", "lista", "kanban") else "cards", **ctx,
    })


@requer_papel(Papel.ADMINISTRADOR, modulo=(Modulo.ESTOQUE, Modulo.COMPRAS))
def relatorio_eventos(request):
    filial_id = request.GET.get("filial", "")
    eventos = Evento.objects.select_related("filial").order_by("filial", "nome")
    if filial_id:
        eventos = eventos.filter(filial_id=filial_id)
    eventos = list(eventos)

    validos = Pedido.objects.filter(
        evento_id__in=[e.pk for e in eventos], ativo=True
    ).exclude(status__in=[StatusPedido.RASCUNHO, StatusPedido.RECUSADO])
    entregas = Movimentacao.objects.filter(
        pedido__in=validos, tipo__in=[TipoMovimentacao.SAIDA, TipoMovimentacao.TRANSFERENCIA]
    )

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


@requer_papel(Papel.ADMINISTRADOR, modulo=(Modulo.ESTOQUE, Modulo.COMPRAS))
def relatorio_centros(request):
    """Desembolso (notas) e consumo (baixas) por centro de custo, com filtros e gráficos.
    Os dois eixos ficam lado a lado e nunca são somados (RN-17)."""
    from datetime import date

    from contas.models import Setor

    from . import relatorio_custos as rc

    def _data(nome):
        try:
            return date.fromisoformat(request.GET.get(nome, ""))
        except ValueError:
            return None

    inicio, fim = _data("de"), _data("ate")
    filial_id = request.GET.get("filial", "")
    filial_id = filial_id if filial_id.isdigit() else ""
    setor_id = request.GET.get("setor", "")
    setor_id = setor_id if setor_id.isdigit() else ""
    centros_ids = {int(c) for c in request.GET.getlist("centro") if c.isdigit()}

    todos_c, todos_d = rc.linhas_consumo(), rc.linhas_desembolso()
    f = lambda linhas: rc.filtrar(linhas, filial_id, setor_id, centros_ids)  # noqa: E731
    no_periodo = lambda linhas: [  # noqa: E731
        l for l in linhas if (not inicio or l["data"] >= inicio) and (not fim or l["data"] <= fim)
    ]
    consumo, desembolso = f(no_periodo(todos_c)), f(no_periodo(todos_d))

    nome_centro = lambda c: str(c) if c else "Sem centro"  # noqa: E731
    por_centro = {}
    for eixo, linhas in (("consumo", consumo), ("desembolso", desembolso)):
        for l in linhas:
            linha = por_centro.setdefault(l["centro"], {"centro": l["centro"], "consumo": 0, "desembolso": 0})
            linha[eixo] += l["valor"]
    tabela = sorted(por_centro.values(), key=lambda x: -(x["consumo"] + x["desembolso"]))
    total_consumo = sum((l["valor"] for l in consumo), Decimal("0"))
    total_desembolso = sum((l["valor"] for l in desembolso), Decimal("0"))
    maior_centro = rc.agrupar(consumo, lambda l: l["centro"], nome_centro, limite=1)

    return render(request, "pedidos/relatorio_centros.html", {
        "total_consumo": total_consumo, "total_desembolso": total_desembolso,
        "qtd_entregas": len(consumo), "qtd_centros": len(por_centro),
        "maior_centro": maior_centro[0] if maior_centro else None,
        "consumo_unidade": rc.agrupar(consumo, lambda l: l["filial"], lambda f_: f_.nome if f_ else "Sem unidade"),
        "consumo_setor": rc.agrupar(consumo, lambda l: l["setor"], lambda s: s.nome if s else "Sem setor", limite=8),
        "consumo_centro": rc.agrupar(consumo, lambda l: l["centro"], nome_centro, limite=8),
        "desembolso_centro": rc.agrupar(desembolso, lambda l: l["centro"], nome_centro, limite=8),
        "serie": rc.serie_mensal(f(todos_c), f(todos_d)),
        "tabela": tabela,
        "filiais": Filial.objects.filter(ativo=True).order_by("nome"),
        "setores": Setor.objects.filter(ativo=True).select_related("filial").order_by("filial__nome", "nome"),
        "centros": CentroCusto.objects.filter(ativo=True).select_related("filial").order_by("filial__nome", "nome"),
        "filial_id": filial_id, "setor_id": setor_id, "centros_ids": centros_ids,
        "de": request.GET.get("de", ""), "ate": request.GET.get("ate", ""),
        "hoje": date.today().isoformat(),
    })
