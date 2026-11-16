import csv
import io
import re
import unicodedata
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from cadastros.models import Produto


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _ler_linhas(caminho):
    """Lista de dicts com as colunas do arquivo (.csv do Glide ou .xlsx de de-para)."""
    if Path(caminho).suffix.lower() in (".xlsx", ".xlsm"):
        ws = load_workbook(caminho, read_only=True, data_only=True).worksheets[0]
        ws.reset_dimensions()
        linhas = ws.iter_rows(values_only=True)
        cabecalho = [str(c or "").strip() for c in next(linhas, [])]
        return [
            {cabecalho[i]: str(v if v is not None else "").strip() for i, v in enumerate(linha) if i < len(cabecalho)}
            for linha in linhas
        ]
    texto = open(caminho, "rb").read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(texto)))


def _coluna(cabecalho, *nomes):
    for c in cabecalho:
        if normalizar(c) in nomes:
            return c
    return None


class Command(BaseCommand):
    help = (
        "Preenche o link da imagem dos produtos a partir do Glide. Aceita o CSV 'Materiais' do Glide "
        "(casa pelo nome idêntico, sem acento/pontuação) ou a planilha de de-para com a coluna 'Código' "
        "(casa pelo código; linhas com o código em branco são puladas). Sem --aplicar, só mostra a prévia."
    )

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="CSV do Glide ou .xlsx de de-para (colunas 'Código' e 'Foto')")
        parser.add_argument("--aplicar", action="store_true", help="Grava os links no banco")
        parser.add_argument("--substituir", action="store_true", help="Troca o link de quem já tem um")

    def handle(self, *args, **opts):
        try:
            linhas = _ler_linhas(opts["arquivo"])
        except FileNotFoundError:
            raise CommandError(f"Arquivo não encontrado: {opts['arquivo']}")
        cabecalho = list(linhas[0]) if linhas else []
        col_foto = _coluna(cabecalho, "foto do material", "foto")
        col_codigo = _coluna(cabecalho, "codigo")
        col_nome = _coluna(cabecalho, "materiais")
        if not col_foto or not (col_codigo or col_nome):
            raise CommandError("Colunas não encontradas: precisa de 'Foto' e de 'Código' (ou 'Materiais').")

        if col_codigo:
            por_chave = {p.codigo: [p] for p in Produto.objects.exclude(codigo=None)}
        else:
            por_chave = {}
            for p in Produto.objects.all():
                por_chave.setdefault(normalizar(p.nome), []).append(p)

        gravados = ambiguos = sem_par = ja_tem = sem_link = em_branco = 0
        sem_produto = []
        with transaction.atomic():
            for linha in linhas:
                url = (linha.get(col_foto) or "").strip()
                chave = (linha.get(col_codigo) or "").strip() if col_codigo else normalizar(linha.get(col_nome))
                if col_codigo and not chave:
                    em_branco += 1  # de-para ainda não conferido
                    continue
                if not url.startswith("https://"):
                    sem_link += 1
                    continue
                candidatos = por_chave.get(chave, [])
                if not candidatos:
                    sem_par += 1
                    sem_produto.append(chave)
                elif len(candidatos) > 1:
                    ambiguos += 1  # nome repetido no sistema: não dá para saber qual é
                else:
                    produto = candidatos[0]
                    if (produto.imagem or produto.imagem_url) and not opts["substituir"]:
                        ja_tem += 1
                        continue
                    produto.imagem_url = url
                    produto.save(update_fields=["imagem_url"])
                    gravados += 1
            if not opts["aplicar"]:
                transaction.set_rollback(True)

        resumo = f"Links a gravar: {gravados} | sem correspondência: {sem_par} | já tinham imagem: {ja_tem} | sem link: {sem_link}"
        if col_codigo:
            resumo += f" | código em branco (pulados): {em_branco}"
        else:
            resumo += f" | nome ambíguo (ignorados): {ambiguos}"
        self.stdout.write(resumo)
        if col_codigo and sem_produto:
            self.stdout.write("  códigos sem produto: " + ", ".join(sem_produto[:20]) + (" ..." if len(sem_produto) > 20 else ""))
        self.stdout.write(
            self.style.SUCCESS("Aplicado.") if opts["aplicar"]
            else self.style.WARNING("PRÉVIA — nada foi gravado. Use --aplicar para confirmar.")
        )
