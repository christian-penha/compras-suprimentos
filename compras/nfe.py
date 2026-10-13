"""Leitura de NF-e (XML). Usa defusedxml: a nota vem de terceiros e não pode abrir entidades
externas nem expandir entidades (XXE / billion laughs)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

TAMANHO_MAXIMO = 2 * 1024 * 1024  # 2 MB; NF-e comum tem poucos KB


class NFeInvalida(Exception):
    pass


@dataclass
class LinhaNFe:
    codigo: str
    descricao: str
    quantidade: Decimal
    valor_unitario: Decimal


@dataclass
class DadosNFe:
    chave: str
    numero: str
    emissao: date | None
    emitente_cnpj: str
    destinatario_cnpj: str
    valor_produtos: Decimal
    valor_frete: Decimal
    valor_total: Decimal
    linhas: list = field(default_factory=list)


def _sem_ns(tag):
    return tag.rsplit("}", 1)[-1]


def _filho(no, nome):
    if no is None:
        return None
    for f in no:
        if _sem_ns(f.tag) == nome:
            return f
    return None


def _texto(no, nome):
    f = _filho(no, nome)
    return (f.text or "").strip() if f is not None else ""


def _decimal(valor, rotulo):
    try:
        return Decimal(valor or "0")
    except InvalidOperation as e:
        raise NFeInvalida(f"Valor inválido em {rotulo}: {valor!r}.") from e


def _cnpj(valor):
    return "".join(c for c in valor if c.isdigit())


def ler_nfe(conteudo: bytes) -> DadosNFe:
    if len(conteudo) > TAMANHO_MAXIMO:
        raise NFeInvalida("Arquivo grande demais para uma NF-e (limite de 2 MB).")
    try:
        raiz = ElementTree.fromstring(conteudo)
    except DefusedXmlException as e:
        raise NFeInvalida("XML rejeitado por conter entidades não permitidas.") from e
    except ElementTree.ParseError as e:
        raise NFeInvalida("O arquivo não é um XML válido.") from e

    inf = next((n for n in raiz.iter() if _sem_ns(n.tag) == "infNFe"), None)
    if inf is None:
        raise NFeInvalida("XML sem o grupo infNFe — não parece uma NF-e.")

    chave = (inf.get("Id") or "").removeprefix("NFe")
    if not (len(chave) == 44 and chave.isdigit()):
        raise NFeInvalida("Chave de acesso ausente ou inválida (esperados 44 dígitos).")

    ide, emit, dest = _filho(inf, "ide"), _filho(inf, "emit"), _filho(inf, "dest")
    total = _filho(_filho(inf, "total"), "ICMSTot")
    emissao_txt = (_texto(ide, "dhEmi") or _texto(ide, "dEmi"))[:10]
    try:
        emissao = date.fromisoformat(emissao_txt) if emissao_txt else None
    except ValueError:
        emissao = None

    linhas = []
    for det in (n for n in inf if _sem_ns(n.tag) == "det"):
        prod = _filho(det, "prod")
        if prod is None:
            continue
        linhas.append(LinhaNFe(
            codigo=_texto(prod, "cProd"),
            descricao=_texto(prod, "xProd"),
            quantidade=_decimal(_texto(prod, "qCom"), "qCom"),
            valor_unitario=_decimal(_texto(prod, "vUnCom"), "vUnCom"),
        ))
    if not linhas:
        raise NFeInvalida("A nota não tem itens.")

    return DadosNFe(
        chave=chave,
        numero=_texto(ide, "nNF"),
        emissao=emissao,
        emitente_cnpj=_cnpj(_texto(emit, "CNPJ")),
        destinatario_cnpj=_cnpj(_texto(dest, "CNPJ")),
        valor_produtos=_decimal(_texto(total, "vProd"), "vProd"),
        valor_frete=_decimal(_texto(total, "vFrete"), "vFrete"),
        valor_total=_decimal(_texto(total, "vNF"), "vNF"),
        linhas=linhas,
    )
