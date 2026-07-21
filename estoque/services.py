from decimal import Decimal

from django.db import transaction

from .models import DebitoAlmoxarifado, Movimentacao, SaldoEstoque, TipoMovimentacao


class EstoqueInsuficiente(Exception):
    pass


def _saldo(produto, almoxarifado):
    saldo, _ = SaldoEstoque.objects.select_for_update().get_or_create(
        produto=produto, almoxarifado=almoxarifado
    )
    return saldo


@transaction.atomic
def registrar_entrada(*, produto, destino, quantidade, responsavel, valor_unitario=Decimal("0"),
                      tipo=TipoMovimentacao.ENTRADA, pedido=None, arquivo_origem="", observacao=""):
    quantidade = Decimal(quantidade)
    valor_unitario = Decimal(valor_unitario)
    saldo = _saldo(produto, destino)
    if valor_unitario > 0:
        total_atual = saldo.quantidade * saldo.custo_medio
        total_novo = quantidade * valor_unitario
        saldo.custo_medio = (total_atual + total_novo) / (saldo.quantidade + quantidade)
    saldo.quantidade += quantidade
    saldo.save()
    return Movimentacao.objects.create(
        tipo=tipo, produto=produto, destino=destino, quantidade=quantidade,
        valor_unitario=valor_unitario, pedido=pedido, responsavel=responsavel,
        arquivo_origem=arquivo_origem, observacao=observacao,
    )


@transaction.atomic
def registrar_saida(*, produto, origem, quantidade, responsavel, pedido=None, observacao=""):
    quantidade = Decimal(quantidade)
    saldo = _saldo(produto, origem)
    if saldo.quantidade < quantidade:
        raise EstoqueInsuficiente(
            f"Saldo de {produto} em {origem} é {saldo.quantidade}; solicitado {quantidade}."
        )
    saldo.quantidade -= quantidade
    saldo.save()
    return Movimentacao.objects.create(
        tipo=TipoMovimentacao.SAIDA, produto=produto, origem=origem, quantidade=quantidade,
        valor_unitario=saldo.custo_medio, pedido=pedido, responsavel=responsavel,
        observacao=observacao,
    )


@transaction.atomic
def transferir(*, produto, origem, destino, quantidade, responsavel, pedido=None, observacao=""):
    quantidade = Decimal(quantidade)
    saldo_origem = _saldo(produto, origem)
    if saldo_origem.quantidade < quantidade:
        raise EstoqueInsuficiente(
            f"Saldo de {produto} em {origem} é {saldo_origem.quantidade}; solicitado {quantidade}."
        )
    custo = saldo_origem.custo_medio
    saldo_origem.quantidade -= quantidade
    saldo_origem.save()

    saldo_destino = _saldo(produto, destino)
    if custo > 0:
        total_atual = saldo_destino.quantidade * saldo_destino.custo_medio
        saldo_destino.custo_medio = (total_atual + quantidade * custo) / (
            saldo_destino.quantidade + quantidade
        )
    saldo_destino.quantidade += quantidade
    saldo_destino.save()

    if not origem.central and origem.filial_id != destino.filial_id:
        debito, _ = DebitoAlmoxarifado.objects.select_for_update().get_or_create(
            devedor=destino, credor=origem, produto=produto
        )
        debito.quantidade += quantidade
        debito.save()

    return Movimentacao.objects.create(
        tipo=TipoMovimentacao.TRANSFERENCIA, produto=produto, origem=origem, destino=destino,
        quantidade=quantidade, valor_unitario=custo, pedido=pedido, responsavel=responsavel,
        observacao=observacao,
    )
