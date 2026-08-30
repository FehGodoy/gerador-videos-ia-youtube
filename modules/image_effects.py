"""Pré-computa fundos desfocados de imagem em Python, uma vez por imagem,
em vez do Remotion recalcular `filter: blur()` a cada frame no Chromium.

Descoberto investigando um render do GitHub Actions que nunca terminava
(estimativa de 41h+): `blur(60px)` em tela cheia é quase de graça num
Chromium com aceleração de GPU (a máquina local do usuário), mas caríssimo
num Chromium renderizando em software — o caso dos runners `ubuntu-latest`
do GitHub, sem GPU. Um vídeo 100% de fotos (0 clipes de vídeo) caiu de
~12 frames/s pra ~0,23 frames/s depois que esse fundo desfocado virou
padrão pra toda cena de imagem (commit 8138fc3 + Parallax Pan da Fase 5).
"""
from __future__ import annotations

import hashlib

from PIL import Image, ImageFilter, ImageOps

from modules.config import PROJECT_ROOT, cache_dir


def get_blurred_background(clip_path: str, width: int, height: int, radius: float = 30) -> str:
    """Devolve o caminho (relativo à raiz do projeto) de uma versão
    desfocada + cortada (cover, mesmo resultado visual de objectFit: cover)
    de `clip_path`, gerando e cacheando se ainda não existir.

    `radius` é o desvio-padrão do kernel gaussiano do Pillow, não a mesma
    unidade do `blur(Npx)` do CSS (que equivale a um desvio-padrão de
    aproximadamente N/2) — o default 30 corresponde visualmente ao antigo
    `blur(60px)` de FootageClip.tsx/ParallaxPan.tsx; AnimatedChart.tsx (que
    usava `blur(20px)`) deve chamar com `radius=10`.
    """
    cache_key = hashlib.sha1(f"{clip_path}:{width}x{height}:{radius}".encode("utf-8")).hexdigest()[:16]
    out_path = cache_dir("blurred_bg") / f"{cache_key}.jpg"
    if not out_path.exists():
        # .convert("RGB") é obrigatório e incondicional: fontes de imagem
        # deste projeto incluem PNG/GIF/WEBP (_MANUAL_IMAGE_EXTENSIONS em
        # footage_search.py), que comumente vêm com canal alfa — salvar
        # RGBA como JPEG sem converter antes lança OSError.
        src = Image.open(PROJECT_ROOT / clip_path).convert("RGB")
        cropped = ImageOps.fit(src, (width, height), Image.LANCZOS)
        blurred = cropped.filter(ImageFilter.GaussianBlur(radius))
        blurred.save(out_path, "JPEG", quality=82)
    return out_path.relative_to(PROJECT_ROOT).as_posix()
