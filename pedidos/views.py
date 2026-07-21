from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from cadastros.models import Produto
from contas.models import Papel, VinculoAprovacao
from contas.permissoes import requer_papel
from estoque.models import SaldoEstoque

from .forms import PedidoForm
from .models import ItemPedido, Pedido, StatusPedido
from .services import (
    SemPermissao,
    TransicaoInvalida,
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
    if usuario.tem_papel(Papel.APROVADOR) or usuario.is_superuser:
        requisitantes = VinculoAprovacao.objects.filter(
            aprovador=usuario, ativo=True
        ).values_list("requisitante_id", flat=True)
        contexto["qtd_aprovacoes"] = Pedido.objects.filter(
            status=StatusPedido.ENVIADO, requisitante_id__in=requisitantes, ativo=True
        ).count()
    if usuario.tem_papel(Papel.ADMINISTRADOR) or usuario.is_superuser:
        contexto["qtd_fila"] = Pedido.objects.filter(
            status__in=[StatusPedido.APROVADO_UNIDADE, StatusPedido.EM_SEPARACAO], ativo=True
        ).count()
    return render(request, "painel.html", contexto)


@login_required
def pedido_novo(request):
    form = PedidoForm(request.POST or None)
    erros_itens = []
    if request.method == "POST" and form.is_valid():
        codigos = request.POST.getlist("item_codigo")
        quantidades = request.POST.getlist("item_quantidade")
        itens = []
        for n, (codigo, qtd) in enumerate(zip(codigos, quantidades), start=1):
            codigo = codigo.strip().split(" — ")[0]
            if not codigo and not qtd.strip():
                continue
            produto = Produto.objects.filter(codigo=codigo, ativo=True).first()
            if produto is None:
                erros_itens.append(f"Linha {n}: produto '{codigo}' não encontrado.")
                continue
            try:
                quantidade = Decimal(qtd.replace(",", "."))
                if quantidade <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                erros_itens.append(f"Linha {n}: quantidade inválida para {produto.nome}.")
                continue
            itens.append((produto, quantidade))
        if not itens and not erros_itens:
            erros_itens.append("Adicione ao menos um item ao pedido.")
        if not erros_itens:
            with transaction.atomic():
                pedido = form.save(commit=False)
                pedido.requisitante = request.user
                pedido.save()
                for produto, quantidade in itens:
                    ItemPedido.objects.create(
                        pedido=pedido, produto=produto, quantidade=quantidade
                    )
                try:
                    transicionar(pedido, StatusPedido.ENVIADO, request.user)
                except (TransicaoInvalida, SemPermissao) as e:
                    transaction.set_rollback(True)
                    messages.error(request, str(e))
                    return redirect("pedido_novo")
            messages.success(request, f"Pedido #{pedido.pk} enviado para aprovação.")
            return redirect("pedido_detalhe", pk=pedido.pk)

    produtos = list(
        Produto.objects.filter(ativo=True).values_list("codigo", "nome", "especificacao")
    )
    saldos = dict(
        SaldoEstoque.objects.filter(almoxarifado__central=True)
        .values_list("produto__codigo", "quantidade")
    )
    catalogo = [
        {"codigo": c, "rotulo": f"{c} — {n}" + (f" ({e})" if e else ""), "saldo": str(saldos.get(c, 0))}
        for c, n, e in produtos
    ]
    return render(
        request, "pedidos/novo.html",
        {"form": form, "catalogo": catalogo, "erros_itens": erros_itens},
    )


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
                transicionar(pedido, StatusPedido.APROVADO_UNIDADE, usuario, observacao)
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


@requer_papel(Papel.APROVADOR)
def aprovacoes(request):
    requisitantes = VinculoAprovacao.objects.filter(
        aprovador=request.user, ativo=True
    ).values_list("requisitante_id", flat=True)
    pedidos = Pedido.objects.filter(
        status=StatusPedido.ENVIADO, ativo=True, requisitante_id__in=requisitantes
    ).select_related("requisitante", "almoxarifado_destino__filial", "motivo")
    if request.user.is_superuser:
        pedidos = Pedido.objects.filter(status=StatusPedido.ENVIADO, ativo=True).select_related(
            "requisitante", "almoxarifado_destino__filial", "motivo"
        )
    return render(request, "pedidos/aprovacoes.html", {"pedidos": pedidos})


@requer_papel(Papel.ADMINISTRADOR)
def fila_suprimentos(request):
    pedidos = Pedido.objects.filter(
        status__in=[StatusPedido.APROVADO_UNIDADE, StatusPedido.EM_SEPARACAO], ativo=True
    ).select_related("requisitante", "almoxarifado_destino__filial")
    return render(request, "estoque/fila.html", {"pedidos": pedidos})
