from . import cart


def carrinho(request):
    if not request.user.is_authenticated:
        return {}
    return {"carrinho_qtd": cart.quantidade_total(request.session)}
