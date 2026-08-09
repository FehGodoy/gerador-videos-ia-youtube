#!/usr/bin/env python
"""
Orquestrador principal (Fase 1): roda o pipeline de dados (passos 1-4 e 6) e
depois dispara o render do Remotion via subprocess.

Checkpoint/cache: cada módulo (narration, keyword_extractor, footage_search)
já cacheia seu próprio resultado por beat em cache/<módulo>/<slug>/. Isso dá
reprocessamento parcial automático e mais granular do que um checkpoint por
etapa: se o pipeline falhar no beat 6 de 8, rodar de novo só refaz o que
faltou (os beats 1-5 já cacheados são reaproveitados na hora). Use --force
para ignorar o cache e regerar tudo do zero.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from modules.composition_builder import build_composition
from modules.config import PROJECT_ROOT, load_config, output_dir, slugify


def _clear_cache(slug: str) -> None:
    cfg = load_config()
    cache_root = PROJECT_ROOT / cfg["paths"]["cache_dir"]
    for module_dir in ("narration", "keywords"):
        target = cache_root / module_dir / slug
        if target.exists():
            shutil.rmtree(target)
    # cache/footage é compartilhado entre roteiros (por hash do termo de busca)
    # e não é limpo pelo --force: é um cache de download, não de resultado.


def _render_with_remotion(composition_path: Path, slug: str) -> Path:
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
    print(f"[6/6] Renderizando com Remotion: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=remotion_dir, shell=(sys.platform == "win32"))
    if result.returncode != 0:
        raise RuntimeError(f"Render do Remotion falhou (exit code {result.returncode}).")
    return video_path


def run_pipeline(script_path: str, force: bool = False, skip_render: bool = False) -> None:
    script = Path(script_path)
    if not script.exists():
        raise FileNotFoundError(f"Roteiro não encontrado: {script}")

    slug = slugify(script.stem)
    if force:
        print(f"[0/6] --force: limpando cache de narração/keywords para '{slug}'")
        _clear_cache(slug)

    print(f"[1-5/6] Rodando pipeline de dados para '{slug}' (roteiro: {script})")
    composition = build_composition(str(script), slug)
    composition_path = output_dir(slug) / "composition.json"
    n_beats = len(composition["beats"])
    duration = composition["audio"]["duration_seconds"]
    print(f"        composition.json pronto: {n_beats} beats, {duration:.1f}s de narração.")

    if skip_render:
        print("        --skip-render: pulando etapa de renderização.")
        return

    video_path = _render_with_remotion(composition_path, slug)
    print(f"[OK] Vídeo final: {video_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de produção de vídeo documentário (Fase 1)")
    parser.add_argument("--script", required=True, help="Caminho do roteiro (.txt ou .md)")
    parser.add_argument(
        "--force", action="store_true", help="Ignora o cache e regera narração/keywords do zero"
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        help="Gera só o composition.json, sem chamar o Remotion (útil para debug)",
    )
    args = parser.parse_args()

    try:
        run_pipeline(args.script, force=args.force, skip_render=args.skip_render)
    except Exception as e:
        print(f"\n[ERRO] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
