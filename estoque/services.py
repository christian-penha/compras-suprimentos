from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    Balanco, DebitoAlmoxarifado, ItemBalanco, Movimentacao, SaldoEstoque, StatusBalanco, TipoMovimentacao,
)


class EstoqueInsuficiente(Exception):
    pass


class MovimentacaoInvalida(Exception):
    pass


def _positiva(quantidade):
    quantidade = Decimal(quantidade)
    if quantidade <= 0:
        raise MovimentacaoInvalida("A quantidade precisa ser maior que zero.")
    return quantidade


def _saldo(produto, almoxarifado):
    saldo, _ = SaldoEstoque.objects.select_for_update().get_or_create(
        produto=produto, almoxarifado=almoxarifado
    )
    return saldo


@transaction.atomic
def registrar_entrada(*, produto, destino, quantidade, responsavel, valor_unitario=Decimal("0"),
                      tipo=TipoMovimentacao.ENTRADA, pedido=None, arquivo_origem="", observacao=""):
    quantidade = _positiva(quantidade)
    valor_unitario = Decimal(valor_unitario)
    if valor_unitario < 0:
        raise MovimentacaoInvalida("O valor unitário não pode ser negativo.")
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
def registrar_saida(*, produto, origem, quantidade, responsavel, pedido=None, observacao="", centro_custo=None):
    quantidade = _positiva(quantidade)
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
        observacao=observacao, centro_custo=centro_custo,
    )


@transaction.atomic
def transferir(*, produto, origem, destino, quantidade, responsavel, pedido=None, observacao="", centro_custo=None):
    quantidade = _positiva(quantidade)
    if origem.pk == destino.pk:
        raise MovimentacaoInvalida("Origem e destino precisam ser diferentes.")
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
        observacao=observacao, centro_custo=centro_custo,
    )


@transaction.atomic
def registrar_ajuste(*, produto, almoxarifado, nova_quantidade, responsavel, observacao=""):
    """Leva o saldo à quantidade informada, registrando a diferença como AJUSTE (None se não mudou)."""
    nova_quantidade = Decimal(nova_quantidade)
    if nova_quantidade < 0:
        raise MovimentacaoInvalida("A quantidade contada não pode ser negativa.")
    saldo = _saldo(produto, almoxarifado)
    diferenca = nova_quantidade - saldo.quantidade
    if diferenca == 0:
        return None
    saldo.quantidade = nova_quantidade
    saldo.save()
    return Movimentacao.objects.create(
        tipo=TipoMovimentacao.AJUSTE, produto=produto,
        destino=almoxarifado if diferenca > 0 else None,
        origem=almoxarifado if diferenca < 0 else None,
        quantidade=abs(diferenca), valor_unitario=saldo.custo_medio,
        responsavel=responsavel, observacao=observacao,
    )


@transaction.atomic
def definir_limites(*, produto, almoxarifado, minimo=None, reposicao=None, maximo=None):
    """Grava mínimo, ponto de reposição e máximo (None limpa), exigindo mínimo ≤ reposição ≤ máximo."""
    valores = [("mínimo", minimo), ("ponto de reposição", reposicao), ("máximo", maximo)]
    for nome, v in valores:
        if v is not None and Decimal(v) < 0:
            raise MovimentacaoInvalida(f"O {nome} não pode ser negativo.")
    informados = [Decimal(v) for _, v in valores if v is not None]
    if informados != sorted(informados):
        raise MovimentacaoInvalida("Os limites precisam respeitar: mínimo ≤ ponto de reposição ≤ máximo.")
    saldo = _saldo(produto, almoxarifado)
    saldo.estoque_minimo = None if minimo is None else Decimal(minimo)
    saldo.ponto_reposicao = None if reposicao is None else Decimal(reposicao)
    saldo.estoque_maximo = None if maximo is None else Decimal(maximo)
    saldo.save()
    return saldo


@transaction.atomic
def abrir_balanco(*, almoxarifado, responsavel, produtos=None, observacao=""):
    """Congela o saldo atual como base da contagem. Sem `produtos`, entram todos com saldo ou limites."""
    if Balanco.objects.filter(almoxarifado=almoxarifado, status=StatusBalanco.EM_CONTAGEM).exists():
        raise MovimentacaoInvalida("Já existe um balanço em contagem neste almoxarifado; conclua ou cancele antes.")
    saldos = SaldoEstoque.objects.filter(almoxarifado=almoxarifado)
    if produtos is not None:
        saldos = saldos.filter(produto__in=produtos)
    else:
        saldos = saldos.exclude(quantidade=0, estoque_minimo__isnull=True, ponto_reposicao__isnull=True,
                                estoque_maximo__isnull=True)
    saldos = list(saldos.select_related("produto"))
    if not saldos:
        raise MovimentacaoInvalida("Nenhum produto para contar neste almoxarifado.")
    balanco = Balanco.objects.create(almoxarifado=almoxarifado, criado_por=responsavel, observacao=observacao)
    ItemBalanco.objects.bulk_create(
        ItemBalanco(balanco=balanco, produto=s.produto, quantidade_sistema=s.quantidade) for s in saldos
    )
    return balanco


@transaction.atomic
def registrar_contagem(balanco, contagens):
    """`contagens`: {item_id: Decimal | None}. None limpa a contagem do item."""
    balanco = Balanco.objects.select_for_update().get(pk=balanco.pk)
    if balanco.status != StatusBalanco.EM_CONTAGEM:
        raise MovimentacaoInvalida("Este balanço já foi encerrado.")
    for item in balanco.itens.filter(pk__in=contagens.keys()):
        valor = contagens[item.pk]
        if valor is not None and Decimal(valor) < 0:
            raise MovimentacaoInvalida(f"Contagem negativa para {item.produto}.")
        item.quantidade_contada = None if valor is None else Decimal(valor)
        item.save(update_fields=["quantidade_contada"])
    return balanco


@transaction.atomic
def aplicar_balanco(balanco, responsavel):
    """Ajusta o saldo dos itens contados para a quantidade contada (diferença vs. o saldo de agora,
    já que pode ter havido movimento durante a contagem). Itens sem contagem ficam como estão.
    Devolve quantos itens de fato mudaram de saldo."""
    balanco = Balanco.objects.select_for_update().get(pk=balanco.pk)
    if balanco.status != StatusBalanco.EM_CONTAGEM:
        raise MovimentacaoInvalida("Este balanço já foi encerrado.")
    itens = list(balanco.itens.select_related("produto"))
    if not any(i.quantidade_contada is not None for i in itens):
        raise MovimentacaoInvalida("Nenhum item foi contado.")
    ajustados = 0
    for item in itens:
        if item.quantidade_contada is None:
            continue
        atual = _saldo(item.produto, balanco.almoxarifado).quantidade
        mov = registrar_ajuste(
            produto=item.produto, almoxarifado=balanco.almoxarifado, nova_quantidade=item.quantidade_contada,
            responsavel=responsavel, observacao=f"Balanço #{balanco.pk}",
        )
        item.diferenca_aplicada = item.quantidade_contada - atual
        item.save(update_fields=["diferenca_aplicada"])
        ajustados += 1 if mov else 0
    balanco.status = StatusBalanco.APLICADO
    balanco.encerrado_por, balanco.encerrado_em = responsavel, timezone.now()
    balanco.save()
    return ajustados


@transaction.atomic
def cancelar_balanco(balanco, responsavel):
    balanco = Balanco.objects.select_for_update().get(pk=balanco.pk)
    if balanco.status != StatusBalanco.EM_CONTAGEM:
        raise MovimentacaoInvalida("Este balanço já foi encerrado.")
    balanco.status = StatusBalanco.CANCELADO
    balanco.encerrado_por, balanco.encerrado_em = responsavel, timezone.now()
    balanco.save()
