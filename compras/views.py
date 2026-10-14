from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from cadastros.models import Fornecedor
from contas.models import AprovadorAlcada, Modulo, Papel
from contas.permissoes import requer_papel
from pedidos.services import SemPermissao, TransicaoInvalida
from tenancy.models import CentroCusto
from estoque.services import EstoqueInsuficiente

from .models import AndamentoOrdem, Cotacao, ItemCotacao, NotaFiscal, OrdemCompra, StatusCotacao, StatusOrdem
from .nfe import NFeInvalida
from .rodadas import criar_rodada, fechar_rodada, propostas_da_rodada
from .services import (
    aprovadores_da_cotacao,
    atualizar_andamento,
    cancelar_cotacao,
    confirmar_recebimento,
    criar_cotacao,
    definir_centros,
    decidir_cotacao,
    decidir_cotacao_por_item,
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

def _visao(request):
    """Modo de exibição das listas da área de compras: cards (padrão), lista ou kanban."""
    visao = request.GET.get("visao", "cards")
    return visao if visao in ("cards", "lista", "kanban") else "cards"


def _agrupar_itens(itens, por):
    """Agrupa itens (da fila ou de cotações) por fornecedor, pedido ou produto.
    Devolve [{"titulo", "subtitulo", "itens"}]."""
    grupos = {}
    for item in itens:
        pedido_item = getattr(item, "item_pedido", item)
        if por == "pedido":
            chave = pedido_item.pedido_id
            titulo = f"Pedido #{pedido_item.pedido_id}"
            sub = f"{pedido_item.pedido.requisitante} · {pedido_item.pedido.almoxarifado_destino.filial.sigla}"
        elif por == "produto":
            chave = pedido_item.produto_id
            titulo = pedido_item.produto.nome
            sub = pedido_item.produto.especificacao
        else:
            fornecedor = pedido_item.produto.fornecedor_padrao
            chave = fornecedor.pk if fornecedor else 0
            titulo = str(fornecedor) if fornecedor else "Sem fornecedor definido"
            sub = "Fornecedor padrão"
        grupos.setdefault(chave, {"titulo": titulo, "subtitulo": sub, "itens": []})["itens"].append(item)
    return sorted(grupos.values(), key=lambda g: g["titulo"])


@comprador
def fila_compras(request):
    por = request.GET.get("agrupar", "fornecedor")
    if por not in ("fornecedor", "pedido", "produto"):
        por = "fornecedor"
    busca = request.GET.get("q", "").strip()
    itens = itens_para_cotar()
    if busca:
        itens = itens.filter(
            Q(produto__nome__icontains=busca) | Q(pedido__requisitante__nome_completo__icontains=busca)
        )
    return render(request, "compras/fila.html", {
        "grupos": _agrupar_itens(itens, por), "agrupar": por, "busca": busca,
        "opcoes": [("fornecedor", "Por fornecedor"), ("pedido", "Por pedido"), ("produto", "Por produto")],
    })


def _propostas_do_post(request, itens, fornecedores):
    """Lê as colunas de fornecedor do formulário: listas paralelas `fornecedor`, `frete` e
    `preco_<item>`. Coluna sem fornecedor é ignorada; preço vazio = não cotou."""
    escolhidos = request.POST.getlist("fornecedor")
    fretes = request.POST.getlist("frete")
    propostas, erro = [], None
    for n, fid in enumerate(escolhidos):
        if not fid:
            continue
        fornecedor = fornecedores.filter(pk=fid if fid.isdigit() else 0).first()
        frete = _decimal(fretes[n] if n < len(fretes) else "", Decimal("0"))
        if fornecedor is None or frete is None:
            return None, "Fornecedor ou frete inválido."
        precos = {}
        for item in itens:
            lista = request.POST.getlist(f"preco_{item.pk}")
            texto = lista[n] if n < len(lista) else ""
            if not texto.strip():
                continue
            valor = _decimal(texto)
            if valor is None or valor <= 0:
                return None, f"Valor inválido para {item.produto.nome} em {fornecedor}."
            precos[item.pk] = valor
        propostas.append({"fornecedor": fornecedor, "precos": precos, "frete": frete})
    if not propostas:
        erro = "Escolha ao menos um fornecedor."
    elif any(not p["precos"] for p in propostas):
        erro = "Todo fornecedor escolhido precisa ter preço em ao menos um item."
    else:
        sem_preco = [i for i in itens if not any(i.pk in p["precos"] for p in propostas)]
        if sem_preco:
            erro = f"Nenhum fornecedor cotou {sem_preco[0].produto.nome}."
    return propostas, erro


@comprador
def solicitacao_cotacao(request):
    """Pedido padronizado ao fornecedor com os itens selecionados na fila (texto para
    copiar/imprimir). Ainda sem persistência: o registro e o envio por e-mail vêm depois."""
    ids = [i for i in request.GET.getlist("item") if i.isdigit()]
    itens = list(itens_para_cotar().filter(pk__in=ids))
    if not itens:
        messages.error(request, "Selecione ao menos um item da fila.")
        return redirect("fila_compras")

    fornecedores = Fornecedor.objects.filter(ativo=True).order_by("razao_social")
    padrao = {i.produto.fornecedor_padrao_id for i in itens if i.produto.fornecedor_padrao_id}
    escolhido = request.GET.get("fornecedor") or (str(padrao.pop()) if len(padrao) == 1 else "")
    fornecedor = fornecedores.filter(pk=escolhido if escolhido.isdigit() else 0).first()

    # o fornecedor quer o total por produto, não a quantidade de cada pedido
    produtos = {}
    for i in itens:
        linha = produtos.setdefault(i.produto_id, {"produto": i.produto, "quantidade": Decimal("0")})
        linha["quantidade"] += i.pendente
    linhas = sorted(produtos.values(), key=lambda l: l["produto"].nome)
    prazos = [i.pedido.data_desejada for i in itens if i.pedido.data_desejada]
    prazo = min(prazos) if prazos else None

    usuario = request.user
    texto = ["SOLICITAÇÃO DE COTAÇÃO", ""]
    if fornecedor:
        texto.append(f"Fornecedor: {fornecedor}")
    texto.append(f"Data: {timezone.localdate():%d/%m/%Y}")
    if prazo:
        texto.append(f"Entrega desejada até: {prazo:%d/%m/%Y}")
    texto += ["", "Itens:"]
    for n, l in enumerate(linhas, start=1):
        p = l["produto"]
        spec = f" — {p.especificacao}" if p.especificacao else ""
        texto.append(f"{n}. {p.nome}{spec}: {l['quantidade'].normalize():f} {p.get_unidade_medida_display().lower()}")
    texto += [
        "", "Por favor, informe o valor unitário de cada item, o frete, o prazo de entrega e as "
        "condições de pagamento.", "",
        f"Responsável: {usuario.nome_completo or usuario.username}"
        + (f" — {usuario.telefone}" if getattr(usuario, "telefone", "") else ""),
    ]
    return render(request, "compras/solicitacao.html", {
        "itens": itens, "linhas": linhas, "fornecedores": fornecedores, "fornecedor": fornecedor,
        "prazo": prazo, "texto": chr(10).join(texto),
    })


@comprador
def cotacao_nova(request):
    ids = request.POST.getlist("item") if request.method == "POST" else request.GET.getlist("item")
    itens = list(itens_para_cotar().filter(pk__in=[i for i in ids if i.isdigit()]))
    if not itens:
        messages.error(request, "Selecione ao menos um item da fila para cotar.")
        return redirect("fila_compras")

    fornecedores = Fornecedor.objects.filter(ativo=True).order_by("razao_social")
    padrao = {i.produto.fornecedor_padrao_id for i in itens if i.produto.fornecedor_padrao_id}
    # Cada coluna é um fornecedor; começa com o padrão (se houver um só) ou uma coluna vazia.
    colunas = [{"fornecedor_id": str(padrao.pop()) if len(padrao) == 1 else "", "frete": "", "precos": {}}]
    contexto = {
        "itens": itens, "fornecedores": fornecedores, "colunas": colunas,
        "centros": CentroCusto.objects.filter(ativo=True).select_related("filial"),
        "pedidos_cotados": [
            {
                "pedido": p,
                "centro_id": str(request.POST.get(f"centro_pedido_{p.pk}") or p.centro_custo_id or ""),
                "itens": [i.produto.nome for i in itens if i.pedido_id == p.pk],
            }
            for p in {i.pedido_id: i.pedido for i in itens}.values()
        ],
    }

    if request.method == "POST" and "enviar" in request.POST:
        propostas, erro = _propostas_do_post(request, itens, fornecedores)
        observacao = request.POST.get("observacao", "")[:300]
        centros_ok = {c.pk: c for c in CentroCusto.objects.filter(ativo=True)}
        # um centro por pedido: todos os itens do mesmo pedido herdam a escolha
        escolhidos = {
            i.pk: centros_ok.get(int(request.POST.get(f"centro_pedido_{i.pedido_id}") or 0)) for i in itens
        }
        if not erro and any(c is None for c in escolhidos.values()):
            erro = "Defina o centro de custo de todos os itens."
        if not erro:
            try:
                completa = len(propostas) == 1 and len(propostas[0]["precos"]) == len(itens)
                if len(propostas) == 1 and not completa:
                    erro = "Com um só fornecedor, todos os itens precisam de preço."
                elif completa:
                    p = propostas[0]
                    cotacao = criar_cotacao(
                        comprador=request.user, fornecedor=p["fornecedor"], precos=p["precos"],
                        valor_frete=p["frete"], observacao=observacao, centros=escolhidos,
                    )
                    ordem = OrdemCompra.objects.filter(cotacao=cotacao).first()
                    messages.success(
                        request,
                        f"Cotação #{cotacao.numero} registrada"
                        + (f" e ordem de compra #{ordem.numero} gerada." if ordem else "."),
                    )
                    return redirect("cotacao_detalhe", pk=cotacao.pk)
                else:
                    rodada = criar_rodada(
                        comprador=request.user, propostas=propostas, observacao=observacao, centros=escolhidos,
                    )
                    messages.success(request, "Propostas registradas — escolha o vencedor de cada item.")
                    return redirect("rodada_comparar", rodada=rodada)
            except ERROS_DE_NEGOCIO as e:
                erro = str(e)
        messages.error(request, erro)
        ids_f, fretes = request.POST.getlist("fornecedor"), request.POST.getlist("frete")
        contexto["colunas"] = [
            {
                "fornecedor_id": fid, "frete": fretes[n] if n < len(fretes) else "",
                "precos": {
                    i.pk: (request.POST.getlist(f"preco_{i.pk}") + [""] * len(ids_f))[n] for i in itens
                },
            }
            for n, fid in enumerate(ids_f)
        ] or colunas
    return render(request, "compras/cotacao_nova.html", contexto)


@comprador
def rodada_comparar(request, rodada):
    propostas = propostas_da_rodada(rodada)
    if not propostas:
        messages.error(request, "Esta comparação já foi encerrada.")
        return redirect("cotacao_lista")

    # item -> {cotacao_id: ItemCotacao}; o mais barato é a sugestão inicial.
    linhas = {}
    for c in propostas:
        for ic in c.itens.all():
            linha = linhas.setdefault(ic.item_pedido_id, {"item": ic.item_pedido, "qtd": ic.quantidade, "ofertas": {}})
            linha["ofertas"][c.pk] = ic
    for linha in linhas.values():
        linha["barato"] = min(linha["ofertas"], key=lambda cid: linha["ofertas"][cid].valor_unitario)
        linha["celulas"] = [linha["ofertas"].get(c.pk) for c in propostas]

    if request.method == "POST":
        if request.POST.get("acao") == "cancelar":
            for c in propostas:
                cancelar_cotacao(c, request.user)
            messages.success(request, "Comparação cancelada — itens voltaram à fila.")
            return redirect("fila_compras")
        escolhas = {}
        for item_id in linhas:
            valor = request.POST.get(f"vencedor_{item_id}", "")
            if valor.isdigit():
                escolhas[item_id] = int(valor)
        try:
            vencedoras = fechar_rodada(rodada, request.user, escolhas)
        except ERROS_DE_NEGOCIO as e:
            messages.error(request, str(e))
        else:
            messages.success(
                request, "Comparação encerrada: " + ", ".join(f"#{c.numero} ({c.fornecedor})" for c in vencedoras)
            )
            return redirect("cotacao_lista")

    return render(request, "compras/rodada.html", {"propostas": propostas, "linhas": list(linhas.values())})


@login_required
def cotacao_lista(request):
    if not (_e_papel(request.user, Modulo.COMPRAS) or AprovadorAlcada.objects.filter(usuario=request.user).exists()):
        raise PermissionDenied
    status = request.GET.get("status", "")
    busca = request.GET.get("q", "").strip()
    por = request.GET.get("agrupar", "cotacao")
    if por not in ("cotacao", "pedido", "produto"):
        por = "cotacao"
    base = Cotacao.objects.select_related("fornecedor", "comprador", "alcada").prefetch_related(
        "itens__item_pedido__produto"
    )
    if status:
        base = base.filter(status=status)
    if busca:
        base = base.filter(
            Q(fornecedor__razao_social__icontains=busca) | Q(fornecedor__nome_fantasia__icontains=busca)
            | Q(itens__item_pedido__produto__nome__icontains=busca)
        ).distinct()

    # Propostas em comparação aparecem como um cartão por rodada, não por fornecedor.
    rodadas = {}
    for c in base.filter(status=StatusCotacao.EM_COMPARACAO):
        r = rodadas.setdefault(c.rodada, {"rodada": c.rodada, "propostas": [], "itens": set()})
        r["propostas"].append(c)
        r["itens"].update(i.item_pedido.produto.nome for i in c.itens.all())
    cotacoes = base.exclude(status=StatusCotacao.EM_COMPARACAO)

    grupos = []
    if por != "cotacao":
        itens = ItemCotacao.objects.filter(cotacao__in=cotacoes).select_related(
            "cotacao__fornecedor", "item_pedido__produto", "item_pedido__pedido__requisitante",
            "item_pedido__pedido__almoxarifado_destino__filial",
        ).order_by("-cotacao__criada_em")
        grupos = _agrupar_itens(itens, por)
    cotacoes = list(cotacoes)
    colunas = [{"titulo": "Em comparação", "rodadas": list(rodadas.values()), "cotacoes": []}] + [
        {"titulo": rotulo, "rodadas": [], "cotacoes": [c for c in cotacoes if c.status == valor]}
        for valor, rotulo in StatusCotacao.choices if valor != StatusCotacao.EM_COMPARACAO
    ]
    for col in colunas:
        col["total"] = len(col["rodadas"]) + len(col["cotacoes"])
    return render(request, "compras/cotacao_lista.html", {
        "visao": _visao(request), "colunas": colunas,
        "cotacoes": cotacoes, "rodadas": list(rodadas.values()), "grupos": grupos,
        "status": status, "busca": busca, "agrupar": por,
        "opcoes": [("cotacao", "Por cotação"), ("pedido", "Por pedido"), ("produto", "Por produto")],
        "status_opcoes": StatusCotacao.choices,
    })


@login_required
def cotacao_detalhe(request, pk):
    cotacao = get_object_or_404(
        Cotacao.objects.select_related("fornecedor", "comprador", "alcada", "decidida_por"), pk=pk
    )
    if cotacao.status == StatusCotacao.EM_COMPARACAO:
        return redirect("rodada_comparar", rodada=cotacao.rodada)
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
                messages.success(request, f"Cotação #{cotacao.numero} aprovada.")
            elif acao == "decidir_itens":
                decisoes = {
                    ic.pk: request.POST.get(f"decisao_{ic.pk}", "") for ic in cotacao.itens.all()
                }
                depois = decidir_cotacao_por_item(cotacao, usuario, decisoes, request.POST.get("motivo", ""))
                if depois.status == StatusCotacao.RECUSADA:
                    messages.success(request, f"Cotação #{cotacao.numero} recusada.")
                else:
                    messages.success(request, f"Cotação #{cotacao.numero} aprovada (itens não aprovados tratados).")
            elif acao == "recusar":
                decidir_cotacao(cotacao, usuario, False, request.POST.get("motivo", ""))
                messages.success(request, f"Cotação #{cotacao.numero} recusada — itens voltaram à fila.")
            elif acao == "cancelar" and e_comprador:
                cancelar_cotacao(cotacao, usuario)
                messages.success(request, f"Cotação #{cotacao.numero} cancelada.")
            elif acao == "definir_centros" and e_comprador:
                livres = {c.pk: c for c in CentroCusto.objects.filter(ativo=True)}
                escolhas = {}
                for ic in cotacao.itens.select_related("item_pedido"):
                    centro = livres.get(int(request.POST.get(f"centro_pedido_{ic.item_pedido.pedido_id}") or 0))
                    if centro:
                        escolhas[ic.pk] = centro
                definir_centros(cotacao, escolhas, usuario)
                messages.success(request, "Centros de custo atualizados.")
            elif acao == "gerar_ordem" and e_comprador:
                ordem = gerar_ordem_compra(cotacao, usuario)
                messages.success(request, f"Ordem de compra #{ordem.numero} gerada.")
                return redirect("ordem_detalhe", pk=ordem.pk)
            else:
                raise SemPermissao("Ação não permitida.")
        except ERROS_DE_NEGOCIO as e:
            messages.error(request, str(e))
        else:
            if acao in ("aprovar", "decidir_itens"):
                ordem = OrdemCompra.objects.filter(cotacao=cotacao).first()
                if ordem:
                    messages.success(request, f"Ordem de compra #{ordem.numero} gerada automaticamente.")
        return redirect("cotacao_detalhe", pk=cotacao.pk)

    ordem = getattr(cotacao, "ordem", None)
    centros_por_pedido = {
        i.item_pedido.pedido_id: i.centro_custo_id for i in cotacao.itens.select_related("item_pedido")
    }
    return render(request, "compras/cotacao_detalhe.html", {
        "cotacao": cotacao,
        "itens": cotacao.itens.select_related("item_pedido__produto", "item_pedido__pedido__requisitante"),
        "aprovadores": aprovadores_da_cotacao(cotacao),
        "pode_decidir": pode_decidir,
        "e_comprador": e_comprador,
        "ordem": ordem,
        "centros": CentroCusto.objects.filter(ativo=True).select_related("filial"),
        "pedidos_cotacao": [
            {"pedido": p, "centro_id": centros_por_pedido.get(p.pk)}
            for p in {i.item_pedido.pedido_id: i.item_pedido.pedido for i in cotacao.itens.select_related("item_pedido__pedido")}.values()
        ],
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
    ordens = OrdemCompra.objects.select_related("cotacao__fornecedor").prefetch_related(
        "cotacao__itens__item_pedido"
    )
    if status:
        ordens = ordens.filter(status=status)
    ordens = list(ordens)
    colunas = [
        {"titulo": rotulo, "ordens": [o for o in ordens if o.status == valor]}
        for valor, rotulo in StatusOrdem.choices
    ]
    return render(request, "compras/ordem_lista.html", {
        "ordens": ordens, "status": status, "visao": _visao(request), "colunas": colunas,
    })


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

    if request.method == "POST" and request.POST.get("acao") == "andamento":
        if not _e_papel(request.user, Modulo.COMPRAS):
            raise PermissionDenied
        previsao = None
        bruto = request.POST.get("previsao", "").strip()
        try:
            if bruto:
                previsao = date.fromisoformat(bruto)
            atualizar_andamento(
                ordem, request.user, request.POST.get("andamento", ""),
                request.POST.get("observacao", ""), previsao,
            )
            messages.success(request, "Andamento da compra atualizado.")
        except ValueError:
            messages.error(request, "Data de previsão inválida.")
        except ERROS_DE_NEGOCIO as e:
            messages.error(request, str(e))
        return redirect("ordem_detalhe", pk=ordem.pk)

    return render(request, "compras/ordem_detalhe.html", {
        "ordem": ordem,
        "historico": ordem.historico.select_related("usuario"),
        "andamentos": [(v, r) for v, r in AndamentoOrdem.choices if v != AndamentoOrdem.RECEBIDA],
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
        messages.success(request, f"Recebimento da OC #{ordem.numero} confirmado — material entrou no estoque.")
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
