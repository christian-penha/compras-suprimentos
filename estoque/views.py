import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils.http import urlencode

from openpyxl import load_workbook

from cadastros.models import Produto
from contas.models import Modulo, Papel
from contas.permissoes import requer_papel
from tenancy.models import Filial

from .models import Almoxarifado, Movimentacao, SaldoEstoque, TipoMovimentacao
from .services import registrar_entrada

SESSAO_PREVIA = "entrada_planilha_previa"


def _normalizar(texto):
    """Compara nomes de almoxarifado sem diferenciar acento, caixa e o tipo de traço."""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[-–—/|:]+", " ", texto).lower().split())


def _rotulo_almoxarifado(almoxarifado):
    return f"{almoxarifado.filial.sigla} — {almoxarifado.nome}"


def _mapa_de_almoxarifados(almoxarifados):
    """'CP — Geral' (ou 'cp - geral') -> almoxarifado. O nome sozinho também vale quando só
    existe um almoxarifado com ele (ex.: 'Estoque Central')."""
    mapa, por_nome = {}, {}
    for a in almoxarifados:
        mapa[_normalizar(_rotulo_almoxarifado(a))] = a
        por_nome.setdefault(_normalizar(a.nome), []).append(a)
    for nome, lista in por_nome.items():
        if len(lista) == 1:
            mapa.setdefault(nome, lista[0])
    return mapa


def _parse_planilha(arquivo, almoxarifados=(), padrao=None):
    """Lê a planilha de entrada. Cada linha vai para o almoxarifado da coluna "Almoxarifado";
    sem a coluna (ou com a célula vazia), vai para o almoxarifado padrão escolhido na tela."""
    wb = load_workbook(arquivo, read_only=True, data_only=True)
    ws = wb.active
    ws.reset_dimensions()
    linhas = ws.iter_rows(values_only=True)
    cabecalho = [str(c or "").strip().lower() for c in next(linhas, ())]

    def coluna(*termos, exceto=()):
        for i, nome in enumerate(cabecalho):
            if i in exceto:
                continue
            if any(t in nome for t in termos):
                return i
        return None

    col_almox = coluna("almoxarifado", "almox")
    sem_almox = (col_almox,) if col_almox is not None else ()
    col_codigo = coluna("código", "codigo", exceto=sem_almox)
    col_qtd = coluna("quantidade", "estoque", "qtd", exceto=sem_almox)
    col_valor = coluna("valor", "custo", "preço", "preco", exceto=sem_almox)
    if col_codigo is None or col_qtd is None:
        return None, [f"Colunas de código/quantidade não encontradas. Cabeçalho lido: {cabecalho}"]

    mapa = _mapa_de_almoxarifados(almoxarifados)
    validas, erros = [], []
    for n, linha in enumerate(linhas, start=2):
        linha = tuple(linha) + (None,) * max(0, len(cabecalho) - len(linha))
        codigo = str(linha[col_codigo] or "").strip()
        if not codigo:
            continue
        produto = Produto.objects.filter(codigo=codigo).first()
        if produto is None:
            erros.append(f"Linha {n}: produto '{codigo}' não cadastrado.")
            continue

        destino = padrao
        texto_almox = str(linha[col_almox] or "").strip() if col_almox is not None else ""
        if texto_almox:
            destino = mapa.get(_normalizar(texto_almox))
            if destino is None:
                erros.append(
                    f"Linha {n}: almoxarifado '{texto_almox}' não encontrado para {codigo} "
                    "(use o formato da planilha modelo, ex.: 'CP — Geral')."
                )
                continue
        elif destino is None:
            erros.append(f"Linha {n}: informe o almoxarifado de {codigo} ou escolha um almoxarifado padrão.")
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
            if valor < 0:
                erros.append(f"Linha {n}: valor negativo para {codigo}.")
                continue
        validas.append({
            "codigo": codigo, "produto": str(produto), "quantidade": str(quantidade), "valor": str(valor),
            "almoxarifado_id": destino.pk, "almoxarifado": _rotulo_almoxarifado(destino),
        })
    return validas, erros


def _resumo_por_destino(linhas):
    resumo = {}
    for l in linhas:
        r = resumo.setdefault(
            l["almoxarifado_id"], {"almoxarifado": l["almoxarifado"], "itens": 0, "valor": Decimal("0")}
        )
        r["itens"] += 1
        r["valor"] += Decimal(l["quantidade"]) * Decimal(l["valor"])
    return sorted(resumo.values(), key=lambda r: r["almoxarifado"])


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def entrada_modelo(request):
    """Planilha de exemplo com as colunas que a entrada espera e lista suspensa de almoxarifados."""
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.worksheet.datavalidation import DataValidation

    almoxarifados = list(
        Almoxarifado.objects.filter(ativo=True).select_related("filial").order_by("filial__sigla", "nome")
    )
    central = next((a for a in almoxarifados if a.central), almoxarifados[0] if almoxarifados else None)

    wb = Workbook()
    ws = wb.active
    ws.title = "Entrada"
    ws.append(["Código", "Produto (só para conferência)", "Almoxarifado", "Quantidade", "Valor unitário"])
    for celula in ws[1]:
        celula.font = Font(bold=True)
    for p in Produto.objects.exclude(codigo__isnull=True).order_by("codigo")[:3]:
        ws.append([p.codigo, p.nome, _rotulo_almoxarifado(central) if central else "", 10, 5.0])
    for col, largura in zip("ABCDE", (18, 44, 30, 14, 16)):
        ws.column_dimensions[col].width = largura
    ws.freeze_panes = "A2"

    lista = wb.create_sheet("Almoxarifados")
    lista.append(["Almoxarifado (use exatamente assim)", "Unidade"])
    for celula in lista[1]:
        celula.font = Font(bold=True)
    for a in almoxarifados:
        lista.append([_rotulo_almoxarifado(a), a.filial.nome])
    lista.column_dimensions["A"].width = 36
    lista.column_dimensions["B"].width = 36
    if almoxarifados:
        validacao = DataValidation(
            type="list", formula1=f"=Almoxarifados!$A$2:$A${len(almoxarifados) + 1}", allow_blank=True,
            showErrorMessage=True, errorTitle="Almoxarifado", error="Escolha um almoxarifado da lista.",
        )
        ws.add_data_validation(validacao)
        validacao.add("C2:C5000")

    resposta = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resposta["Content-Disposition"] = 'attachment; filename="modelo-entrada-de-material.xlsx"'
    wb.save(resposta)
    return resposta


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def entrada_planilha(request):
    almoxarifados = Almoxarifado.objects.filter(ativo=True).select_related("filial")

    if request.method == "POST" and request.FILES.get("arquivo"):
        arquivo = request.FILES["arquivo"]
        padrao_id = request.POST.get("almoxarifado", "")
        padrao = almoxarifados.filter(pk=padrao_id).first() if padrao_id.isdigit() else None
        validas, erros = _parse_planilha(arquivo, almoxarifados, padrao)
        if validas is None:
            messages.error(request, erros[0])
            return redirect("entrada_planilha")
        request.session[SESSAO_PREVIA] = {
            "linhas": validas,
            "erros": erros,
            "arquivo": arquivo.name,
            "almoxarifado_id": padrao.pk if padrao else None,
            "observacao": request.POST.get("observacao", ""),
        }
        return redirect("entrada_planilha")

    if request.method == "POST" and request.POST.get("acao") == "aplicar":
        previa = request.session.pop(SESSAO_PREVIA, None)
        if not previa:
            messages.error(request, "Prévia expirada — envie a planilha novamente.")
            return redirect("entrada_planilha")
        ids = {l["almoxarifado_id"] for l in previa["linhas"]}
        destinos = {a.pk: a for a in almoxarifados.filter(pk__in=ids)}
        if len(destinos) != len(ids):
            messages.error(request, "Um dos almoxarifados da prévia foi desativado — envie a planilha novamente.")
            return redirect("entrada_planilha")
        with transaction.atomic():  # ou entram todas as linhas, ou nenhuma
            for linha in previa["linhas"]:
                registrar_entrada(
                    produto=Produto.objects.get(codigo=linha["codigo"]),
                    destino=destinos[linha["almoxarifado_id"]],
                    quantidade=Decimal(linha["quantidade"]),
                    valor_unitario=Decimal(linha["valor"]),
                    responsavel=request.user,
                    arquivo_origem=previa["arquivo"],
                    observacao=previa["observacao"],
                )
        onde = f"em {next(iter(destinos.values()))}" if len(destinos) == 1 else f"em {len(destinos)} almoxarifados"
        messages.success(
            request, f"{len(previa['linhas'])} entrada(s) registradas {onde} (arquivo {previa['arquivo']})."
        )
        return redirect("entrada_planilha")

    if request.method == "POST" and request.POST.get("acao") == "cancelar":
        request.session.pop(SESSAO_PREVIA, None)
        return redirect("entrada_planilha")

    previa = request.session.get(SESSAO_PREVIA)
    if previa and previa["linhas"] and "almoxarifado_id" not in previa["linhas"][0]:
        request.session.pop(SESSAO_PREVIA, None)  # prévia no formato antigo (sem almoxarifado por linha)
        previa = None
    return render(
        request, "estoque/entrada_planilha.html",
        {
            "total_quantidade": sum((Decimal(l["quantidade"]) for l in previa["linhas"]), Decimal("0")) if previa else 0,
            "total_valor": sum((Decimal(l["quantidade"]) * Decimal(l["valor"]) for l in previa["linhas"]), Decimal("0")) if previa else 0,
            "recentes": Movimentacao.objects.filter(tipo=TipoMovimentacao.ENTRADA)
            .select_related("produto", "destino__filial")[:8],
            "almoxarifados": almoxarifados, "previa": previa,
            "destinos": _resumo_por_destino(previa["linhas"]) if previa else [],
            "filiais_destino": Filial.objects.filter(pk__in=almoxarifados.values("filial_id")).order_by("nome"),
        },
    )


SITUACOES = [
    ("sem_estoque", "Sem estoque"),
    ("abaixo", "Abaixo do mínimo"),
    ("atencao", "Em atenção"),
    ("ok", "Em dia"),
]


def _situacao(saldo):
    if saldo.quantidade <= 0:
        return "sem_estoque"
    if saldo.estoque_minimo is not None and saldo.quantidade < saldo.estoque_minimo:
        return "abaixo"
    if saldo.ponto_reposicao is not None and saldo.quantidade <= saldo.ponto_reposicao:
        return "atencao"
    return "ok"


@requer_papel(Papel.ADMINISTRADOR, modulo=Modulo.ESTOQUE)
def posicao_estoque(request):
    filial_id = request.GET.get("filial", "")
    almoxarifado_id = request.GET.get("almoxarifado", "")
    busca = request.GET.get("q", "").strip()
    situacao = request.GET.get("situacao", "")

    almoxarifados = Almoxarifado.objects.filter(ativo=True).select_related("filial")
    saldos = SaldoEstoque.objects.filter(almoxarifado__in=almoxarifados).select_related(
        "produto", "almoxarifado__filial"
    )
    if filial_id.isdigit():
        saldos = saldos.filter(almoxarifado__filial_id=filial_id)
        # o almoxarifado tem de ser da unidade escolhida; senão o filtro dele é descartado
        if almoxarifado_id.isdigit() and not almoxarifados.filter(pk=almoxarifado_id, filial_id=filial_id).exists():
            almoxarifado_id = ""
    if almoxarifado_id.isdigit():
        saldos = saldos.filter(almoxarifado_id=almoxarifado_id)
    if busca:
        saldos = saldos.filter(
            Q(produto__nome__icontains=busca)
            | Q(produto__codigo__icontains=busca)
            | Q(produto__especificacao__icontains=busca)
        )

    linhas = []
    for s in saldos.order_by("almoxarifado__filial__sigla", "almoxarifado__nome", "produto__nome"):
        s.situacao = _situacao(s)
        s.valor = s.quantidade * s.custo_medio
        linhas.append(s)

    contagem = {chave: 0 for chave, _ in SITUACOES}
    for s in linhas:
        contagem[s.situacao] += 1
    kpis = {
        "itens": sum(1 for s in linhas if s.quantidade > 0),
        "abaixo": contagem["abaixo"] + contagem["sem_estoque"],
        "atencao": contagem["atencao"],
        "valor": sum((s.valor for s in linhas), Decimal("0")),
    }

    if situacao in contagem:
        linhas = [s for s in linhas if s.situacao == situacao]
    pagina = Paginator(linhas, 50).get_page(request.GET.get("pagina"))

    sem_pagina = {k: v for k, v in request.GET.items() if v and k != "pagina"}
    sem_situacao = {k: v for k, v in sem_pagina.items() if k != "situacao"}
    return render(request, "estoque/posicao.html", {
        "pagina": pagina,
        "total": len(linhas),
        "kpis": kpis,
        "contagem": contagem,
        "situacoes": SITUACOES,
        "situacao": situacao,
        "filiais": Filial.objects.filter(ativo=True).order_by("nome"),
        "almoxarifados": almoxarifados.order_by("filial__sigla", "nome"),
        "filial_id": filial_id,
        "almoxarifado_id": almoxarifado_id,
        "almoxarifado_sel": almoxarifados.filter(pk=almoxarifado_id).first() if almoxarifado_id.isdigit() else None,
        "busca": busca,
        "qs_pagina": urlencode(sem_pagina),
        "qs_situacao": urlencode(sem_situacao),
    })
