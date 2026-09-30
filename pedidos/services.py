from django.db import transaction

from contas.models import ModoAprovacaoSetor, RegraAprovacaoEmpresa
from estoque.models import SaldoEstoque
from estoque.services import transferir
from tenancy.models import Almoxarifado
from pedidos.models import (
    AprovacaoLider,
    EventoPedido,
    OrigemAtendimento,
    Pedido,
    StatusPedido,
)


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


def lideres_do_setor(usuario):
    """Líderes ativos do setor do usuário — quem pode aprovar os pedidos dele."""
    if not usuario.setor_id:
        return []
    return list(usuario.setor.lideres.filter(ativo_no_sistema=True))


def tem_lider_disponivel(requisitante):
    return bool(lideres_do_setor(requisitante))


def pode_aprovar(usuario, pedido):
    requisitante = pedido.requisitante
    if usuario == requisitante or not requisitante.setor_id:
        return False
    return requisitante.setor.lideres.filter(pk=usuario.pk, ativo_no_sistema=True).exists()


def modo_aprovacao_de(requisitante):
    if not requisitante.filial_id:
        return ModoAprovacaoSetor.QUALQUER_LIDER
    regra = RegraAprovacaoEmpresa.objects.filter(
        empresa_id=requisitante.filial.empresa_id
    ).first()
    return regra.modo_aprovacao_setor if regra else ModoAprovacaoSetor.QUALQUER_LIDER


def aprovacao_pendente(pedido):
    """Ainda falta aprovação de líder para o pedido avançar?"""
    lideres = lideres_do_setor(pedido.requisitante)
    if not lideres:
        return True
    if modo_aprovacao_de(pedido.requisitante) == ModoAprovacaoSetor.QUALQUER_LIDER:
        return not pedido.aprovacoes_lider.exists()
    ja_aprovaram = set(pedido.aprovacoes_lider.values_list("lider_id", flat=True))
    return any(lider.pk not in ja_aprovaram for lider in lideres)


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
        if not pedido.requisitante.setor_id:
            raise TransicaoInvalida(
                "Você não está vinculado a nenhum setor — peça ao administrador para "
                "cadastrar o seu setor antes de enviar pedidos."
            )
        if not tem_lider_disponivel(pedido.requisitante):
            raise TransicaoInvalida(
                "O seu setor não tem líder ativo para aprovar pedidos — contate o administrador."
            )

    if novo_status in (StatusPedido.APROVADO_UNIDADE, StatusPedido.RECUSADO):
        if not pode_aprovar(usuario, pedido):
            raise SemPermissao("Usuário não é líder do setor deste requisitante.")
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


@transaction.atomic
def aprovar_como_lider(pedido, usuario, observacao=""):
    """Registra o voto de um líder de setor. O pedido só avança para APROVADO_UNIDADE
    quando a regra da empresa estiver satisfeita (um líder ou todos)."""
    if pedido.status != StatusPedido.ENVIADO:
        raise TransicaoInvalida("Pedido não está aguardando aprovação do setor.")
    if not pode_aprovar(usuario, pedido):
        raise SemPermissao("Usuário não é líder do setor deste requisitante.")

    _, criado = AprovacaoLider.objects.get_or_create(pedido=pedido, lider=usuario)
    if criado:
        EventoPedido.objects.create(
            pedido=pedido, de_status=pedido.status, para_status=pedido.status,
            usuario=usuario, observacao=observacao or "Aprovação do líder de setor registrada.",
        )

    if not aprovacao_pendente(pedido):
        return transicionar(pedido, StatusPedido.APROVADO_UNIDADE, usuario, observacao)
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
