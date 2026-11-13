"""Operação do estoque: entrada/saída/transferência manuais, extrato, limites, reposição e balanço."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlencode

from cadastros.models import Produto
from contas.models import Modulo, Papel
from contas.permissoes import requer_papel
from tenancy.models import Almoxarifado

from . import services
from .models import Balanco, Movimentacao, SaldoEstoque, StatusBalanco, TipoMovimentacao
from .services import EstoqueInsuficiente, MovimentacaoInvalida

ESTOQUE = requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)


def _decimal(texto, nome="valor"):
    """'' -> None; '1,5' -> Decimal('1.5'); lixo -> ValueError com mensagem em português."""
    texto = (texto or "").strip().replace(",", ".")
    if not texto:
        return None
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f"Valor inválido em {nome}: «{texto}».")
    if not valor.is_finite():
        raise ValueError(f"Valor inválido em {nome}: «{texto}».")
    return valor


def _almoxarifados():
    return Almoxarifado.objects.filter(ativo=True).select_related("filial").order_by("filial__sigla", "nome")


def _almoxarifado_ou_none(valor):
    return _almoxarifados().filter(pk=valor).first() if str(valor or "").isdigit() else None


# ---------------------------------------------------------------- movimentação manual
@ESTOQUE
def movimentar(request):
    """Entrada, saída (baixa) ou transferência avulsa, com várias linhas; tudo ou nada."""
    produtos = list(Produto.objects.filter(ativo=True).order_by("nome").only("id", "nome", "especificacao", "codigo", "unidade_medida"))
    contexto = {
        "almoxarifados": _almoxarifados(), "produtos": produtos,
        "tipos": [("ENTRADA", "Entrada"), ("SAIDA", "Saída / baixa"), ("TRANSFERENCIA", "Transferência")],
        "tipo": request.POST.get("tipo", "ENTRADA"), "recentes": Movimentacao.objects.select_related(
            "produto", "origem__filial", "destino__filial", "responsavel")[:8],
    }
    contexto["post_produtos"] = list(zip(
        request.POST.getlist("produto"), request.POST.getlist("quantidade"), request.POST.getlist("valor")))
    if request.method != "POST":
        return render(request, "estoque/movimentar.html", contexto)

    tipo = request.POST.get("tipo")
    observacao = request.POST.get("observacao", "").strip()
    almox = _almoxarifado_ou_none(request.POST.get("almoxarifado"))
    destino = _almoxarifado_ou_none(request.POST.get("destino"))
    erros = []
    if tipo not in ("ENTRADA", "SAIDA", "TRANSFERENCIA"):
        erros.append("Escolha o tipo da movimentação.")
    if almox is None:
        erros.append("Escolha o almoxarifado.")
    if tipo == "TRANSFERENCIA" and destino is None:
        erros.append("Escolha o almoxarifado de destino.")
    if tipo in ("SAIDA", "TRANSFERENCIA") and not observacao:
        erros.append("Informe o motivo (observação).")

    linhas = []
    for pid, qtd, valor in zip(request.POST.getlist("produto"), request.POST.getlist("quantidade"),
                               request.POST.getlist("valor")):
        if not pid and not qtd.strip():
            continue
        produto = next((p for p in produtos if str(p.pk) == pid), None)
        try:
            quantidade, valor_unitario = _decimal(qtd, "quantidade"), _decimal(valor, "valor unitário")
        except ValueError as e:
            erros.append(str(e))
            continue
        if produto is None or quantidade is None or quantidade <= 0:
            erros.append("Cada linha precisa de produto e quantidade maior que zero.")
            continue
        linhas.append((produto, quantidade, valor_unitario or Decimal("0")))
    if not linhas and not erros:
        erros.append("Inclua ao menos um produto.")

    if not erros:
        try:
            with transaction.atomic():
                for produto, quantidade, valor_unitario in linhas:
                    if tipo == "ENTRADA":
                        services.registrar_entrada(
                            produto=produto, destino=almox, quantidade=quantidade, valor_unitario=valor_unitario,
                            responsavel=request.user, observacao=observacao or "Entrada manual")
                    elif tipo == "SAIDA":
                        services.registrar_saida(
                            produto=produto, origem=almox, quantidade=quantidade,
                            responsavel=request.user, observacao=observacao)
                    else:
                        services.transferir(
                            produto=produto, origem=almox, destino=destino, quantidade=quantidade,
                            responsavel=request.user, observacao=observacao)
        except (EstoqueInsuficiente, MovimentacaoInvalida) as e:
            erros.append(str(e))
    if erros:
        for e in dict.fromkeys(erros):
            messages.error(request, e)
        return render(request, "estoque/movimentar.html", contexto)
    messages.success(request, f"{len(linhas)} movimentação(ões) registrada(s).")
    return redirect("estoque_movimentar")


# ---------------------------------------------------------------- extrato (kardex)
@ESTOQUE
def extrato(request):
    movs = Movimentacao.objects.select_related("produto", "origem__filial", "destino__filial", "responsavel")
    almox = _almoxarifado_ou_none(request.GET.get("almoxarifado"))
    if almox:
        movs = movs.filter(Q(origem=almox) | Q(destino=almox))
    busca = request.GET.get("q", "").strip()
    if busca:
        movs = movs.filter(Q(produto__nome__icontains=busca) | Q(produto__codigo__icontains=busca))
    tipo = request.GET.get("tipo", "")
    if tipo in TipoMovimentacao.values:
        movs = movs.filter(tipo=tipo)
    for campo, lookup in (("de", "criado_em__date__gte"), ("ate", "criado_em__date__lte")):
        try:
            movs = movs.filter(**{lookup: datetime.strptime(request.GET.get(campo, ""), "%Y-%m-%d").date()})
        except ValueError:
            pass
    pagina = Paginator(movs.order_by("-criado_em", "-pk"), 50).get_page(request.GET.get("pagina"))
    filtros = {k: v for k, v in request.GET.items() if v and k != "pagina"}
    return render(request, "estoque/extrato.html", {
        "pagina": pagina, "almoxarifados": _almoxarifados(), "almoxarifado_id": str(almox.pk) if almox else "",
        "busca": busca, "tipo": tipo, "tipos": TipoMovimentacao.choices,
        "de": request.GET.get("de", ""), "ate": request.GET.get("ate", ""), "qs": urlencode(filtros),
    })


# ---------------------------------------------------------------- limites (mínimo / reposição / máximo)
@ESTOQUE
def limites(request):
    almox = _almoxarifado_ou_none(request.POST.get("almoxarifado") or request.GET.get("almoxarifado"))
    if almox and request.method == "POST":
        erros, gravados = [], 0
        entradas = list(zip(request.POST.getlist("produto"), request.POST.getlist("minimo"),
                            request.POST.getlist("reposicao"), request.POST.getlist("maximo")))
        novo = request.POST.get("novo_produto")
        if novo:
            entradas.append((novo, request.POST.get("novo_minimo", ""), request.POST.get("novo_reposicao", ""),
                             request.POST.get("novo_maximo", "")))
        with transaction.atomic():
            for pid, mn, rp, mx in entradas:
                produto = Produto.objects.filter(pk=pid).first() if str(pid).isdigit() else None
                if produto is None:
                    continue
                try:
                    services.definir_limites(
                        produto=produto, almoxarifado=almox, minimo=_decimal(mn, "mínimo"),
                        reposicao=_decimal(rp, "ponto de reposição"), maximo=_decimal(mx, "máximo"))
                    gravados += 1
                except (ValueError, MovimentacaoInvalida) as e:
                    erros.append(f"{produto.nome}: {e}")
            if erros:
                transaction.set_rollback(True)
        if erros:
            for e in erros:
                messages.error(request, e)
            messages.error(request, "Nada foi gravado; corrija os limites indicados.")
        else:
            messages.success(request, "Limites de estoque salvos.")
            return redirect(f"{request.path}?almoxarifado={almox.pk}")
    saldos = []
    if almox:
        busca = request.GET.get("q", "").strip()
        qs = SaldoEstoque.objects.filter(almoxarifado=almox).select_related("produto").order_by("produto__nome")
        if busca:
            qs = qs.filter(Q(produto__nome__icontains=busca) | Q(produto__codigo__icontains=busca))
        saldos = list(qs)
    sem_saldo = (Produto.objects.filter(ativo=True).exclude(saldos__almoxarifado=almox).order_by("nome")
                 if almox else [])
    return render(request, "estoque/limites.html", {
        "almoxarifados": _almoxarifados(), "almoxarifado": almox, "saldos": saldos, "sem_saldo": sem_saldo,
        "busca": request.GET.get("q", ""),
    })


# ---------------------------------------------------------------- reposição
@ESTOQUE
def reposicao(request):
    almox = _almoxarifado_ou_none(request.GET.get("almoxarifado"))
    saldos = SaldoEstoque.objects.select_related("produto", "almoxarifado__filial")
    if almox:
        saldos = saldos.filter(almoxarifado=almox)
    saldos = saldos.filter(Q(estoque_minimo__isnull=False) | Q(ponto_reposicao__isnull=False))
    linhas = [s for s in saldos.order_by("almoxarifado__filial__sigla", "almoxarifado__nome", "produto__nome")
              if s.precisa_repor]
    for s in linhas:
        s.critico = s.abaixo_do_minimo
        s.valor_estimado = s.sugestao_reposicao * s.custo_medio
    excesso = [s for s in saldos.order_by("produto__nome") if s.acima_do_maximo]
    return render(request, "estoque/reposicao.html", {
        "almoxarifados": _almoxarifados(), "almoxarifado_id": str(almox.pk) if almox else "",
        "linhas": linhas, "excesso": excesso,
        "total_estimado": sum((s.valor_estimado for s in linhas), Decimal("0")),
    })


# ---------------------------------------------------------------- balanço
@ESTOQUE
def balanco_lista(request):
    if request.method == "POST":
        almox = _almoxarifado_ou_none(request.POST.get("almoxarifado"))
        if almox is None:
            messages.error(request, "Escolha o almoxarifado do balanço.")
        else:
            try:
                balanco = services.abrir_balanco(
                    almoxarifado=almox, responsavel=request.user, observacao=request.POST.get("observacao", "").strip())
                return redirect("estoque_balanco_detalhe", pk=balanco.pk)
            except MovimentacaoInvalida as e:
                messages.error(request, str(e))
    balancos = Balanco.objects.select_related("almoxarifado__filial", "criado_por")[:50]
    return render(request, "estoque/balanco_lista.html", {"balancos": balancos, "almoxarifados": _almoxarifados()})


@ESTOQUE
def balanco_detalhe(request, pk):
    balanco = get_object_or_404(Balanco.objects.select_related("almoxarifado__filial", "criado_por"), pk=pk)
    editavel = balanco.status == StatusBalanco.EM_CONTAGEM
    if request.method == "POST" and editavel:
        acao = request.POST.get("acao")
        try:
            if acao in ("salvar", "aplicar"):
                contagens = {}
                for chave, valor in request.POST.items():
                    if chave.startswith("contagem_") and chave[9:].isdigit():
                        contagens[int(chave[9:])] = _decimal(valor, "contagem")
                services.registrar_contagem(balanco, contagens)
            if acao == "aplicar":
                n = services.aplicar_balanco(balanco, request.user)
                messages.success(request, f"Balanço aplicado: {n} item(ns) com saldo ajustado.")
            elif acao == "cancelar":
                services.cancelar_balanco(balanco, request.user)
                messages.success(request, "Balanço cancelado; nenhum saldo foi alterado.")
                return redirect("estoque_balanco_lista")
            else:
                messages.success(request, "Contagem salva.")
        except (ValueError, MovimentacaoInvalida) as e:
            messages.error(request, str(e))
        return redirect("estoque_balanco_detalhe", pk=balanco.pk)
    itens = list(balanco.itens.select_related("produto"))
    saldos_atuais = {s.produto_id: s.quantidade for s in SaldoEstoque.objects.filter(almoxarifado=balanco.almoxarifado)}
    for i in itens:
        i.saldo_atual = saldos_atuais.get(i.produto_id, Decimal("0"))
        i.mexeu = i.saldo_atual != i.quantidade_sistema  # houve movimento depois do início da contagem
    return render(request, "estoque/balanco_detalhe.html", {
        "balanco": balanco, "itens": itens, "editavel": editavel,
        "contados": sum(1 for i in itens if i.quantidade_contada is not None),
        "com_diferenca": sum(1 for i in itens if i.diferenca not in (None, 0)),
    })
