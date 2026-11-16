import csv
import io
import zipfile

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q

from cadastros.imagens import ImagemInvalida, baixar_imagem, extensao_da_imagem, nome_de_arquivo
from cadastros.models import Produto


class Command(BaseCommand):
    help = (
        "Exporta o banco de imagens dos produtos para um .zip (uma foto por código + manifesto.csv). "
        "Usa a foto anexada; sem ela, baixa o link externo (Glide). Leve o .zip para o banco novo e "
        "rode importar_imagens."
    )

    def add_arguments(self, parser):
        parser.add_argument("saida", help="Arquivo .zip a gerar, ex.: imagens-produtos.zip")
        parser.add_argument("--sem-links", action="store_true", help="Não baixa os links externos, só as fotos anexadas")

    def handle(self, *args, saida, sem_links, **opcoes):
        produtos = Produto.objects.filter(Q(imagem__gt="") | Q(imagem_url__gt="")).order_by("codigo")
        sessao = requests.Session()
        exportadas, sem_codigo, falhas = 0, [], []
        usados = set()
        with zipfile.ZipFile(saida, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            manifesto = io.StringIO()
            escritor = csv.writer(manifesto)
            escritor.writerow(["arquivo", "codigo", "produto", "origem"])
            for p in produtos.iterator():
                if not p.codigo:
                    sem_codigo.append(str(p))
                    continue
                try:
                    if p.imagem:
                        with p.imagem.open("rb") as f:
                            conteudo = f.read()
                        extensao, origem = extensao_da_imagem(conteudo), "anexo"
                    elif p.imagem_url and not sem_links:
                        conteudo, extensao = baixar_imagem(p.imagem_url, sessao=sessao)
                        origem = "link"
                    else:
                        continue
                except (ImagemInvalida, OSError) as erro:
                    falhas.append(f"{p.codigo}: {erro}")
                    continue
                arquivo = nome_de_arquivo(p.codigo, extensao)
                n = 2
                while arquivo.lower() in usados:  # códigos diferentes que viram o mesmo nome seguro
                    arquivo = nome_de_arquivo(f"{p.codigo}_{n}", extensao)
                    n += 1
                usados.add(arquivo.lower())
                zf.writestr(f"imagens/{arquivo}", conteudo)
                escritor.writerow([arquivo, p.codigo, str(p), origem])
                exportadas += 1
                if exportadas % 50 == 0:
                    self.stdout.write(f"  {exportadas} imagens...")
            zf.writestr("manifesto.csv", manifesto.getvalue().encode("utf-8-sig"))

        self.stdout.write(self.style.SUCCESS(f"{exportadas} imagem(ns) exportada(s) em {saida}."))
        if sem_codigo:
            self.stdout.write(self.style.WARNING(
                f"{len(sem_codigo)} produto(s) com imagem mas SEM código (não dá para ligar no banco novo): "
                + "; ".join(sem_codigo[:10]) + (" ..." if len(sem_codigo) > 10 else "")
            ))
        if falhas:
            self.stdout.write(self.style.WARNING(f"{len(falhas)} falha(s):"))
            for f in falhas:
                self.stdout.write(f"  - {f}")
