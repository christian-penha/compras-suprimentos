from decimal import Decimal, InvalidOperation

from cadastros.models import Produto

SESSAO = "carrinho"


def _carrinho(session):
    return session.setdefault(SESSAO, {})


def adicionar(session, produto_id, quantidade):
    try:
        qtd = Decimal(str(quantidade).replace(",", "."))
    except (InvalidOperation, AttributeError):
        return False
    if qtd <= 0 or not produto_id:
        return False
    carrinho = _carrinho(session)
    atual = Decimal(carrinho.get(produto_id, "0"))
    carrinho[produto_id] = str(atual + qtd)
    session.modified = True
    return True


def definir(session, produto_id, quantidade):
    carrinho = _carrinho(session)
    try:
        qtd = Decimal(str(quantidade).replace(",", "."))
    except (InvalidOperation, AttributeError):
        return
    if qtd <= 0:
        carrinho.pop(produto_id, None)
    else:
        carrinho[produto_id] = str(qtd)
    session.modified = True


def remover(session, produto_id):
    _carrinho(session).pop(produto_id, None)
    session.modified = True


def limpar(session):
    session[SESSAO] = {}
    session.modified = True


def quantidade_total(session):
    # conta o que itens() resolve de fato: chave órfã (produto apagado) não pode inflar o badge
    return len(itens(session))


def itens(session):
    carrinho = _carrinho(session)
    ids = [k for k in carrinho if str(k).isdigit()]
    produtos = {str(p.pk): p for p in Produto.objects.filter(pk__in=ids)}
    resultado = []
    for produto_id, qtd in carrinho.items():
        produto = produtos.get(str(produto_id))
        if produto:
            resultado.append({"produto": produto, "quantidade": Decimal(qtd)})
    return resultado
