"""Banco de imagens dos produtos: baixa, valida e nomeia as fotos para levar de um banco a outro.

A ligação entre bancos é pelo código do produto (o mesmo do Gestio), nunca pelo id.
"""
import io
import re
import unicodedata

import requests
from PIL import Image, UnidentifiedImageError

TAMANHO_MAXIMO = 8 * 1024 * 1024  # 8 MB por imagem
FORMATOS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


class ImagemInvalida(Exception):
    pass


def extensao_da_imagem(conteudo):
    """Confere que os bytes são mesmo uma imagem suportada e devolve a extensão."""
    if not conteudo:
        raise ImagemInvalida("arquivo vazio")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ImagemInvalida(f"maior que {TAMANHO_MAXIMO // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(conteudo)) as img:
            formato = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as erro:
        raise ImagemInvalida(f"não é uma imagem válida ({erro.__class__.__name__})")
    if formato not in FORMATOS:
        raise ImagemInvalida(f"formato {formato} não suportado")
    return FORMATOS[formato]


def baixar_imagem(url, sessao=None, timeout=20):
    """Baixa uma imagem por HTTPS, com limite de tamanho. Devolve (bytes, extensão)."""
    if not str(url).lower().startswith("https://"):
        raise ImagemInvalida("só links https:// são baixados")
    cliente = sessao or requests
    try:
        resposta = cliente.get(url, timeout=timeout, stream=True, headers={"User-Agent": "compras-suprimentos/1.0"})
        resposta.raise_for_status()
        partes, total = [], 0
        for parte in resposta.iter_content(64 * 1024):
            total += len(parte)
            if total > TAMANHO_MAXIMO:
                raise ImagemInvalida(f"maior que {TAMANHO_MAXIMO // (1024 * 1024)} MB")
            partes.append(parte)
    except requests.RequestException as erro:
        raise ImagemInvalida(f"falha ao baixar ({erro.__class__.__name__})")
    conteudo = b"".join(partes)
    return conteudo, extensao_da_imagem(conteudo)


def nome_de_arquivo(codigo, extensao):
    """Nome seguro para o arquivo a partir do código (o código exato vai no manifesto)."""
    base = unicodedata.normalize("NFKD", str(codigo)).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "produto"
    return f"{base[:80]}.{extensao}"
