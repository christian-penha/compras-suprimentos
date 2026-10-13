import random
from pathlib import Path
from xml.sax.saxutils import escape

from django.core.management.base import BaseCommand, CommandError

from compras.models import OrdemCompra
from tenancy.models import Empresa

CNPJ_FORNECEDOR_PADRAO = "11222333000181"


def _digitos(v):
    return "".join(c for c in (v or "") if c.isdigit())


class Command(BaseCommand):
    help = (
        "Gera um XML de NF-e de TESTE para uma ordem de compra, com emitente = fornecedor e "
        "destinatário = empresa do grupo, para exercitar o fluxo de recebimento sem nota real."
    )

    def add_arguments(self, parser):
        parser.add_argument("ordem", type=int, help="Número da ordem de compra")
        parser.add_argument("--saida", help="Arquivo de saída (padrão: nfe-teste-oc<N>.xml)")

    def handle(self, *args, ordem, saida=None, **opcoes):
        try:
            oc = OrdemCompra.todos_os_tenants.select_related("cotacao__fornecedor").get(pk=ordem)
        except OrdemCompra.DoesNotExist:
            raise CommandError(f"Ordem de compra {ordem} não encontrada.")
        empresa = Empresa.todos_os_tenants.filter(tenant=oc.tenant).first()
        if empresa is None or not _digitos(empresa.cnpj):
            raise CommandError("A empresa do grupo não tem CNPJ cadastrado (Configurações → Empresas).")

        cotacao = oc.cotacao
        emitente = _digitos(cotacao.fornecedor.cnpj) or CNPJ_FORNECEDOR_PADRAO
        destinatario = _digitos(empresa.cnpj)
        numero = random.randint(1000, 999999)
        chave = "35" + "2610" + emitente.zfill(14) + "55" + "001" + str(numero).zfill(9) + "1" + str(
            random.randint(0, 99999999)
        ).zfill(8) + str(random.randint(0, 9))
        assert len(chave) == 44, len(chave)

        itens = list(cotacao.itens.select_related("item_pedido__produto"))
        dets = []
        for n, ic in enumerate(itens, start=1):
            p = ic.item_pedido.produto
            dets.append(
                f'<det nItem="{n}"><prod><cProd>TESTE-{p.pk}</cProd><xProd>{escape(p.nome)}</xProd>'
                f"<qCom>{ic.quantidade:.4f}</qCom><vUnCom>{ic.valor_unitario:.4f}</vUnCom>"
                f"<vProd>{ic.subtotal:.2f}</vProd></prod></det>"
            )
        total_itens = sum(ic.subtotal for ic in itens)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe>'
            f'<infNFe Id="NFe{chave}" versao="4.00">'
            f"<ide><nNF>{numero}</nNF><dhEmi>2026-10-14T10:00:00-03:00</dhEmi></ide>"
            f"<emit><CNPJ>{emitente}</CNPJ><xNome>{escape(str(cotacao.fornecedor))}</xNome></emit>"
            f"<dest><CNPJ>{destinatario}</CNPJ><xNome>{escape(str(empresa))}</xNome></dest>"
            + "".join(dets)
            + f"<total><ICMSTot><vProd>{total_itens:.2f}</vProd><vFrete>{cotacao.valor_frete:.2f}</vFrete>"
            f"<vNF>{total_itens + cotacao.valor_frete:.2f}</vNF></ICMSTot></total>"
            "</infNFe></NFe></nfeProc>"
        )

        destino = Path(saida or f"nfe-teste-oc{oc.pk}.xml")
        destino.write_text(xml, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"NF-e de teste gravada em {destino.resolve()}"))
        if not _digitos(cotacao.fornecedor.cnpj):
            self.stdout.write(f"Fornecedor sem CNPJ: usado {CNPJ_FORNECEDOR_PADRAO} como emitente.")
