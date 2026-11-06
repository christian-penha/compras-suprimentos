from decimal import Decimal

from django.db import transaction

from contas.models import ModoAprovacaoSetor, Modulo, Papel, RegraAprovacaoEmpresa
from estoque.models import SaldoEstoque
from estoque.services import registrar_saida
from tenancy.models import Almoxarifado
from pedidos.models import (
    AprovacaoLider,
    EventoPedido,
    MensagemPedido,
    OrigemAtendimento,
    Pedido,
    StatusPedido,
    StatusSimilar,
)


class TransicaoInvalida(Exception):
    pass


class SemPermissao(Exception):
    pass


TRANSICOES = {
    StatusPedido.RASCUNHO: {StatusPedido.ENVIADO},
    StatusPedido.ENVIADO: {StatusPedido.APROVADO_UNIDADE, StatusPedido.RECUSADO},
    StatusPedido.APROVADO_UNIDADE: {StatusPedido.EM_SEPARACAO, StatusPedido.AGUARDANDO_COMPRA},
    StatusPedido.AGUARDANDO_COMPRA: {StatusPedido.APROVADO_UNIDADE},
    StatusPedido.EM_SEPARACAO: {StatusPedido.ENTREGUE, StatusPedido.ENTREGA_PARCIAL},
    StatusPedido.ENTREGA_PARCIAL: {StatusPedido.ENTREGUE, StatusPedido.ENTREGA_PARCIAL},
    StatusPedido.ENTREGUE: {StatusPedido.CONCLUIDO},
}

# Cancelamento (motivo obrigatório; o pedido cancelado sai das listas de operação):
# - o requisitante cancela só até a aprovação do setor;
# - aprovadores (líderes do setor) e suprimentos, até a entrega.
STATUS_CANCELAVEL_PELO_REQUISITANTE = {StatusPedido.RASCUNHO, StatusPedido.ENVIADO}
STATUS_CANCELAVEL_PELA_OPERACAO = STATUS_CANCELAVEL_PELO_REQUISITANTE | {
    StatusPedido.APROVADO_UNIDADE, StatusPedido.AGUARDANDO_COMPRA, StatusPedido.EM_SEPARACAO,
}
for _origem in STATUS_CANCELAVEL_PELA_OPERACAO:
    TRANSICOES.setdefault(_origem, set()).add(StatusPedido.CANCELADO)


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


def liberar_itens_comprados(pedido, usuario, observacao):
    """Chamado quando itens comprados entram no estoque: o pedido que esperava a compra
    volta ao fluxo normal, sem reclassificar itens (isso reverteria itens já entregues)."""
    EventoPedido.objects.create(
        pedido=pedido, de_status=pedido.status, para_status=pedido.status,
        usuario=usuario, observacao=observacao,
    )
    tem_item_de_estoque = pedido.itens.filter(origem_atendimento=OrigemAtendimento.ESTOQUE).exists()
    if pedido.status == StatusPedido.AGUARDANDO_COMPRA and tem_item_de_estoque:
        _aplicar_transicao(
            pedido, StatusPedido.APROVADO_UNIDADE, usuario,
            "Itens comprados recebidos — pedido liberado para separação.", classificar=False,
        )


def validar_centro_da_unidade(centro, filial_id):
    """O centro de custo tem de ser da unidade do pedido (uma unidade não custeia na outra)."""
    if centro is not None and centro.filial_id != filial_id:
        raise TransicaoInvalida(f"O centro de custo {centro} é de outra unidade; escolha um da unidade do pedido.")


def reavaliar_pedido(pedido, usuario):
    """Depois que itens saem do pedido (eliminados na alçada de compra): encerra o pedido se
    não sobrou nada a entregar, ou o libera para separação se só restam itens de estoque."""
    pedido.refresh_from_db()
    itens = list(pedido.itens.all())
    abertos = [i for i in itens if i.pendente > 0]
    if not abertos:
        if any(i.quantidade_entregue > 0 for i in itens):
            if pedido.status in (StatusPedido.EM_SEPARACAO, StatusPedido.ENTREGA_PARCIAL):
                _aplicar_transicao(
                    pedido, StatusPedido.ENTREGUE, usuario,
                    "Itens restantes eliminados da compra — pedido entregue.", classificar=False,
                )
        elif pedido.status in (StatusPedido.AGUARDANDO_COMPRA, StatusPedido.APROVADO_UNIDADE):
            _aplicar_transicao(
                pedido, StatusPedido.RECUSADO, usuario,
                "Todos os itens foram eliminados na compra — pedido encerrado.", classificar=False,
            )
    elif pedido.status == StatusPedido.AGUARDANDO_COMPRA and not any(
        i.origem_atendimento == OrigemAtendimento.COMPRA for i in abertos
    ):
        _aplicar_transicao(
            pedido, StatusPedido.APROVADO_UNIDADE, usuario,
            "Itens de compra eliminados — o restante segue para separação.", classificar=False,
        )


def _aplicar_transicao(pedido, novo_status, usuario, observacao, classificar=True):
    EventoPedido.objects.create(
        pedido=pedido, de_status=pedido.status, para_status=novo_status,
        usuario=usuario, observacao=observacao,
    )
    pedido.status = novo_status
    pedido.save(update_fields=["status", "atualizado_em"])
    if novo_status == StatusPedido.APROVADO_UNIDADE and classificar:
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
    """Item com saldo no almoxarifado do pedido sai dele; sem saldo, vai para compra."""
    for item in pedido.itens.select_related("produto"):
        saldo = SaldoEstoque.objects.filter(
            produto=item.produto, almoxarifado=pedido.almoxarifado_destino
        ).first()
        disponivel = saldo.quantidade if saldo else 0
        item.origem_atendimento = (
            OrigemAtendimento.ESTOQUE if disponivel >= item.quantidade
            else OrigemAtendimento.COMPRA
        )
        item.save(update_fields=["origem_atendimento"])


@transaction.atomic
def entregar(pedido, usuario, quantidades=None):
    """Entrega os itens do estoque do almoxarifado do pedido: a baixa sai dele.

    `quantidades` é {item_id: Decimal} para entrega parcial; sem ele entrega todo o
    saldo pendente. O pedido vira ENTREGUE quando nenhum item de estoque tem pendência,
    senão ENTREGA_PARCIAL (e pode receber novas entregas).
    """
    if pedido.status not in (StatusPedido.EM_SEPARACAO, StatusPedido.ENTREGA_PARCIAL):
        raise TransicaoInvalida("Só é possível entregar pedidos em separação.")

    if pedido.centro_custo_id is None:
        raise TransicaoInvalida(
            "Defina o centro de custo do pedido antes de entregar (eixo de consumo)."
        )
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
        registrar_saida(
            produto=item.produto, origem=pedido.almoxarifado_destino, quantidade=qtd,
            responsavel=usuario, pedido=pedido, centro_custo=pedido.centro_custo,
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


def saldo_no_almoxarifado(produto, almoxarifado):
    saldo = SaldoEstoque.objects.filter(produto=produto, almoxarifado=almoxarifado).first()
    return saldo.quantidade if saldo else 0


# ------------------------------------------------------------------ cancelamento

def e_suprimentos(usuario):
    return (
        usuario.is_superuser or usuario.tem_papel(Papel.SUPERADMIN)
        or usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.ESTOQUE)
    )


def pode_cancelar(usuario, pedido):
    """Pode cancelar o pedido (ou itens dele) neste momento? Depende de quem é o usuário e do status."""
    if pedido.status in STATUS_CANCELAVEL_PELA_OPERACAO and (
        e_suprimentos(usuario) or pode_aprovar(usuario, pedido)
    ):
        return True
    return usuario == pedido.requisitante and pedido.status in STATUS_CANCELAVEL_PELO_REQUISITANTE


def _cotacoes_em_andamento(itens):
    """Cotações ainda vivas (em comparação, na alçada ou aprovadas) que incluem esses itens."""
    from compras.models import ItemCotacao, StatusCotacao

    vivas = [StatusCotacao.EM_COMPARACAO, StatusCotacao.AGUARDANDO_ALCADA, StatusCotacao.APROVADA]
    return sorted({
        ic.cotacao.numero for ic in ItemCotacao.objects.filter(
            item_pedido__in=itens, cotacao__status__in=vivas
        ).select_related("cotacao")
    })


def _validar_cancelamento(usuario, pedido, motivo, itens):
    if not (motivo or "").strip():
        raise TransicaoInvalida("Informe o motivo do cancelamento.")
    if pedido.status == StatusPedido.CANCELADO:
        raise TransicaoInvalida("Este pedido já foi cancelado.")
    if pedido.status not in STATUS_CANCELAVEL_PELA_OPERACAO:
        raise TransicaoInvalida("O pedido já foi entregue (ou encerrado) e não pode mais ser cancelado.")
    if not pode_cancelar(usuario, pedido):
        if usuario == pedido.requisitante:
            raise SemPermissao("Depois da aprovação, o cancelamento é feito pelo aprovador ou por suprimentos.")
        raise SemPermissao("Você não pode cancelar este pedido.")
    if any(i.quantidade_entregue > 0 for i in itens):
        raise TransicaoInvalida("Já houve entrega de itens — não é possível cancelar o que foi entregue.")
    em_cotacao = _cotacoes_em_andamento(itens)
    if em_cotacao:
        numeros = ", ".join(f"#{n}" for n in em_cotacao)
        raise TransicaoInvalida(
            f"Há item em cotação em andamento ({numeros}). Recuse ou cancele a cotação antes de cancelar."
        )


@transaction.atomic
def cancelar_pedido(pedido, usuario, motivo):
    """Cancela o pedido inteiro. O pedido continua consultável, só sai das filas e da operação."""
    itens = list(pedido.itens.filter(cancelado=False))
    _validar_cancelamento(usuario, pedido, motivo, itens)
    motivo = motivo.strip()
    for item in itens:
        item.cancelado = True
        item.motivo_cancelamento = f"Pedido cancelado: {motivo}"[:300]
        item.save(update_fields=["cancelado", "motivo_cancelamento"])
    pedido.motivo_cancelamento = motivo[:500]
    pedido.cancelado_por = usuario
    pedido.save(update_fields=["motivo_cancelamento", "cancelado_por"])
    _aplicar_transicao(pedido, StatusPedido.CANCELADO, usuario, f"Pedido cancelado: {motivo}", classificar=False)
    return pedido


@transaction.atomic
def cancelar_item(item, usuario, motivo):
    """Cancela um item do pedido. Se era o último item ativo, o pedido inteiro é cancelado."""
    pedido = item.pedido
    if item.cancelado:
        raise TransicaoInvalida("Este item já foi cancelado.")
    _validar_cancelamento(usuario, pedido, motivo, [item])
    motivo = motivo.strip()
    item.cancelado = True
    item.motivo_cancelamento = motivo[:300]
    item.save(update_fields=["cancelado", "motivo_cancelamento"])
    EventoPedido.objects.create(
        pedido=pedido, de_status=pedido.status, para_status=pedido.status, usuario=usuario,
        observacao=f"Item cancelado — {item.produto}: {motivo}",
    )
    if not pedido.itens.filter(cancelado=False).exists():
        pedido.motivo_cancelamento = f"Todos os itens foram cancelados. Último motivo: {motivo}"[:500]
        pedido.cancelado_por = usuario
        pedido.save(update_fields=["motivo_cancelamento", "cancelado_por"])
        _aplicar_transicao(
            pedido, StatusPedido.CANCELADO, usuario,
            "Todos os itens foram cancelados — pedido cancelado.", classificar=False,
        )
    elif pedido.status in (
        StatusPedido.AGUARDANDO_COMPRA, StatusPedido.APROVADO_UNIDADE, StatusPedido.EM_SEPARACAO,
    ):
        reavaliar_pedido(pedido, usuario)
    return item


# ------------------------------------------------------------------ conversa do pedido

def aprovador_de_compra_do_pedido(usuario, pedido):
    """É aprovador (setor/alçada) de alguma cotação que inclui itens deste pedido?"""
    from compras.models import Cotacao
    from compras.services import aprovadores_da_cotacao

    cotacoes = Cotacao.objects.filter(itens__item_pedido__pedido=pedido).distinct().select_related("alcada")
    return any(usuario.pk in {u.pk for u in aprovadores_da_cotacao(c)} for c in cotacoes)


def e_comprador(usuario):
    return usuario.tem_papel(Papel.ADMINISTRADOR, Modulo.COMPRAS)


def pode_participar(usuario, pedido):
    """Quem acompanha o pedido e participa da conversa: o requisitante, os aprovadores do setor,
    suprimentos, compras e os aprovadores das compras ligadas ao pedido."""
    return (
        usuario == pedido.requisitante or pode_aprovar(usuario, pedido) or e_suprimentos(usuario)
        or e_comprador(usuario) or aprovador_de_compra_do_pedido(usuario, pedido)
    )


def papel_na_conversa(usuario, pedido):
    if usuario == pedido.requisitante:
        return "Requisitante"
    if e_suprimentos(usuario):
        return "Suprimentos"
    if e_comprador(usuario):
        return "Compras"
    if pode_aprovar(usuario, pedido):
        return "Aprovador"
    if aprovador_de_compra_do_pedido(usuario, pedido):
        return "Aprovador da compra"
    return ""


def enviar_mensagem(pedido, usuario, texto):
    texto = (texto or "").strip()
    if not texto:
        raise TransicaoInvalida("Escreva a mensagem antes de enviar.")
    if len(texto) > MensagemPedido.TAMANHO_MAXIMO:
        raise TransicaoInvalida(f"A mensagem passa de {MensagemPedido.TAMANHO_MAXIMO} caracteres.")
    if not pode_participar(usuario, pedido):
        raise SemPermissao("Você não participa deste pedido.")
    return MensagemPedido.objects.create(pedido=pedido, autor=usuario, texto=texto)


# ------------------------------------------------------------------ produto similar

STATUS_ACEITA_SIMILAR = {StatusPedido.APROVADO_UNIDADE, StatusPedido.AGUARDANDO_COMPRA, StatusPedido.EM_SEPARACAO}


def _mensagem(pedido, usuario, texto):
    MensagemPedido.objects.create(pedido=pedido, autor=usuario, texto=texto[: MensagemPedido.TAMANHO_MAXIMO])


def item_aceita_similar(item):
    """Item sem estoque (vai para compra), ainda não entregue e sem sugestão pendente."""
    return (
        not item.cancelado and item.quantidade_entregue == 0
        and item.origem_atendimento == OrigemAtendimento.COMPRA
        and item.similar_status != StatusSimilar.PENDENTE
        and item.pedido.status in STATUS_ACEITA_SIMILAR
    )


def candidatos_similares(item):
    """Similares vinculados ao produto do item que têm saldo para atender o pedido inteiro."""
    from cadastros.similares import similares_de

    if not item_aceita_similar(item):
        return []
    destino = item.pedido.almoxarifado_destino
    candidatos = []
    for similar in similares_de(item.produto):
        saldo = saldo_no_almoxarifado(similar, destino)
        if saldo >= item.quantidade:
            candidatos.append({"produto": similar, "saldo": saldo})
    return candidatos


@transaction.atomic
def sugerir_similar(item, similar, usuario):
    """Suprimentos sugere trocar o produto sem estoque por um similar (vínculo cadastrado por eles)."""
    pedido = item.pedido
    if not e_suprimentos(usuario):
        raise SemPermissao("Só suprimentos sugere produto similar.")
    if not item_aceita_similar(item):
        raise TransicaoInvalida("Este item não aceita sugestão de similar agora.")
    candidato = next((c for c in candidatos_similares(item) if c["produto"].pk == similar.pk), None)
    if candidato is None:
        raise TransicaoInvalida(
            f"{similar} não é um similar vinculado a {item.produto} ou não tem saldo suficiente em "
            f"{pedido.almoxarifado_destino.nome}."
        )
    if _cotacoes_em_andamento([item]):
        raise TransicaoInvalida("O item está em cotação em andamento — recuse ou cancele a cotação antes.")

    item.similar_sugerido, item.similar_status = similar, StatusSimilar.PENDENTE
    item.similar_sugerido_por, item.similar_resposta = usuario, ""
    item.save(update_fields=["similar_sugerido", "similar_status", "similar_sugerido_por", "similar_resposta"])
    EventoPedido.objects.create(
        pedido=pedido, de_status=pedido.status, para_status=pedido.status, usuario=usuario,
        observacao=f"Similar sugerido para {item.produto}: {similar}.",
    )
    _mensagem(
        pedido, usuario,
        f"Sugestão de produto similar para «{item.produto}»: «{similar}» "
        f"(há {candidato['saldo']:g} em {pedido.almoxarifado_destino.nome}). "
        "Aceite ou recuse no item do pedido.",
    )
    return item


@transaction.atomic
def responder_similar(item, usuario, aceitar, observacao=""):
    """O requisitante aceita (o item passa a ser o similar, atendido pelo estoque) ou recusa."""
    pedido = item.pedido
    if usuario != pedido.requisitante:
        raise SemPermissao("Só o requisitante responde à sugestão de similar.")
    if item.similar_status != StatusSimilar.PENDENTE or item.similar_sugerido_id is None:
        raise TransicaoInvalida("Não há sugestão de similar aguardando resposta neste item.")
    observacao = (observacao or "").strip()[:300]
    similar, original = item.similar_sugerido, item.produto

    if not aceitar:
        item.similar_status, item.similar_resposta = StatusSimilar.RECUSADO, observacao
        item.save(update_fields=["similar_status", "similar_resposta"])
        EventoPedido.objects.create(
            pedido=pedido, de_status=pedido.status, para_status=pedido.status, usuario=usuario,
            observacao=f"Similar recusado ({similar}) — o item segue para compra.",
        )
        _mensagem(pedido, usuario, f"Recusei o similar «{similar}»" + (f": {observacao}" if observacao else "."))
        return item

    if pedido.itens.filter(produto=similar).exclude(pk=item.pk).exists():
        raise TransicaoInvalida(
            f"O pedido já tem «{similar}» em outro item. Cancele um dos itens ou ajuste a quantidade antes."
        )
    if saldo_no_almoxarifado(similar, pedido.almoxarifado_destino) < item.quantidade:
        raise TransicaoInvalida(f"O saldo de «{similar}» mudou e não atende mais o pedido.")
    if _cotacoes_em_andamento([item]):
        raise TransicaoInvalida("O item entrou em cotação — recuse ou cancele a cotação antes de trocar.")

    item.produto_original = original
    item.produto = similar
    item.origem_atendimento = OrigemAtendimento.ESTOQUE
    item.similar_status, item.similar_resposta = StatusSimilar.ACEITO, observacao
    item.save(update_fields=["produto_original", "produto", "origem_atendimento", "similar_status", "similar_resposta"])
    EventoPedido.objects.create(
        pedido=pedido, de_status=pedido.status, para_status=pedido.status, usuario=usuario,
        observacao=f"Similar aceito: {original} → {similar}.",
    )
    _mensagem(pedido, usuario, f"Aceitei o similar: «{original}» foi trocado por «{similar}».")
    if pedido.status == StatusPedido.AGUARDANDO_COMPRA:
        _aplicar_transicao(
            pedido, StatusPedido.APROVADO_UNIDADE, usuario,
            "Similar aceito — pedido liberado para separação.", classificar=False,
        )
    return item
