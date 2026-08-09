"""
Dispara o render do Remotion via subprocess, lendo a saída linha a linha para
reportar progresso. Usado tanto pelo CLI (pipeline.py, sem callback) quanto
pelo painel web (webapp/job_runner.py, com callback que empurra pra fila SSE).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from modules.config import PROJECT_ROOT, load_config, output_dir

_PROGRESS_RE = re.compile(r"Rendered (\d+)/(\d+)")

OnProgress = Callable[[int, int], None]


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
