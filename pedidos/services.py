from django.db import transaction

from contas.models import VinculoAprovacao
from estoque.models import SaldoEstoque
from estoque.services import transferir
from tenancy.models import Almoxarifado
from pedidos.models import EventoPedido, OrigemAtendimento, Pedido, StatusPedido


class TransicaoInvalida(Exception):
    pass


class SemPermissao(Exception):
    pass


TRANSICOES = {
    StatusPedido.RASCUNHO: {StatusPedido.ENVIADO},
    StatusPedido.ENVIADO: {StatusPedido.APROVADO_UNIDADE, StatusPedido.RECUSADO},
    StatusPedido.APROVADO_UNIDADE: {StatusPedido.EM_SEPARACAO},
    StatusPedido.EM_SEPARACAO: {StatusPedido.ENTREGUE},
    StatusPedido.ENTREGUE: {StatusPedido.CONCLUIDO},
}


def aprovadores_de(usuario):
    return VinculoAprovacao.objects.filter(requisitante=usuario, ativo=True).order_by("ordem")


def pode_aprovar(usuario, pedido):
    return (
        usuario != pedido.requisitante
        and VinculoAprovacao.objects.filter(
            requisitante=pedido.requisitante, aprovador=usuario, ativo=True
        ).exists()
    )


@transaction.atomic
def transicionar(pedido, novo_status, usuario, observacao=""):
    atual = pedido.status
    if novo_status not in TRANSICOES.get(atual, set()):
        raise TransicaoInvalida(f"Transição {atual} → {novo_status} não é permitida.")

    if novo_status == StatusPedido.ENVIADO:
        if usuario != pedido.requisitante:
            raise SemPermissao("Apenas o requisitante envia o próprio pedido.")
        if not pedido.itens.exists():
            raise TransicaoInvalida("Pedido sem itens não pode ser enviado.")
        if not aprovadores_de(pedido.requisitante).exists():
            raise TransicaoInvalida(
                "Requisitante sem aprovador vinculado — cadastre o vínculo de aprovação."
            )

    if novo_status in (StatusPedido.APROVADO_UNIDADE, StatusPedido.RECUSADO):
        if not pode_aprovar(usuario, pedido):
            raise SemPermissao("Usuário não é aprovador vinculado deste requisitante.")
        if novo_status == StatusPedido.RECUSADO and not observacao.strip():
            raise TransicaoInvalida("Recusa exige motivo.")

    if novo_status == StatusPedido.CONCLUIDO and usuario != pedido.requisitante:
        raise SemPermissao("Apenas o requisitante confirma o recebimento.")

    EventoPedido.objects.create(
        pedido=pedido, de_status=atual, para_status=novo_status,
        usuario=usuario, observacao=observacao,
    )
    pedido.status = novo_status
    pedido.save(update_fields=["status", "atualizado_em"])

    if novo_status == StatusPedido.APROVADO_UNIDADE:
        classificar_itens(pedido)
    return pedido


def classificar_itens(pedido):
    central = Almoxarifado.objects.get(central=True)
    for item in pedido.itens.select_related("produto"):
        saldo = SaldoEstoque.objects.filter(
            produto=item.produto, almoxarifado=central
        ).first()
        disponivel = saldo.quantidade if saldo else 0
        item.origem_atendimento = (
            OrigemAtendimento.ESTOQUE if disponivel >= item.quantidade
            else OrigemAtendimento.COMPRA
        )
        item.save(update_fields=["origem_atendimento"])


@transaction.atomic
def entregar(pedido, usuario):
    central = Almoxarifado.objects.get(central=True)
    itens_estoque = pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE)
    for item in itens_estoque.select_related("produto"):
        transferir(
            produto=item.produto, origem=central, destino=pedido.almoxarifado_destino,
            quantidade=item.quantidade, responsavel=usuario, pedido=pedido,
        )
        item.quantidade_entregue = item.quantidade
        item.save(update_fields=["quantidade_entregue"])
    return transicionar(pedido, StatusPedido.ENTREGUE, usuario)


def saldo_central(produto):
    saldo = SaldoEstoque.objects.filter(produto=produto, almoxarifado__central=True).first()
    return saldo.quantidade if saldo else 0
