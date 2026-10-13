from decimal import Decimal

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
    StatusPedido.APROVADO_UNIDADE: {StatusPedido.EM_SEPARACAO, StatusPedido.AGUARDANDO_COMPRA},
    StatusPedido.EM_SEPARACAO: {StatusPedido.ENTREGUE, StatusPedido.ENTREGA_PARCIAL},
    StatusPedido.ENTREGA_PARCIAL: {StatusPedido.ENTREGUE, StatusPedido.ENTREGA_PARCIAL},
    StatusPedido.ENTREGUE: {StatusPedido.CONCLUIDO},
}


def lideres_do_setor(usuario):
    """Líderes ativos do setor do usuário que podem aprovar os pedidos dele.

    O próprio usuário fica de fora: ninguém aprova o próprio pedido, então contá-lo
    deixaria o pedido sem aprovador (ou, no modo TODOS_LIDERES, esperando um voto
    que nunca pode acontecer).
    """
    if not usuario.setor_id:
        return []
    return list(usuario.setor.lideres.filter(ativo_no_sistema=True).exclude(pk=usuario.pk))


def tem_lider_disponivel(requisitante):
    return bool(lideres_do_setor(requisitante))


def e_lider_do_proprio_setor(usuario):
    return bool(usuario.setor_id) and usuario.setor.lideres.filter(
        pk=usuario.pk, ativo_no_sistema=True
    ).exists()


def aprovacao_do_setor_dispensada(requisitante):
    """Líder que pede e não tem outro líder no setor não passa por aprovação do setor."""
    return e_lider_do_proprio_setor(requisitante) and not tem_lider_disponivel(requisitante)


def pode_aprovar(usuario, pedido):
    requisitante = pedido.requisitante
    if usuario == requisitante or not requisitante.setor_id:
        return False
    return requisitante.setor.lideres.filter(pk=usuario.pk, ativo_no_sistema=True).exists()


def modo_aprovacao_de(requisitante):
    if requisitante.setor_id and requisitante.setor.modo_aprovacao:
        return requisitante.setor.modo_aprovacao
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
        if not tem_lider_disponivel(pedido.requisitante) and not e_lider_do_proprio_setor(
            pedido.requisitante
        ):
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

    _aplicar_transicao(pedido, novo_status, usuario, observacao)

    if novo_status == StatusPedido.ENVIADO and aprovacao_do_setor_dispensada(pedido.requisitante):
        _aplicar_transicao(
            pedido, StatusPedido.APROVADO_UNIDADE, usuario,
            "Aprovação do setor dispensada: você é o líder do setor.",
        )
    return pedido


def _aplicar_transicao(pedido, novo_status, usuario, observacao):
    EventoPedido.objects.create(
        pedido=pedido, de_status=pedido.status, para_status=novo_status,
        usuario=usuario, observacao=observacao,
    )
    pedido.status = novo_status
    pedido.save(update_fields=["status", "atualizado_em"])
    if novo_status == StatusPedido.APROVADO_UNIDADE:
        classificar_itens(pedido)
        if not pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE).exists():
            _aplicar_transicao(
                pedido, StatusPedido.AGUARDANDO_COMPRA, usuario,
                "Nenhum item tem estoque suficiente — pedido aguardando compra.",
            )


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
def entregar(pedido, usuario, quantidades=None):
    """Entrega itens do estoque central ao destino do pedido.

    `quantidades` é {item_id: Decimal} para entrega parcial; sem ele entrega todo o
    saldo pendente. O pedido vira ENTREGUE quando nenhum item de estoque tem pendência,
    senão ENTREGA_PARCIAL (e pode receber novas entregas).
    """
    if pedido.status not in (StatusPedido.EM_SEPARACAO, StatusPedido.ENTREGA_PARCIAL):
        raise TransicaoInvalida("Só é possível entregar pedidos em separação.")

    central = Almoxarifado.objects.get(central=True)
    itens = list(
        pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE)
        .select_related("produto").select_for_update(of=("self",))
    )
    a_entregar = []
    for item in itens:
        qtd = item.pendente if quantidades is None else quantidades.get(item.pk, Decimal("0"))
        if qtd < 0 or qtd > item.pendente:
            raise TransicaoInvalida(
                f"{item.produto}: a quantidade a entregar deve estar entre 0 e {item.pendente:f}."
            )
        if qtd > 0:
            a_entregar.append((item, qtd))
    if not a_entregar:
        raise TransicaoInvalida(
            "Informe a quantidade de pelo menos um item para entregar."
            if itens else "Nenhum item deste pedido está disponível no estoque para entrega."
        )

    for item, qtd in a_entregar:
        transferir(
            produto=item.produto, origem=central, destino=pedido.almoxarifado_destino,
            quantidade=qtd, responsavel=usuario, pedido=pedido,
        )
        item.quantidade_entregue += qtd
        item.save(update_fields=["quantidade_entregue"])

    # itens de compra também contam: o pedido só fecha quando tudo foi entregue
    completo = all(item.pendente == 0 for item in pedido.itens.all())
    resumo = "; ".join(f"{qtd:f} × {item.produto.nome}" for item, qtd in a_entregar)[:480]
    return transicionar(
        pedido,
        StatusPedido.ENTREGUE if completo else StatusPedido.ENTREGA_PARCIAL,
        usuario,
        f"Entregue: {resumo}" if resumo else "",
    )


def saldo_central(produto):
    saldo = SaldoEstoque.objects.filter(produto=produto, almoxarifado__central=True).first()
    return saldo.quantidade if saldo else 0
