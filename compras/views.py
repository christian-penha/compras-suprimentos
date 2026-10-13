from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from cadastros.models import Fornecedor
from contas.models import AprovadorAlcada, Modulo, Papel
from contas.permissoes import requer_papel
from pedidos.services import SemPermissao, TransicaoInvalida
from estoque.services import EstoqueInsuficiente

from .models import Cotacao, NotaFiscal, OrdemCompra, StatusCotacao
from .nfe import NFeInvalida
from .services import (
    aprovadores_da_cotacao,
    cancelar_cotacao,
    confirmar_recebimento,
    criar_cotacao,
    decidir_cotacao,
    gerar_ordem_compra,
    itens_para_cotar,
    pode_decidir_cotacao,
    registrar_nota_xml,
    sugerir_conferencia,
)

ERROS_DE_NEGOCIO = (TransicaoInvalida, SemPermissao, NFeInvalida, EstoqueInsuficiente)

comprador = requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.COMPRAS)
estoquista = requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)


def _e_papel(usuario, modulo):
    return (
        usuario.is_superuser
        or usuario.tem_papel(Papel.SUPERADMIN)
        or usuario.tem_papel(Papel.ADMINISTRADOR, modulo)
    )


def _decimal(texto, padrao=None):
    bruto = (texto or "").strip().replace(".", "").replace(",", ".") if "," in (texto or "") else (texto or "").strip()
    try:
        return Decimal(bruto) if bruto else padrao
    except InvalidOperation:
        return None


# ------------------------------------------------------------------ fila e cotação

@comprador
def fila_compras(request):
    grupos = {}
    for item in itens_para_cotar():
        fornecedor = item.produto.fornecedor_padrao
        chave = fornecedor.pk if fornecedor else 0
        grupos.setdefault(chave, {"fornecedor": fornecedor, "itens": []})["itens"].append(item)
    ordenados = sorted(grupos.values(), key=lambda g: (g["fornecedor"] is None, str(g["fornecedor"])))
    return render(request, "compras/fila.html", {"grupos": ordenados})


@comprador
def cotacao_nova(request):
    ids = request.POST.getlist("item") if request.method == "POST" else request.GET.getlist("item")
    itens = list(itens_para_cotar().filter(pk__in=[i for i in ids if i.isdigit()]))
    if not itens:
        messages.error(request, "Selecione ao menos um item da fila para cotar.")
        return redirect("fila_compras")

    fornecedores = Fornecedor.objects.filter(ativo=True).order_by("razao_social")
    padrao = {i.produto.fornecedor_padrao_id for i in itens if i.produto.fornecedor_padrao_id}
    contexto = {
        "itens": itens,
        "fornecedores": fornecedores,
        "fornecedor_id": str(padrao.pop()) if len(padrao) == 1 else "",
    }

    if request.method == "POST" and "enviar" in request.POST:
        fornecedor = fornecedores.filter(pk=request.POST.get("fornecedor") or 0).first()
        frete = _decimal(request.POST.get("frete"), Decimal("0"))
        precos, erro = {}, None
        for item in itens:
            valor = _decimal(request.POST.get(f"preco_{item.pk}"))
            if valor is None or valor <= 0:
                erro = f"Informe o valor unitário de {item.produto.nome}."
                break
            precos[item.pk] = valor
        if fornecedor is None:
            erro = erro or "Escolha o fornecedor."
        if frete is None:
            erro = erro or "Frete inválido."
        if not erro:
            try:
                cotacao = criar_cotacao(
                    comprador=request.user, fornecedor=fornecedor, precos=precos,
                    valor_frete=frete, observacao=request.POST.get("observacao", "")[:300],
                )
            except ERROS_DE_NEGOCIO as e:
                erro = str(e)
            else:
                messages.success(request, f"Cotação #{cotacao.pk} registrada.")
                return redirect("cotacao_detalhe", pk=cotacao.pk)
        messages.error(request, erro)
        contexto["fornecedor_id"] = request.POST.get("fornecedor", "")
        contexto["precos"] = {i.pk: request.POST.get(f"preco_{i.pk}", "") for i in itens}
        contexto["frete"] = request.POST.get("frete", "")
    return render(request, "compras/cotacao_nova.html", contexto)


@login_required
def cotacao_lista(request):
    if not (_e_papel(request.user, Modulo.COMPRAS) or AprovadorAlcada.objects.filter(usuario=request.user).exists()):
        raise PermissionDenied
    status = request.GET.get("status", "")
    busca = request.GET.get("q", "").strip()
    cotacoes = Cotacao.objects.select_related("fornecedor", "comprador", "alcada").prefetch_related(
        "itens__item_pedido__produto"
    )
    if status:
        cotacoes = cotacoes.filter(status=status)
    if busca:
        cotacoes = cotacoes.filter(
            Q(fornecedor__razao_social__icontains=busca) | Q(fornecedor__nome_fantasia__icontains=busca)
            | Q(itens__item_pedido__produto__nome__icontains=busca)
        ).distinct()
    return render(request, "compras/cotacao_lista.html", {
        "cotacoes": cotacoes, "status": status, "busca": busca,
        "status_opcoes": StatusCotacao.choices,
    })


@login_required
def cotacao_detalhe(request, pk):
    cotacao = get_object_or_404(
        Cotacao.objects.select_related("fornecedor", "comprador", "alcada", "decidida_por"), pk=pk
    )
    usuario = request.user
    pode_decidir = pode_decidir_cotacao(usuario, cotacao)
    e_comprador = _e_papel(usuario, Modulo.COMPRAS)
    e_aprovador_da_faixa = aprovadores_da_cotacao(cotacao).filter(pk=usuario.pk).exists()
    if not (pode_decidir or e_comprador or e_aprovador_da_faixa):
        raise PermissionDenied

    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "aprovar":
                decidir_cotacao(cotacao, usuario, True)
                messages.success(request, f"Cotação #{cotacao.pk} aprovada.")
            elif acao == "recusar":
                decidir_cotacao(cotacao, usuario, False, request.POST.get("motivo", ""))
                messages.success(request, f"Cotação #{cotacao.pk} recusada — itens voltaram à fila.")
            elif acao == "cancelar" and e_comprador:
                cancelar_cotacao(cotacao, usuario)
                messages.success(request, f"Cotação #{cotacao.pk} cancelada.")
            elif acao == "gerar_ordem" and e_comprador:
                ordem = gerar_ordem_compra(cotacao, usuario)
                messages.success(request, f"Ordem de compra #{ordem.pk} gerada.")
                return redirect("ordem_detalhe", pk=ordem.pk)
            else:
                raise SemPermissao("Ação não permitida.")
        except ERROS_DE_NEGOCIO as e:
            messages.error(request, str(e))
        return redirect("cotacao_detalhe", pk=cotacao.pk)

    ordem = getattr(cotacao, "ordem", None)
    return render(request, "compras/cotacao_detalhe.html", {
        "cotacao": cotacao,
        "itens": cotacao.itens.select_related("item_pedido__produto", "item_pedido__pedido__requisitante"),
        "aprovadores": aprovadores_da_cotacao(cotacao),
        "pode_decidir": pode_decidir,
        "e_comprador": e_comprador,
        "ordem": ordem,
    })


@login_required
def aprovacoes_compra(request):
    minhas = [
        c for c in Cotacao.objects.filter(status=StatusCotacao.AGUARDANDO_ALCADA)
        .select_related("fornecedor", "comprador", "alcada")
        if pode_decidir_cotacao(request.user, c)
    ]
    return render(request, "compras/aprovacoes.html", {"cotacoes": minhas})


# ------------------------------------------------------------------ ordem e recebimento

@login_required
def ordem_lista(request):
    if not (_e_papel(request.user, Modulo.COMPRAS) or _e_papel(request.user, Modulo.ESTOQUE)):
        raise PermissionDenied
    status = request.GET.get("status", "")
    ordens = OrdemCompra.objects.select_related("cotacao__fornecedor")
    if status:
        ordens = ordens.filter(status=status)
    return render(request, "compras/ordem_lista.html", {"ordens": ordens, "status": status})


@login_required
def ordem_detalhe(request, pk):
    ordem = get_object_or_404(OrdemCompra.objects.select_related("cotacao__fornecedor"), pk=pk)
    if not (_e_papel(request.user, Modulo.COMPRAS) or _e_papel(request.user, Modulo.ESTOQUE)):
        raise PermissionDenied

    if request.method == "POST" and request.POST.get("acao") == "enviar_nota":
        if not _e_papel(request.user, Modulo.COMPRAS):
            raise PermissionDenied
        arquivo = request.FILES.get("xml")
        try:
            if arquivo is None:
                raise TransicaoInvalida("Escolha o arquivo XML da nota fiscal.")
            nota = registrar_nota_xml(ordem, arquivo.read(), request.user)
        except ERROS_DE_NEGOCIO as e:
            messages.error(request, str(e))
            return redirect("ordem_detalhe", pk=ordem.pk)
        messages.success(request, f"Nota {nota.numero or nota.chave_acesso} lançada. Confira o recebimento.")
        return redirect("recebimento", pk=ordem.pk, nota_pk=nota.pk)

    return render(request, "compras/ordem_detalhe.html", {
        "ordem": ordem,
        "cotacao": ordem.cotacao,
        "itens": ordem.cotacao.itens.select_related("item_pedido__produto", "item_pedido__pedido"),
        "notas": ordem.notas.all(),
        "recebimentos": ordem.recebimentos.select_related("conferido_por").prefetch_related(
            "itens__item_cotacao__item_pedido__produto"
        ),
        "pode_comprar": _e_papel(request.user, Modulo.COMPRAS),
        "pode_receber": _e_papel(request.user, Modulo.ESTOQUE),
    })


@estoquista
def recebimento(request, pk, nota_pk=None):
    ordem = get_object_or_404(OrdemCompra.objects.select_related("cotacao__fornecedor"), pk=pk)
    nota = get_object_or_404(NotaFiscal, pk=nota_pk, ordem=ordem) if nota_pk else None
    conferencia = sugerir_conferencia(ordem, nota)

    if request.method == "POST":
        conferencias = []
        for linha in conferencia["itens"]:
            item = linha["item"]
            qtd = _decimal(request.POST.get(f"qtd_{item.pk}"), Decimal("0"))
            idx = request.POST.get(f"linha_{item.pk}", "")
            if qtd is None:
                messages.error(request, f"Quantidade inválida para {item.item_pedido.produto}.")
                return redirect(request.path)
            conferencias.append({
                "item_id": item.pk, "quantidade": qtd,
                "linha_idx": int(idx) if idx.isdigit() else None,
            })
        try:
            confirmar_recebimento(ordem, request.user, conferencias, nota, request.POST.get("observacao", ""))
        except ERROS_DE_NEGOCIO as e:
            messages.error(request, str(e))
            return redirect(request.path)
        messages.success(request, f"Recebimento da OC #{ordem.pk} confirmado — material entrou no estoque.")
        return redirect("ordem_detalhe", pk=ordem.pk)

    return render(request, "compras/recebimento.html", {
        "ordem": ordem, "nota": nota, **conferencia,
    })


@login_required
def nota_xml(request, pk):
    nota = get_object_or_404(NotaFiscal, pk=pk)
    if not (_e_papel(request.user, Modulo.COMPRAS) or _e_papel(request.user, Modulo.ESTOQUE)):
        raise PermissionDenied
    if not nota.arquivo_xml:
        raise Http404
    resposta = FileResponse(
        nota.arquivo_xml.open("rb"), as_attachment=True,
        filename=f"nfe-{nota.chave_acesso or nota.pk}.xml", content_type="application/xml",
    )
    resposta["X-Content-Type-Options"] = "nosniff"
    return resposta
