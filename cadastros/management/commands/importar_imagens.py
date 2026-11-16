import csv
import io
import zipfile
from pathlib import Path

import requests
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from cadastros.imagens import ImagemInvalida, baixar_imagem, extensao_da_imagem, nome_de_arquivo
from cadastros.models import Produto

EXTENSOES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _itens_do_zip(caminho):
    """(codigo, nome do arquivo, bytes) de um .zip do exportar_imagens. Lê sem extrair nada no disco."""
    with zipfile.ZipFile(caminho) as zf:
        nomes = {Path(n).name: n for n in zf.namelist() if not n.endswith("/")}
        if "manifesto.csv" in nomes:
            texto = zf.read(nomes["manifesto.csv"]).decode("utf-8-sig")
            for linha in csv.DictReader(io.StringIO(texto)):
                arquivo = nomes.get(linha["arquivo"])
                if arquivo:
                    yield linha["codigo"], linha["arquivo"], zf.read(arquivo)
        else:  # zip montado à mão: o nome do arquivo é o código (PAPOFI-001.jpg)
            for nome, interno in nomes.items():
                if Path(nome).suffix.lower() in EXTENSOES:
                    yield Path(nome).stem, nome, zf.read(interno)


def _itens_da_pasta(caminho):
    for arquivo in sorted(Path(caminho).iterdir()):
        if arquivo.is_file() and arquivo.suffix.lower() in EXTENSOES:
            yield arquivo.stem, arquivo.name, arquivo.read_bytes()


class Command(BaseCommand):
    help = (
        "Importa o banco de imagens para os produtos, ligando pelo código. Aceita o .zip do "
        "exportar_imagens ou uma pasta/zip com fotos nomeadas pelo código (PAPOFI-001.jpg). "
        "Sem --aplicar, só mostra a prévia."
    )

    def add_arguments(self, parser):
        parser.add_argument("origem", nargs="?", help=".zip ou pasta com as imagens")
        parser.add_argument("--aplicar", action="store_true", help="Grava as imagens")
        parser.add_argument("--substituir", action="store_true", help="Troca a foto de quem já tem imagem anexada")
        parser.add_argument(
            "--baixar-links", action="store_true",
            help="Também baixa para o sistema as imagens que hoje são só link externo (Glide)",
        )

    def handle(self, *args, origem, aplicar, substituir, baixar_links, **opcoes):
        if not origem and not baixar_links:
            raise CommandError("Informe o .zip/pasta de imagens ou use --baixar-links.")
        gravadas, ja_tinham, sem_produto, invalidas = 0, 0, [], []

        def gravar(produto, conteudo, extensao):
            nonlocal gravadas
            if aplicar:
                antiga = produto.imagem.name if produto.imagem else None
                produto.imagem.save(nome_de_arquivo(produto.codigo or produto.pk, extensao), ContentFile(conteudo), save=False)
                produto.save(update_fields=["imagem"])
                if antiga and antiga != produto.imagem.name:
                    produto.imagem.storage.delete(antiga)
            gravadas += 1

        if origem:
            caminho = Path(origem)
            if not caminho.exists():
                raise CommandError(f"Não encontrado: {origem}")
            if caminho.is_dir():
                itens = _itens_da_pasta(caminho)
            elif zipfile.is_zipfile(caminho):
                itens = _itens_do_zip(caminho)
            else:
                raise CommandError("A origem deve ser um .zip ou uma pasta.")
            for codigo, arquivo, conteudo in itens:
                produto = Produto.objects.filter(codigo=codigo).first()
                if produto is None:
                    sem_produto.append(f"{arquivo} (código {codigo})")
                    continue
                if produto.imagem and not substituir:
                    ja_tinham += 1
                    continue
                try:
                    extensao = extensao_da_imagem(conteudo)
                except ImagemInvalida as erro:
                    invalidas.append(f"{arquivo}: {erro}")
                    continue
                gravar(produto, conteudo, extensao)

        baixadas = 0
        if baixar_links:
            sessao = requests.Session()
            pendentes = Produto.objects.filter(imagem_url__startswith="https://").filter(
                Q(imagem="") | Q(imagem__isnull=True)
            )
            for produto in pendentes.iterator():
                try:
                    conteudo, extensao = baixar_imagem(produto.imagem_url, sessao=sessao)
                except ImagemInvalida as erro:
                    invalidas.append(f"link de {produto.codigo or produto}: {erro}")
                    continue
                gravar(produto, conteudo, extensao)
                baixadas += 1

        self.stdout.write(
            f"Imagens {'gravadas' if aplicar else 'a gravar'}: {gravadas}"
            + (f" (das quais {baixadas} baixadas de links)" if baixar_links else "")
            + f" | produto já tinha foto: {ja_tinham} | sem produto com o código: {len(sem_produto)}"
            + f" | inválidas: {len(invalidas)}"
        )
        for linha in sem_produto[:20]:
            self.stdout.write(f"  sem produto: {linha}")
        if len(sem_produto) > 20:
            self.stdout.write(f"  ... e mais {len(sem_produto) - 20}")
        for linha in invalidas:
            self.stdout.write(f"  inválida: {linha}")
        self.stdout.write(
            self.style.SUCCESS("Aplicado.") if aplicar
            else self.style.WARNING("PRÉVIA — nada foi gravado. Use --aplicar para confirmar.")
        )
