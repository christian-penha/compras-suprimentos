import uuid
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from contas.models import Alcada, AprovadorAlcada, PapelAlcada, Usuario
from estoque.services import registrar_entrada
from pedidos.models import EventoPedido, ItemPedido, OrigemAtendimento, StatusPedido
from pedidos.services import SemPermissao, TransicaoInvalida, liberar_itens_comprados, reavaliar_pedido
from tenancy.context import obter_tenant_atual_id
from tenancy.models import Almoxarifado, Empresa

from .models import (
    AndamentoOrdem,
    CorrespondenciaProdutoFornecedor,
    HistoricoAndamento,
    Cotacao,
    ItemCotacao,
    ItemRecebimento,
    NotaFiscal,
    OrdemCompra,
    Recebimento,
    StatusCotacao,
    StatusOrdem,
)
from .nfe import ler_nfe

CENTAVOS = Decimal("0.01")

PEDIDO_AGUARDA_ITEM_DE_COMPRA = (
    StatusPedido.AGUARDANDO_COMPRA, StatusPedido.APROVADO_UNIDADE,
    StatusPedido.EM_SEPARACAO, StatusPedido.ENTREGA_PARCIAL,
)


def _digitos(valor):
    return "".join(c for c in (valor or "") if c.isdigit())


def _registrar_nos_pedidos(pedidos, usuario, texto):
    for pedido in pedidos:
        EventoPedido.objects.create(
            pedido=pedido, de_status=pedido.status, para_status=pedido.status,
            usuario=usuario, observacao=texto[:500],
        )


# ---------------------------------------------------------------- fila e cotação

def itens_para_cotar():
    """Itens que precisam de compra e ainda não estão em nenhuma cotação ativa."""
    em_cotacao_ativa = ItemCotacao.objects.filter(
        cotacao__status__in=[
            StatusCotacao.EM_COMPARACAO, StatusCotacao.AGUARDANDO_ALCADA, StatusCotacao.APROVADA,
        ]
    ).values("item_pedido_id")
    return (
        ItemPedido.objects.filter(
            origem_atendimento=OrigemAtendimento.COMPRA,
            cancelado=False,
            pedido__ativo=True,
            pedido__status__in=PEDIDO_AGUARDA_ITEM_DE_COMPRA,
            pedido__requisitante__tenant_id=obter_tenant_atual_id(),
        )
        .exclude(pk__in=em_cotacao_ativa)
        .select_related(
            "produto__fornecedor_padrao", "pedido__requisitante",
            "pedido__almoxarifado_destino__filial", "pedido__evento",
        )
        .order_by("produto__nome", "pedido_id")
    )


def proximo_numero_cotacao(tenant, pedido_ids):
    """Número herdado do(s) pedido(s): '9', '9.2' (2ª cotação do mesmo pedido), '9 + 11'."""
    base = " + ".join(str(p) for p in sorted(set(pedido_ids)))
    existentes = Cotacao.todos_os_tenants.filter(tenant=tenant).filter(
        Q(numero=base) | Q(numero__startswith=base + ".")
    ).count()
    return base if existentes == 0 else f"{base}.{existentes + 1}"


@transaction.atomic
def criar_cotacao(*, comprador, fornecedor, precos, valor_frete=Decimal("0"), observacao="", centros=None):
    """`precos` é {item_pedido_id: valor_unitario}. Criar já envia: a faixa de alçada é
    aplicada sobre o total com frete e a cotação nasce aprovada (faixa automática) ou
    aguardando a alçada."""
    if not precos:
        raise TransicaoInvalida("Selecione ao menos um item para cotar.")
    if valor_frete < 0:
        raise TransicaoInvalida("O frete não pode ser negativo.")
    if any(p <= 0 for p in precos.values()):
        raise TransicaoInvalida("Informe um valor unitário maior que zero para todos os itens.")

    itens = list(
        itens_para_cotar().filter(pk__in=list(precos)).select_for_update(of=("self",))
    )
    if len(itens) != len(precos):
        raise TransicaoInvalida(
            "Algum item não está mais disponível para cotação (já cotado ou já atendido)."
        )

    total = sum((i.pendente * precos[i.pk] for i in itens), Decimal("0")) + valor_frete
    total = total.quantize(CENTAVOS)
    filiais = {i.pedido.almoxarifado_destino.filial_id for i in itens}
    alcada = Alcada.para_valor(total, filiais)
    if alcada is None:
        raise TransicaoInvalida(
            "Nenhuma faixa de alçada cobre este valor — cadastre as faixas de alçada."
        )
    automatica = alcada.papel_aprovador == PapelAlcada.AUTOMATICA

    cotacao = Cotacao.objects.create(
        tenant=comprador.tenant, fornecedor=fornecedor, comprador=comprador,
        numero=proximo_numero_cotacao(comprador.tenant, {i.pedido_id for i in itens}),
        valor_frete=valor_frete, valor_total=total, alcada=alcada, observacao=observacao,
        status=StatusCotacao.APROVADA if automatica else StatusCotacao.AGUARDANDO_ALCADA,
        decidida_em=timezone.now() if automatica else None,
    )
    ItemCotacao.objects.bulk_create([
        ItemCotacao(
            cotacao=cotacao, item_pedido=i, quantidade=i.pendente, valor_unitario=precos[i.pk],
            centro_custo=(centros or {}).get(i.pk) or i.pedido.centro_custo,
        )
        for i in itens
    ])
    situacao = (
        "aprovada automaticamente (abaixo do limite de alçada)" if automatica
        else f"enviada para aprovação ({alcada.get_papel_aprovador_display()})"
    )
    _registrar_nos_pedidos(
        {i.pedido for i in itens}, comprador,
        f"Cotação #{cotacao.numero} com {fornecedor}: R$ {total} — {situacao}.",
    )
    if automatica:
        gerar_ordem_automatica(cotacao)
    return cotacao


def filiais_da_cotacao(cotacao):
    return {i.item_pedido.pedido.almoxarifado_destino.filial_id for i in cotacao.itens.all()}


def _requisitantes_da_cotacao(cotacao):
    return {i.item_pedido.pedido.requisitante for i in cotacao.itens.select_related("item_pedido__pedido__requisitante")}


def aprovadores_da_cotacao(cotacao):
    """Usuários que podem decidir a cotação.

    - Líder do setor: líderes dos setores de quem pediu (nunca o próprio requisitante). Sem
      líder disponível, a decisão escala para a diretoria da mesma tabela de alçadas.
    - Diretoria de unidade: diretor de uma das unidades envolvidas que cubra algum dos setores
      envolvidos (diretor sem setores cadastrados cobre todos os da unidade).
    - CFO/CEO: os cadastrados na faixa."""
    alcada = cotacao.alcada
    if alcada.papel_aprovador == PapelAlcada.LIDER_SETOR:
        requisitantes = _requisitantes_da_cotacao(cotacao)
        setores = {r.setor_id for r in requisitantes if r.setor_id}
        lideres = (
            Usuario.objects.filter(setores_liderados__in=setores, ativo_no_sistema=True)
            .exclude(pk__in=[r.pk for r in requisitantes]).distinct()
        )
        if lideres.exists():
            return lideres
        alcada = Alcada.todos_os_tenants.filter(
            tenant=cotacao.tenant, filial=alcada.filial, ativo=True,
            papel_aprovador=PapelAlcada.DIRETORIA_UNIDADE,
        ).first()
        if alcada is None:
            return Usuario.objects.none()
        papel = PapelAlcada.DIRETORIA_UNIDADE
    else:
        papel = alcada.papel_aprovador

    vinculos = AprovadorAlcada.objects.filter(alcada=alcada)
    if papel == PapelAlcada.DIRETORIA_UNIDADE:
        setores = {r.setor_id for r in _requisitantes_da_cotacao(cotacao) if r.setor_id}
        vinculos = vinculos.filter(filial_id__in=filiais_da_cotacao(cotacao)).filter(
            Q(setores__isnull=True) | Q(setores__in=setores)
        ).distinct()
    return Usuario.objects.filter(pk__in=vinculos.values("usuario_id"), ativo_no_sistema=True)


def pode_decidir_cotacao(usuario, cotacao):
    return (
        cotacao.status == StatusCotacao.AGUARDANDO_ALCADA
        and usuario != cotacao.comprador  # quem compra não aprova a própria compra
        and aprovadores_da_cotacao(cotacao).filter(pk=usuario.pk).exists()
    )


@transaction.atomic
def decidir_cotacao(cotacao, usuario, aprovar, motivo=""):
    cotacao = Cotacao.objects.select_for_update().get(pk=cotacao.pk)
    if cotacao.status != StatusCotacao.AGUARDANDO_ALCADA:
        raise TransicaoInvalida("Esta cotação não está aguardando aprovação.")
    if usuario == cotacao.comprador:
        raise SemPermissao("Quem fez a cotação não pode aprová-la.")
    if not pode_decidir_cotacao(usuario, cotacao):
        raise SemPermissao("Você não é aprovador desta faixa de alçada.")
    if not aprovar and not motivo.strip():
        raise TransicaoInvalida("A recusa exige um motivo.")

    cotacao.status = StatusCotacao.APROVADA if aprovar else StatusCotacao.RECUSADA
    cotacao.decidida_por = usuario
    cotacao.decidida_em = timezone.now()
    cotacao.motivo_recusa = "" if aprovar else motivo.strip()[:300]
    cotacao.save()

    pedidos = {i.item_pedido.pedido for i in cotacao.itens.select_related("item_pedido__pedido")}
    if aprovar:
        texto = f"Cotação #{cotacao.numero} aprovada pela alçada."
    else:
        texto = f"Cotação #{cotacao.numero} recusada pela alçada: {cotacao.motivo_recusa}. Item volta à fila de compras."
    _registrar_nos_pedidos(pedidos, usuario, texto)
    if aprovar:
        gerar_ordem_automatica(cotacao)
    return cotacao


APROVAR, RECOTAR, ELIMINAR = "APROVAR", "RECOTAR", "ELIMINAR"


@transaction.atomic
def decidir_cotacao_por_item(cotacao, usuario, decisoes, motivo=""):
    """Aprovação parcial na alçada. `decisoes` é {item_cotacao_id: APROVAR | RECOTAR | ELIMINAR}:
    APROVAR mantém o item na compra; RECOTAR devolve o item à fila para nova cotação;
    ELIMINAR tira o item do pedido. Sem nenhum item aprovado a cotação é recusada."""
    cotacao = Cotacao.objects.select_for_update().get(pk=cotacao.pk)
    if cotacao.status != StatusCotacao.AGUARDANDO_ALCADA:
        raise TransicaoInvalida("Esta cotação não está aguardando aprovação.")
    if usuario == cotacao.comprador:
        raise SemPermissao("Quem fez a cotação não pode aprová-la.")
    if not pode_decidir_cotacao(usuario, cotacao):
        raise SemPermissao("Você não é aprovador desta faixa de alçada.")

    itens = list(cotacao.itens.select_related("item_pedido__pedido", "item_pedido__produto"))
    if {i.pk for i in itens} != set(decisoes):
        raise TransicaoInvalida("Informe a decisão de cada item da cotação.")
    if any(d not in (APROVAR, RECOTAR, ELIMINAR) for d in decisoes.values()):
        raise TransicaoInvalida("Decisão inválida para um dos itens.")
    aprovados = [i for i in itens if decisoes[i.pk] == APROVAR]
    fora = [i for i in itens if decisoes[i.pk] != APROVAR]
    if fora and not motivo.strip():
        raise TransicaoInvalida("Informe o motivo dos itens não aprovados.")
    motivo = motivo.strip()[:300]

    pedidos = {i.item_pedido.pedido for i in itens}
    for i in fora:
        pedido, produto = i.item_pedido.pedido, i.item_pedido.produto
        if decisoes[i.pk] == ELIMINAR:
            ItemPedido.objects.filter(pk=i.item_pedido_id).update(cancelado=True, motivo_cancelamento=motivo)
            texto = f"{produto.nome} eliminado do pedido na alçada: {motivo}."
        else:
            texto = f"{produto.nome} não aprovado na alçada e devolvido à fila para nova cotação: {motivo}."
        _registrar_nos_pedidos([pedido], usuario, texto)

    cotacao.decidida_por, cotacao.decidida_em = usuario, timezone.now()
    if aprovados:
        for i in fora:
            i.delete()
        total = sum((i.subtotal for i in aprovados), Decimal("0")) + cotacao.valor_frete
        cotacao.valor_total = total.quantize(CENTAVOS)
        cotacao.status = StatusCotacao.APROVADA
        cotacao.motivo_recusa = motivo if fora else ""
        resumo = "aprovada parcialmente" if fora else "aprovada pela alçada"
    else:
        cotacao.status = StatusCotacao.RECUSADA
        cotacao.motivo_recusa = motivo
        resumo = "recusada pela alçada"
    cotacao.save()
    _registrar_nos_pedidos(pedidos, usuario, f"Cotação #{cotacao.numero} {resumo}.")
    for pedido in pedidos:
        reavaliar_pedido(pedido, usuario)
    if aprovados:
        gerar_ordem_automatica(cotacao)
    return cotacao


@transaction.atomic
def cancelar_cotacao(cotacao, usuario):
    cotacao = Cotacao.objects.select_for_update().get(pk=cotacao.pk)
    if not cotacao.ativa or hasattr(cotacao, "ordem"):
        raise TransicaoInvalida("Só é possível cancelar cotação ativa e sem ordem de compra.")
    cotacao.status = StatusCotacao.CANCELADA
    cotacao.save(update_fields=["status"])
    pedidos = {i.item_pedido.pedido for i in cotacao.itens.select_related("item_pedido__pedido")}
    _registrar_nos_pedidos(pedidos, usuario, f"Cotação #{cotacao.numero} cancelada. Item volta à fila de compras.")
    return cotacao


# ---------------------------------------------------------------- ordem e nota

@transaction.atomic
def definir_centros(cotacao, escolhas, usuario):
    """`escolhas` é {item_cotacao_id: CentroCusto}. Só antes da ordem de compra."""
    cotacao = Cotacao.objects.select_for_update().get(pk=cotacao.pk)
    if not cotacao.ativa or hasattr(cotacao, "ordem"):
        raise TransicaoInvalida("O centro de custo só pode ser alterado antes da ordem de compra.")
    for item in cotacao.itens.filter(pk__in=list(escolhas)):
        item.centro_custo = escolhas[item.pk]
        item.save(update_fields=["centro_custo"])
    pedidos = {i.item_pedido.pedido for i in cotacao.itens.select_related("item_pedido__pedido")}
    _registrar_nos_pedidos(pedidos, usuario, f"Centro de custo da cotação #{cotacao.numero or cotacao.pk} definido por compras.")
    if cotacao.status == StatusCotacao.APROVADA:
        gerar_ordem_automatica(cotacao)
    return cotacao


def gerar_ordem_automatica(cotacao):
    """Cotação aprovada gera a ordem de compra sozinha, em nome do comprador. Se faltar algo
    (centro de custo), ela segue aprovada e o comprador completa e gera pela tela."""
    try:
        return gerar_ordem_compra(cotacao, cotacao.comprador)
    except TransicaoInvalida:
        return None


@transaction.atomic
def gerar_ordem_compra(cotacao, usuario):
    cotacao = Cotacao.objects.select_for_update().get(pk=cotacao.pk)
    if cotacao.status != StatusCotacao.APROVADA:
        raise TransicaoInvalida("A ordem de compra só sai de uma cotação aprovada.")
    if hasattr(cotacao, "ordem"):
        return cotacao.ordem  # idempotente: a OC pode ter saído automaticamente na aprovação
    if cotacao.itens.filter(centro_custo__isnull=True).exists():
        raise TransicaoInvalida(
            "Defina o centro de custo de todos os itens antes de gerar a ordem de compra."
        )
    ordem = OrdemCompra.objects.create(tenant=cotacao.tenant, cotacao=cotacao, criada_por=usuario)
    HistoricoAndamento.objects.create(
        tenant=ordem.tenant, ordem=ordem, andamento=AndamentoOrdem.EMITIDA, usuario=usuario,
        observacao="Ordem de compra emitida.",
    )
    pedidos = {i.item_pedido.pedido for i in cotacao.itens.select_related("item_pedido__pedido")}
    _registrar_nos_pedidos(pedidos, usuario, f"Ordem de compra #{ordem.numero} emitida para {cotacao.fornecedor}.")
    return ordem


@transaction.atomic
def registrar_nota_xml(ordem, conteudo, usuario):
    if not ordem.aberta:
        raise TransicaoInvalida("A ordem de compra não está aberta para receber notas.")
    dados = ler_nfe(conteudo)

    if NotaFiscal.todos_os_tenants.filter(chave_acesso=dados.chave).exists():
        raise TransicaoInvalida("Esta nota fiscal (chave de acesso) já foi lançada.")
    cnpjs_do_grupo = {_digitos(e.cnpj) for e in Empresa.objects.all()}
    if dados.destinatario_cnpj not in cnpjs_do_grupo:
        raise TransicaoInvalida(
            f"O CNPJ do destinatário ({dados.destinatario_cnpj or 'ausente'}) não pertence a nenhuma "
            "empresa do grupo. Confira o CNPJ em Configurações → Empresas."
        )
    fornecedor = ordem.cotacao.fornecedor
    cnpj_fornecedor = _digitos(fornecedor.cnpj)
    if cnpj_fornecedor and dados.emitente_cnpj != cnpj_fornecedor:
        raise TransicaoInvalida("O emitente da nota não é o fornecedor desta ordem de compra.")

    nota = NotaFiscal(
        tenant=ordem.tenant, ordem=ordem, chave_acesso=dados.chave, numero=dados.numero,
        emitente_cnpj=dados.emitente_cnpj, destinatario_cnpj=dados.destinatario_cnpj,
        data_emissao=dados.emissao, valor_produtos=dados.valor_produtos,
        valor_frete=dados.valor_frete, valor_total=dados.valor_total, registrada_por=usuario,
    )
    nota.arquivo_xml.save(f"{uuid.uuid4().hex}.xml", ContentFile(conteudo), save=False)
    nota.save()
    return nota


def linhas_da_nota(nota):
    if not nota.arquivo_xml:
        return []
    with nota.arquivo_xml.open("rb") as f:
        return ler_nfe(f.read()).linhas


def sugerir_conferencia(ordem, nota):
    """Para cada item pendente da OC, sugere a linha da nota que o representa: primeiro pela
    correspondência aprendida (código do fornecedor), depois pela ordem, se baterem em número."""
    linhas = linhas_da_nota(nota) if nota else []
    pendentes = [i for i in ordem.cotacao.itens.select_related("item_pedido__produto") if i.pendente > 0]
    aprendido = {
        c.produto_id: c.codigo_fornecedor
        for c in CorrespondenciaProdutoFornecedor.objects.filter(fornecedor=ordem.cotacao.fornecedor)
    }
    por_codigo = {linha.codigo: idx for idx, linha in enumerate(linhas)}
    resultado = []
    for pos, item in enumerate(pendentes):
        idx = por_codigo.get(aprendido.get(item.item_pedido.produto_id))
        if idx is None and len(linhas) == len(pendentes):
            idx = pos
        linha = linhas[idx] if idx is not None else None
        resultado.append({
            "item": item,
            "linha_idx": idx,
            "linha": linha,
            "quantidade_nota": linha.quantidade if linha else item.pendente,
            "sugerida": min(linha.quantidade, item.pendente) if linha else item.pendente,
        })
    return {"linhas": linhas, "itens": resultado}


# ---------------------------------------------------------------- recebimento

@transaction.atomic
def confirmar_recebimento(ordem, usuario, conferencias, nota=None, observacao=""):
    """`conferencias`: lista de dicts {item_id, quantidade, linha_idx (opcional)}.

    A entrada no estoque (no almoxarifado de cada pedido) acontece aqui, na conferência (RN-13), a custo
    cotado + frete rateado proporcionalmente ao valor dos itens (RN-14)."""
    ordem = OrdemCompra.objects.select_for_update().get(pk=ordem.pk)
    if not ordem.aberta:
        raise TransicaoInvalida("Esta ordem de compra não está aberta para recebimento.")

    cotacao = ordem.cotacao
    itens = {i.pk: i for i in cotacao.itens.select_related("item_pedido__produto", "item_pedido__pedido")}
    base = cotacao.valor_itens
    linhas = linhas_da_nota(nota) if nota else []

    a_receber = []
    for c in conferencias:
        item = itens.get(c["item_id"])
        if item is None:
            raise TransicaoInvalida("Item que não pertence a esta ordem de compra.")
        qtd = c["quantidade"]
        if qtd < 0 or qtd > item.pendente:
            raise TransicaoInvalida(
                f"{item.item_pedido.produto}: a quantidade conferida deve estar entre 0 e {item.pendente:f}."
            )
        if qtd > 0:
            a_receber.append((item, qtd, c.get("linha_idx")))
    if not a_receber:
        raise TransicaoInvalida("Informe a quantidade conferida de pelo menos um item.")

    proprio_comprador = usuario == cotacao.comprador
    recebimento = Recebimento.objects.create(
        tenant=ordem.tenant, ordem=ordem, nota=nota, conferido_por=usuario,
        observacao=(observacao + (" " if observacao else "") + (
            "Conferência feita pelo próprio comprador (exceção registrada)." if proprio_comprador else ""
        )).strip()[:300],
    )

    pedidos = {}
    for item, qtd, linha_idx in a_receber:
        frete_unit = Decimal("0")
        if base > 0 and cotacao.valor_frete > 0:
            frete_unit = cotacao.valor_frete * (item.subtotal / base) / item.quantidade
        custo = (item.valor_unitario + frete_unit).quantize(CENTAVOS)
        produto = item.item_pedido.produto
        registrar_entrada(
            produto=produto, destino=item.item_pedido.pedido.almoxarifado_destino, quantidade=qtd, responsavel=usuario,
            valor_unitario=custo, arquivo_origem=f"OC #{ordem.numero}",
            observacao=f"Recebimento da OC #{ordem.numero}" + (f" — NF {nota.numero}" if nota and nota.numero else ""),
        )
        item.quantidade_recebida += qtd
        item.save(update_fields=["quantidade_recebida"])
        qtd_nota = linhas[linha_idx].quantidade if linha_idx is not None and linha_idx < len(linhas) else qtd
        ItemRecebimento.objects.create(
            recebimento=recebimento, item_cotacao=item,
            quantidade_nota=qtd_nota, quantidade_conferida=qtd,
        )
        if linha_idx is not None and linha_idx < len(linhas) and linhas[linha_idx].codigo:
            CorrespondenciaProdutoFornecedor.objects.update_or_create(
                tenant=ordem.tenant, fornecedor=cotacao.fornecedor,
                codigo_fornecedor=linhas[linha_idx].codigo, defaults={"produto": produto},
            )
        if item.pendente == 0:
            pedido_item = item.item_pedido
            pedido_item.origem_atendimento = OrigemAtendimento.ESTOQUE
            pedido_item.save(update_fields=["origem_atendimento"])
        pedidos[item.item_pedido.pedido_id] = item.item_pedido.pedido

    ordem.status = (
        StatusOrdem.CONCLUIDA if all(i.pendente == 0 for i in itens.values())
        else StatusOrdem.PARCIALMENTE_RECEBIDA
    )
    ordem.save(update_fields=["status"])
    if ordem.status == StatusOrdem.CONCLUIDA and ordem.andamento != AndamentoOrdem.RECEBIDA:
        ordem.andamento = AndamentoOrdem.RECEBIDA
        ordem.save(update_fields=["andamento"])
        HistoricoAndamento.objects.create(
            tenant=ordem.tenant, ordem=ordem, andamento=AndamentoOrdem.RECEBIDA,
            observacao="Recebimento conferido e entrada no estoque.", usuario=usuario,
        )

    for pedido in pedidos.values():
        liberar_itens_comprados(
            pedido, usuario, f"Recebimento da OC #{ordem.numero} confirmado — material entrou no estoque."
        )
    return recebimento


@transaction.atomic
def atualizar_andamento(ordem, usuario, andamento, observacao="", previsao=None):
    """O comprador informa em que pé está a compra (efetuada, em trânsito…). Fica no histórico
    e aparece para o requisitante no pedido."""
    ordem = OrdemCompra.objects.select_for_update().get(pk=ordem.pk)
    if not ordem.aberta:
        raise TransicaoInvalida("A ordem de compra não está mais aberta.")
    manuais = (AndamentoOrdem.EMITIDA, AndamentoOrdem.COMPRA_EFETUADA, AndamentoOrdem.EM_TRANSITO)
    if andamento not in manuais:
        raise TransicaoInvalida("Situação inválida: a conclusão do recebimento é automática.")
    ordem.andamento = andamento
    if previsao is not None:
        ordem.previsao_entrega = previsao
    ordem.save(update_fields=["andamento", "previsao_entrega"])
    HistoricoAndamento.objects.create(
        tenant=ordem.tenant, ordem=ordem, andamento=andamento, observacao=observacao.strip()[:300],
        previsao_entrega=previsao, usuario=usuario,
    )
    pedidos = {i.item_pedido.pedido for i in ordem.cotacao.itens.select_related("item_pedido__pedido")}
    texto = f"Compra (OC #{ordem.numero}): {ordem.get_andamento_display().lower()}"
    if ordem.previsao_entrega:
        texto += f", previsão de entrega {ordem.previsao_entrega:%d/%m/%Y}"
    _registrar_nos_pedidos(pedidos, usuario, texto + ".")
    return ordem


def desembolso_por_centro(inicio=None, fim=None):
    """Eixo desembolso (RN-17): valor das notas no período distribuído pelos centros de custo
    dos itens da cotação, proporcionalmente ao subtotal de cada centro. {CentroCusto|None: valor}"""
    notas = NotaFiscal.objects.select_related("ordem__cotacao")
    if inicio:
        notas = notas.filter(data_emissao__gte=inicio)
    if fim:
        notas = notas.filter(data_emissao__lte=fim)
    totais = {}
    for nota in notas:
        itens = list(nota.ordem.cotacao.itens.select_related("centro_custo"))
        base = sum((i.subtotal for i in itens), Decimal("0"))
        if not itens or base == 0:
            totais[None] = totais.get(None, Decimal("0")) + nota.valor_total
            continue
        for i in itens:
            parte = (nota.valor_total * i.subtotal / base).quantize(CENTAVOS)
            totais[i.centro_custo] = totais.get(i.centro_custo, Decimal("0")) + parte
    return totais
