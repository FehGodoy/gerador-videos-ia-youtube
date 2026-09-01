"""
Dispara o render do Remotion via subprocess, lendo a saída linha a linha para
reportar progresso. Usado tanto pelo CLI (pipeline.py, sem callback) quanto
pelo painel web (webapp/job_runner.py, com callback que empurra pra fila SSE).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from modules.config import PROJECT_ROOT, load_config, output_dir

logger = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"Rendered (\d+)/(\d+)")

OnProgress = Callable[[int, int], None]

_SUBSCRIBE_ICON_FILES = (
    "like-ativado.png",
    "like-desativado.png",
    "bell-novo.png",
    "bell-desativado.png",
    "hand-cursor.webp",
)


def stage_media_for_render(composition_path: Path, slug: str) -> Path:
    """Copia só os arquivos de mídia que este composition.json realmente
    referencia (áudio + clipes de footage) para uma pasta de staging isolada,
    usada como public dir do Remotion neste render.

    Antes, o public dir apontava pra raiz do projeto inteira — o Remotion
    copia recursivamente tudo que está lá antes de renderizar, então cada
    render copiava cache/ inteiro (todo footage já baixado em qualquer
    execução, não só desta) e output/ inteiro (todo vídeo já renderizado
    antes), um diretório que só cresce e nunca é limpo. Isso não só desperdiça
    tempo copiando dado que não é usado neste render, como piora a cada
    execução.

    Pública (não `_stage_media_for_render`) porque modules/github_render.py
    também usa — lá, a pasta é zipada e enviada pro runner do GitHub em vez
    de virar o public dir de um subprocess local.
    """
    staging_dir = PROJECT_ROOT / "cache" / "render_staging" / slug
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    # Textura de fundo da identidade visual (VideoComposition.tsx) — pintada
    # incondicionalmente em todo vídeo, não referenciada em nenhum campo do
    # composition.json, então precisa entrar aqui hardcoded (mesmo padrão
    # dos ícones fixos do subscribe abaixo) ou dá 404 no primeiro frame.
    referenced_paths = {composition["audio"]["path"], "assets/texture/paper-grain.png"}
    if composition.get("music"):
        referenced_paths.add(composition["music"]["path"])
    # O footage fica em beat["scenes"][i]["footage"] — um beat vira várias
    # cenas curtas. Enquanto isto aqui lia o antigo beat["footage"], o staging
    # saía só com a narração e o render morria no primeiro frame com 404 do
    # clipe (não dá erro na hora de montar o pacote, só lá no runner).
    for beat in composition["beats"]:
        for scene in beat.get("scenes", []):
            footage = scene.get("footage")
            if footage and footage.get("clip_path"):
                referenced_paths.add(footage["clip_path"])
            if footage and footage.get("blurred_background_path"):
                # Fundo pré-desfocado em Python (modules/image_effects.py)
                # pra cena de imagem — mesmo bug de antes (404 no primeiro
                # frame) se esse campo faltasse aqui.
                referenced_paths.add(footage["blurred_background_path"])
            # Cena "gallery" (Split Screen/Comparison Slider/Gallery Grid/
            # Masonry) não tem "footage" — a mídia real fica numa LISTA em
            # gallery.items. Mesmo bug de antes (404 no primeiro frame) se
            # esse braço faltasse aqui.
            gallery = scene.get("gallery")
            if gallery:
                for item in gallery.get("items", []):
                    if item.get("clip_path"):
                        referenced_paths.add(item["clip_path"])
                    if item.get("blurred_background_path"):
                        referenced_paths.add(item["blurred_background_path"])

    subscribe_popup = composition.get("subscribe_popup")
    if subscribe_popup:
        # Ícones fixos de UI (não são "footage" de shot nenhum, então nunca
        # apareceriam por scan de scene.footage/scene.gallery acima) — nomes
        # hardcoded de propósito, não glob: esta função só copia o que é
        # explicitamente citado, nunca o conteúdo inteiro de uma pasta.
        for icon_name in _SUBSCRIBE_ICON_FILES:
            referenced_paths.add(f"assets/subscribe/{icon_name}")
        if subscribe_popup.get("avatar_path"):
            referenced_paths.add(subscribe_popup["avatar_path"])

    missing = [p for p in sorted(referenced_paths) if not (PROJECT_ROOT / p).exists()]
    if missing:
        raise FileNotFoundError(
            "Arquivos de mídia referenciados no composition.json não existem em disco: "
            + ", ".join(missing)
        )

    for rel_path in referenced_paths:
        src = PROJECT_ROOT / rel_path
        dest = staging_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    logger.info("Staging pronto: %d arquivos em %s", len(referenced_paths), staging_dir)
    return staging_dir


def render_with_remotion(
    composition_path: Path, slug: str, on_progress: Optional[OnProgress] = None
) -> Path:
    """Roda `npx remotion render` e retorna o caminho do vídeo final.

    Cada linha de stdout é sempre impressa no terminal (preserva o
    comportamento visível do CLI). Se `on_progress` for passado, também é
    chamado com (frame_atual, total_frames) sempre que uma linha
    "Rendered N/M" aparecer na saída do Remotion.
    """
    cfg = load_config()
    remotion_dir = PROJECT_ROOT / cfg["remotion"]["project_dir"]
    if not (remotion_dir / "node_modules").exists():
        raise RuntimeError(
            f"'{remotion_dir}/node_modules' não existe. Rode 'npm install' dentro de "
            f"'{remotion_dir}' antes de renderizar."
        )

    video_path = output_dir(slug) / "video.mp4"
    staging_dir = stage_media_for_render(composition_path, slug)
    try:
        cmd = [
            "npx",
            "remotion",
            "render",
            "src/index.ts",
            cfg["remotion"]["composition_id"],
            str(video_path),
            f"--props={composition_path}",
        ]
        print(f"Renderizando com Remotion: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            cwd=remotion_dir,
            shell=(sys.platform == "win32"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "REMOTION_PUBLIC_DIR": str(staging_dir)},
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")
            print(line)
            if on_progress is not None:
                match = _PROGRESS_RE.search(line)
                if match:
                    on_progress(int(match.group(1)), int(match.group(2)))

        returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"Render do Remotion falhou (exit code {returncode}).")
        return video_path
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
