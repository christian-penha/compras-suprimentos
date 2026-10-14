import csv
import io
import re
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cadastros.models import Produto


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


class Command(BaseCommand):
    help = (
        "Preenche o link da imagem dos produtos a partir da exportação 'Materiais' do Glide, "
        "casando pelo nome idêntico (sem acento/pontuação). Sem --aplicar, só mostra a prévia."
    )

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="CSV do Glide (colunas 'Materiais' e 'Foto do material')")
        parser.add_argument("--aplicar", action="store_true", help="Grava os links no banco")

    def handle(self, *args, **opts):
        try:
            texto = open(opts["arquivo"], "rb").read().decode("utf-8-sig")
        except FileNotFoundError:
            raise CommandError(f"Arquivo não encontrado: {opts['arquivo']}")
        linhas = list(csv.DictReader(io.StringIO(texto)))
        if not linhas or "Materiais" not in linhas[0] or "Foto do material" not in linhas[0]:
            raise CommandError("Colunas 'Materiais' e 'Foto do material' não encontradas.")

        por_nome = {}
        for p in Produto.objects.all():
            por_nome.setdefault(normalizar(p.nome), []).append(p)

        gravados = ambiguos = sem_par = ja_tem = sem_link = 0
        with transaction.atomic():
            for linha in linhas:
                url = (linha["Foto do material"] or "").strip()
                if not url.startswith("https://"):
                    sem_link += 1
                    continue
                candidatos = por_nome.get(normalizar(linha["Materiais"]), [])
                if not candidatos:
                    sem_par += 1
                elif len(candidatos) > 1:
                    ambiguos += 1  # nome repetido no sistema: não dá para saber qual é
                else:
                    produto = candidatos[0]
                    if produto.imagem or produto.imagem_url:
                        ja_tem += 1
                        continue
                    produto.imagem_url = url
                    produto.save(update_fields=["imagem_url"])
                    gravados += 1
            if not opts["aplicar"]:
                transaction.set_rollback(True)

        self.stdout.write(
            f"Links a gravar: {gravados} | sem correspondência: {sem_par} | "
            f"nome ambíguo (ignorados): {ambiguos} | já tinham imagem: {ja_tem} | sem link: {sem_link}"
        )
        self.stdout.write(
            self.style.SUCCESS("Aplicado.") if opts["aplicar"]
            else self.style.WARNING("PRÉVIA — nada foi gravado. Use --aplicar para confirmar.")
        )
