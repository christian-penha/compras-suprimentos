from decimal import Decimal, InvalidOperation

from cadastros.models import Produto

SESSAO = "carrinho"


def _carrinho(session):
    return session.setdefault(SESSAO, {})


def adicionar(session, codigo, quantidade):
    try:
        qtd = Decimal(str(quantidade).replace(",", "."))
    except (InvalidOperation, AttributeError):
        return False
    if qtd <= 0:
        return False
    carrinho = _carrinho(session)
    atual = Decimal(carrinho.get(codigo, "0"))
    carrinho[codigo] = str(atual + qtd)
    session.modified = True
    return True


def definir(session, codigo, quantidade):
    carrinho = _carrinho(session)
    try:
        qtd = Decimal(str(quantidade).replace(",", "."))
    except (InvalidOperation, AttributeError):
        return
    if qtd <= 0:
        carrinho.pop(codigo, None)
    else:
        carrinho[codigo] = str(qtd)
    session.modified = True


def remover(session, codigo):
    _carrinho(session).pop(codigo, None)
    session.modified = True


def limpar(session):
    session[SESSAO] = {}
    session.modified = True


def quantidade_total(session):
    return len(_carrinho(session))


def itens(session):
    carrinho = _carrinho(session)
    produtos = {p.codigo: p for p in Produto.objects.filter(codigo__in=carrinho.keys())}
    resultado = []
    for codigo, qtd in carrinho.items():
        produto = produtos.get(codigo)
        if produto:
            resultado.append({"produto": produto, "quantidade": Decimal(qtd)})
    return resultado
