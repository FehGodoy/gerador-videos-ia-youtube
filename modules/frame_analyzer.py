"""
Fase 3: análise de frames pra escolher o MELHOR TRECHO dentro de um clipe já
baixado — em vez do rodízio cego de hoje (composition_builder._tile_scenes
espalhava reusos em pontos matematicamente equidistantes do clipe, sem saber
se aquele ponto mostra algo útil ou cai numa transição/tela preta/corte ruim).

Só compensa rodar isso em clipes bem mais longos que uma cena — é aí que
existe uma escolha real de trecho a fazer; um clipe curto usado inteiro não
precisa de recomendação nenhuma.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from modules.config import cache_dir, load_config

logger = logging.getLogger(__name__)

# Espaçamento entre frames analisados: junto o bastante pra não pular um
# trecho ruim inteiro entre dois frames, esparso o bastante pra não estourar
# o orçamento de tokens de imagem por chamada.
FRAME_INTERVAL_SECONDS = 4
MAX_FRAMES = 10
MIN_FRAMES_TO_TRUST = 3
# Abaixo disso o clipe inteiro cabe numa cena e não tem "melhor trecho" pra
# escolher — analisar seria gasto de chamada de IA à toa.
MIN_DURATION_FOR_ANALYSIS = 14.0
# Claude cobra por patch de 28x28px (tokens = ⌈largura/28⌉ × ⌈altura/28⌉), e
# Haiku é tier padrão (teto de 1568px). Frame em resolução nativa (ex:
# 1920x1080) custa ~1560 tokens; essa análise só precisa identificar
# transição/tela preta/corte ruim, não ver detalhe fino — o mesmo raciocínio
# que já faz o ranking usar miniatura em vez de imagem cheia. Encolhendo pro
# lado maior aqui, um frame 16:9 cai pra ~375 tokens (~4x mais barato).
FRAME_MAX_DIMENSION = 700


def _extract_frames(clip_path: str, duration: float) -> list[tuple[float, bytes]]:
    """Extrai frames em timestamps espaçados via ffmpeg, redimensionados pra
    baratear a análise. Devolve [(segundo, jpeg_bytes)]."""
    n = min(MAX_FRAMES, max(MIN_FRAMES_TO_TRUST, int(duration // FRAME_INTERVAL_SECONDS)))
    timestamps = [duration * i / (n - 1) if n > 1 else 0.0 for i in range(n)]
    # nunca no ultimíssimo instante: alguns encoders corrompem/pretejam o
    # último frame do arquivo
    timestamps = [min(t, max(0.0, duration - 0.3)) for t in timestamps]

    # Encolhe o lado MAIOR pro teto, preservando proporção e orientação (o
    # -2 força dimensão par, exigido por alguns encoders — sem custo aqui
    # porque é so 1px de folga que o "if" já não usa de qualquer forma).
    scale_filter = (
        f"scale='if(gt(iw,ih),{FRAME_MAX_DIMENSION},-2)':"
        f"'if(gt(iw,ih),-2,{FRAME_MAX_DIMENSION})'"
    )

    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(timestamps):
            out = Path(tmp) / f"f{i}.jpg"
            resultado = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(t),
                    "-i", clip_path,
                    "-frames:v", "1",
                    "-vf", scale_filter,
                    "-q:v", "4",
                    str(out),
                ],
                capture_output=True,
            )
            if resultado.returncode == 0 and out.exists() and out.stat().st_size > 0:
                frames.append((round(t, 2), out.read_bytes()))
    return frames


def _ask_claude(context: str, frames: list[tuple[float, bytes]]) -> list[list[float]] | None:
    import anthropic

    cfg = load_config()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "Este clipe de vídeo já foi escolhido pra uma cena de documentário. "
                f"{context}\n\n"
                f"Abaixo estão {len(frames)} frames tirados em segundos diferentes do vídeo "
                "inteiro (o segundo de cada um está indicado antes da imagem). Alguns podem "
                "ser transição, tela preta ou borrada, ou simplesmente não mostrar nada "
                "relevante pro contexto.\n"
                "Identifique quais INTERVALOS de tempo (entre os segundos que você viu) "
                "mostram conteúdo útil e relevante — evite trechos de transição, tela "
                "preta/borrada, ou irrelevantes.\n"
                "Responda APENAS com um JSON, sem markdown: "
                '{"good_ranges": [[inicio_segundos, fim_segundos], ...]} '
                "ordenados do melhor trecho pro pior. Se o vídeo inteiro for uniformemente "
                "bom ou uniformemente ruim, devolva um único intervalo cobrindo tudo."
            ),
        }
    ]
    for t, img_bytes in frames:
        content.append({"type": "text", "text": f"Segundo {t}:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("ascii"),
                },
            }
        )

    response = client.messages.create(
        model=cfg["keywords"]["model"],
        max_tokens=500,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    parsed = json.loads(raw)

    ranges = parsed.get("good_ranges")
    if not isinstance(ranges, list):
        return None
    cleaned = []
    for r in ranges:
        if isinstance(r, list) and len(r) == 2:
            try:
                a, b = float(r[0]), float(r[1])
            except (TypeError, ValueError):
                continue
            if b > a:
                cleaned.append([round(a, 2), round(b, 2)])
    return cleaned or None


def find_good_segments(clip_path: str, duration: float | None, context: str) -> list[list[float]]:
    """Devolve intervalos [início, fim] (segundos) recomendados dentro do
    clipe, do melhor pro pior. Lista vazia = não analisou ou não vale a pena
    (clipe curto, sem chave de API, erro de rede/parse) — quem chama deve
    cair no rodízio cego de sempre.

    Cacheado em disco por caminho do clipe (que já é um hash de conteúdo —
    ver footage_search._candidate_cache_key): útil regenerar um rascunho não
    reanalisa o mesmo clipe. Só cacheia resultado de uma chamada que
    completou (sucesso OU "nenhum trecho bom" genuíno); falha de rede não
    fica presa em cache, tenta de novo na próxima.
    """
    if not duration or duration < MIN_DURATION_FOR_ANALYSIS:
        return []

    cache_file = cache_dir("frame_analysis") / f"{Path(clip_path).stem}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return []

    try:
        frames = _extract_frames(clip_path, duration)
        if len(frames) < MIN_FRAMES_TO_TRUST:
            return []
        ranges = _ask_claude(context, frames) or []
    except Exception:
        logger.exception("Análise de frames falhou para %s, usando rodízio cego", clip_path)
        return []

    cache_file.write_text(json.dumps(ranges, ensure_ascii=False), encoding="utf-8")
    return ranges


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python -m modules.frame_analyzer <caminho-do-clipe> <duracao-segundos> [contexto]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    path, dur = sys.argv[1], float(sys.argv[2])
    ctx = sys.argv[3] if len(sys.argv) > 3 else "Cena genérica de documentário."
    result = find_good_segments(path, dur, ctx)
    print(json.dumps(result, ensure_ascii=False, indent=2))
