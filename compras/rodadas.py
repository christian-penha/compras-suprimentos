"""Comparação de fornecedores: propostas concorrentes pelos mesmos itens e escolha do vencedor
por item. Só depois de fechada a rodada nascem as cotações que seguem para alçada e OC."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from contas.models import Alcada, PapelAlcada
from pedidos.services import TransicaoInvalida

from .models import Cotacao, ItemCotacao, StatusCotacao
from .services import (
    CENTAVOS, _registrar_nos_pedidos, _centro_do_item, gerar_ordem_automatica, itens_para_cotar, proximo_numero_cotacao,
)


@transaction.atomic
def criar_rodada(*, comprador, propostas, observacao="", centros=None):
    """`propostas`: lista de {"fornecedor", "precos": {item_pedido_id: valor}, "frete"}.
    Item sem preço = o fornecedor não cotou. Cada proposta nasce EM_COMPARACAO, sem alçada
    nem número, e prende os itens fora da fila até `fechar_rodada`."""
    if len(propostas) < 2:
        raise TransicaoInvalida("Uma comparação precisa de ao menos dois fornecedores.")
    if len({p["fornecedor"].pk for p in propostas}) != len(propostas):
        raise TransicaoInvalida("O mesmo fornecedor foi informado mais de uma vez.")
    for p in propostas:
        if not p["precos"]:
            raise TransicaoInvalida(f"{p['fornecedor']} não tem preço em nenhum item.")
        if p["frete"] < 0:
            raise TransicaoInvalida("O frete não pode ser negativo.")
        if any(v <= 0 for v in p["precos"].values()):
            raise TransicaoInvalida("Informe um valor unitário maior que zero para todos os itens.")

    ids = {pk for p in propostas for pk in p["precos"]}
    itens = {i.pk: i for i in itens_para_cotar().filter(pk__in=ids).select_for_update(of=("self",))}
    if len(itens) != len(ids):
        raise TransicaoInvalida(
            "Algum item não está mais disponível para cotação (já cotado ou já atendido)."
        )

    rodada = None
    for p in propostas:
        total = sum((itens[pk].pendente * v for pk, v in p["precos"].items()), Decimal("0"))
        cotacao = Cotacao.objects.create(
            tenant=comprador.tenant, fornecedor=p["fornecedor"], comprador=comprador,
            valor_frete=p["frete"], valor_total=(total + p["frete"]).quantize(CENTAVOS),
            status=StatusCotacao.EM_COMPARACAO, observacao=observacao, rodada=rodada,
        )
        if rodada is None:
            rodada = cotacao.rodada = cotacao.pk
            cotacao.save(update_fields=["rodada"])
        ItemCotacao.objects.bulk_create([
            ItemCotacao(
                cotacao=cotacao, item_pedido=itens[pk], quantidade=itens[pk].pendente, valor_unitario=v,
                centro_custo=_centro_do_item(itens[pk], centros),
            )
            for pk, v in p["precos"].items()
        ])
    _registrar_nos_pedidos(
        {i.pedido for i in itens.values()}, comprador,
        "Itens em comparação entre " + ", ".join(str(p["fornecedor"]) for p in propostas) + ".",
    )
    return rodada


def propostas_da_rodada(rodada):
    return list(
        Cotacao.objects.filter(rodada=rodada, status=StatusCotacao.EM_COMPARACAO)
        .select_related("fornecedor").prefetch_related("itens__item_pedido__produto")
        .order_by("pk")
    )


@transaction.atomic
def fechar_rodada(rodada, comprador, escolhas):
    """`escolhas`: {item_pedido_id: cotacao_id vencedora}. Cada fornecedor vencedor fica com
    uma cotação só com os itens que ganhou (alçada e número próprios, frete integral com ele);
    quem não ganhou nada é cancelado. Devolve as cotações vencedoras."""
    propostas = {
        c.pk: c for c in Cotacao.objects.select_for_update().filter(
            rodada=rodada, status=StatusCotacao.EM_COMPARACAO
        )
    }
    if not propostas:
        raise TransicaoInvalida("Esta comparação já foi encerrada.")
    itens = list(
        ItemCotacao.objects.filter(cotacao__in=propostas)
        .select_related("item_pedido__pedido__almoxarifado_destino")
    )
    if set(escolhas) != {i.item_pedido_id for i in itens}:
        raise TransicaoInvalida("Escolha um fornecedor para cada item.")
    ofertas = {(i.item_pedido_id, i.cotacao_id) for i in itens}
    if any((item, cot) not in ofertas for item, cot in escolhas.items()):
        raise TransicaoInvalida("O fornecedor escolhido não cotou um dos itens.")

    vencedoras = []
    for pk, cotacao in propostas.items():
        ganhos = [i for i in itens if i.cotacao_id == pk and escolhas[i.item_pedido_id] == pk]
        if not ganhos:
            cotacao.status = StatusCotacao.CANCELADA
            cotacao.motivo_recusa = "Não venceu a comparação."
            cotacao.save(update_fields=["status", "motivo_recusa"])
            continue
        ItemCotacao.objects.filter(cotacao=cotacao).exclude(pk__in=[i.pk for i in ganhos]).delete()
        total = (sum((i.subtotal for i in ganhos), Decimal("0")) + cotacao.valor_frete).quantize(CENTAVOS)
        pedidos = {i.item_pedido.pedido for i in ganhos}
        alcada = Alcada.para_valor(total, {p.almoxarifado_destino.filial_id for p in pedidos})
        if alcada is None:
            raise TransicaoInvalida("Nenhuma faixa de alçada cobre este valor — cadastre as faixas de alçada.")
        automatica = alcada.papel_aprovador == PapelAlcada.AUTOMATICA
        cotacao.valor_total, cotacao.alcada = total, alcada
        cotacao.numero = proximo_numero_cotacao(comprador.tenant, {p.pk for p in pedidos})
        cotacao.status = StatusCotacao.APROVADA if automatica else StatusCotacao.AGUARDANDO_ALCADA
        cotacao.decidida_em = timezone.now() if automatica else None
        cotacao.save()
        vencedoras.append(cotacao)
        if automatica:
            gerar_ordem_automatica(cotacao)
        _registrar_nos_pedidos(
            pedidos, comprador,
            f"Cotação #{cotacao.numero} com {cotacao.fornecedor}: R$ {total} — "
            + ("aprovada automaticamente." if automatica
               else f"enviada para aprovação ({alcada.get_papel_aprovador_display()})."),
        )
    return vencedoras
