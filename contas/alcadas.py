"""Edição da tabela de alçadas: o grupo tem uma tabela padrão e cada filial pode ter a sua.

A tabela é salva inteira e versionada (nova versão ativa, anterior desativada): cotações
antigas continuam apontando para a faixa com que foram decididas."""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import Alcada, AprovadorAlcada, PapelAlcada

CENTAVO = Decimal("0.01")
LIMITE_MAXIMO = Decimal("9999999999.99")  # cabe em DecimalField(max_digits=12, decimal_places=2)


class TabelaInvalida(ValueError):
    pass


def _decimal(texto):
    texto = (texto or "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto).quantize(CENTAVO)
    except InvalidOperation:
        return None


def ler_linhas(maximos, papeis):
    """Converte o formulário em [(valor_max, papel)]. A última faixa não tem teto."""
    if not papeis or len(maximos) != len(papeis):
        raise TabelaInvalida("Cadastre ao menos uma faixa.")
    validos = {valor for valor, _ in PapelAlcada.choices}
    linhas, anterior = [], None
    for i, (bruto, papel) in enumerate(zip(maximos, papeis), start=1):
        if papel not in validos:
            raise TabelaInvalida(f"Faixa {i}: escolha quem aprova.")
        if i == len(papeis):
            linhas.append((None, papel))
            continue
        maximo = _decimal(bruto)
        piso = Decimal("0") if anterior is None else anterior + CENTAVO
        if maximo is None or maximo <= piso:
            raise TabelaInvalida(f"Faixa {i}: o limite deve ser um valor maior que o da faixa anterior.")
        if maximo > LIMITE_MAXIMO:
            raise TabelaInvalida(f"Faixa {i}: limite grande demais.")
        linhas.append((maximo, papel))
        anterior = maximo
    if linhas[-1][1] == PapelAlcada.AUTOMATICA:
        raise TabelaInvalida(
            "A última faixa (sem limite) precisa de aprovador: não pode ser automática."
        )
    return linhas


def tabela_ativa(tenant, filial):
    return list(
        Alcada.todos_os_tenants.filter(tenant=tenant, filial=filial, ativo=True)
        .order_by("valor_min").prefetch_related("aprovadores__usuario", "aprovadores__filial")
    )


def _com_minimos(linhas):
    minimo, saida = Decimal("0.00"), []
    for maximo, papel in linhas:
        saida.append((minimo, maximo, papel))
        if maximo is not None:
            minimo = maximo + CENTAVO
    return saida


@transaction.atomic
def salvar_tabela(*, tenant, filial, linhas, usuario):
    """Grava a tabela do escopo (grupo ou filial). Devolve False se nada mudou."""
    novas = _com_minimos(linhas)
    atuais = tabela_ativa(tenant, filial)
    if [(a.valor_min, a.valor_max, a.papel_aprovador) for a in atuais] == novas:
        return False

    # Os aprovadores acompanham o tipo de aprovação (ex.: Diretoria) entre versões.
    herdados = {}
    for a in atuais:
        herdados.setdefault(
            a.papel_aprovador,
            [(ap.usuario_id, ap.filial_id, [s.pk for s in ap.setores.all()]) for ap in a.aprovadores.all()],
        )
    Alcada.todos_os_tenants.filter(pk__in=[a.pk for a in atuais]).update(ativo=False)

    for minimo, maximo, papel in novas:
        faixa = Alcada.todos_os_tenants.create(
            tenant=tenant, filial=filial, valor_min=minimo, valor_max=maximo,
            papel_aprovador=papel, atualizado_por=usuario,
        )
        for usuario_id, filial_aprovador_id, setores in herdados.get(papel, []):
            aprovador, _ = AprovadorAlcada.objects.get_or_create(
                alcada=faixa, usuario_id=usuario_id, filial_id=filial_aprovador_id
            )
            aprovador.setores.set(setores)
    return True


def criar_tabela_propria(*, tenant, filial, usuario):
    """Cópia da tabela padrão do grupo para a filial editar à parte."""
    padrao = tabela_ativa(tenant, None)
    if not padrao:
        raise TabelaInvalida("O grupo ainda não tem tabela padrão para copiar.")
    return salvar_tabela(
        tenant=tenant, filial=filial,
        linhas=[(a.valor_max, a.papel_aprovador) for a in padrao], usuario=usuario,
    )


@transaction.atomic
def voltar_ao_padrao(*, tenant, filial):
    """Descarta a tabela própria: a filial volta a seguir a tabela padrão do grupo."""
    Alcada.todos_os_tenants.filter(tenant=tenant, filial=filial, ativo=True).update(ativo=False)
