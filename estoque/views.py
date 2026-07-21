from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render

from openpyxl import load_workbook

from cadastros.models import Produto
from contas.models import Papel
from contas.permissoes import requer_papel

from .models import Almoxarifado, SaldoEstoque
from .services import registrar_entrada

SESSAO_PREVIA = "entrada_planilha_previa"


def _parse_planilha(arquivo):
    wb = load_workbook(arquivo, read_only=True, data_only=True)
    ws = wb.active
    ws.reset_dimensions()
    linhas = ws.iter_rows(values_only=True)
    cabecalho = [str(c or "").strip().lower() for c in next(linhas)]

    def coluna(*termos):
        for i, nome in enumerate(cabecalho):
            if any(t in nome for t in termos):
                return i
        return None

    col_codigo = coluna("código", "codigo")
    col_qtd = coluna("quantidade", "estoque", "qtd")
    col_valor = coluna("valor", "custo", "preço", "preco")
    if col_codigo is None or col_qtd is None:
        return None, [f"Colunas de código/quantidade não encontradas. Cabeçalho lido: {cabecalho}"]

    validas, erros = [], []
    for n, linha in enumerate(linhas, start=2):
        codigo = str(linha[col_codigo] or "").strip()
        if not codigo:
            continue
        produto = Produto.objects.filter(codigo=codigo).first()
        if produto is None:
            erros.append(f"Linha {n}: produto '{codigo}' não cadastrado.")
            continue
        try:
            quantidade = Decimal(str(linha[col_qtd] or "0").replace(",", "."))
        except InvalidOperation:
            erros.append(f"Linha {n}: quantidade inválida ('{linha[col_qtd]}') para {codigo}.")
            continue
        if quantidade <= 0:
            continue
        valor = Decimal("0")
        if col_valor is not None and linha[col_valor] not in (None, ""):
            try:
                valor = Decimal(str(linha[col_valor]).replace(",", "."))
            except InvalidOperation:
                erros.append(f"Linha {n}: valor inválido ('{linha[col_valor]}') para {codigo}.")
                continue
        validas.append(
            {"codigo": codigo, "produto": str(produto), "quantidade": str(quantidade), "valor": str(valor)}
        )
    return validas, erros


@requer_papel(Papel.ADMINISTRADOR)
def entrada_planilha(request):
    almoxarifados = Almoxarifado.objects.filter(ativo=True).select_related("filial")

    if request.method == "POST" and request.FILES.get("arquivo"):
        arquivo = request.FILES["arquivo"]
        validas, erros = _parse_planilha(arquivo)
        if validas is None:
            messages.error(request, erros[0])
            return redirect("entrada_planilha")
        request.session[SESSAO_PREVIA] = {
            "linhas": validas,
            "erros": erros,
            "arquivo": arquivo.name,
            "almoxarifado_id": request.POST.get("almoxarifado"),
            "observacao": request.POST.get("observacao", ""),
        }
        return redirect("entrada_planilha")

    if request.method == "POST" and request.POST.get("acao") == "aplicar":
        previa = request.session.pop(SESSAO_PREVIA, None)
        if not previa:
            messages.error(request, "Prévia expirada — envie a planilha novamente.")
            return redirect("entrada_planilha")
        destino = Almoxarifado.objects.get(pk=previa["almoxarifado_id"])
        for linha in previa["linhas"]:
            produto = Produto.objects.get(codigo=linha["codigo"])
            registrar_entrada(
                produto=produto, destino=destino,
                quantidade=Decimal(linha["quantidade"]),
                valor_unitario=Decimal(linha["valor"]),
                responsavel=request.user,
                arquivo_origem=previa["arquivo"],
                observacao=previa["observacao"],
            )
        messages.success(
            request,
            f"{len(previa['linhas'])} entrada(s) registradas em {destino} "
            f"(arquivo {previa['arquivo']}).",
        )
        return redirect("entrada_planilha")

    if request.method == "POST" and request.POST.get("acao") == "cancelar":
        request.session.pop(SESSAO_PREVIA, None)
        return redirect("entrada_planilha")

    previa = request.session.get(SESSAO_PREVIA)
    destino = None
    if previa:
        destino = Almoxarifado.objects.filter(pk=previa["almoxarifado_id"]).first()
    return render(
        request, "estoque/entrada_planilha.html",
        {"almoxarifados": almoxarifados, "previa": previa, "destino": destino},
    )


@requer_papel(Papel.ADMINISTRADOR)
def posicao_estoque(request):
    saldos = (
        SaldoEstoque.objects.filter(quantidade__gt=0)
        .select_related("produto", "almoxarifado__filial")
        .order_by("almoxarifado__filial__sigla", "produto__nome")
    )
    return render(request, "estoque/posicao.html", {"saldos": saldos})
