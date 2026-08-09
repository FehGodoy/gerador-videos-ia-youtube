"""
Renderiza via GitHub Actions em vez de local: empacota a mídia referenciada
pelo composition.json, sobe como asset de uma Release temporária (só como
forma de transporte pro runner do GitHub, não é a entrega final), dispara
o workflow .github/workflows/render.yml, espera terminar, baixa o artifact
resultante e limpa a release temporária.

Tudo via `gh` CLI em subprocess — sem dependência Python nova (nada de
PyGithub), reaproveitando a mesma autenticação já configurada no `gh`.
Progresso aqui é por estágio (texto), não por frame — o GitHub não expõe
progresso frame-a-frame como o subprocess local (ver modules/renderer.py).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

from modules.config import PROJECT_ROOT, output_dir
from modules.renderer import stage_media_for_render

WORKFLOW_FILE = "render.yml"
POLL_INTERVAL_SECONDS = 15

OnStatus = Callable[[str], None]


def _gh_executable() -> str:
    found = shutil.which("gh")
    if found:
        return found
    windows_default = r"C:\Program Files\GitHub CLI\gh.exe"
    if Path(windows_default).exists():
        return windows_default
    raise RuntimeError(
        "gh CLI não encontrado. Instale (winget install GitHub.cli) e rode 'gh auth login'."
    )


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [_gh_executable(), *args], capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} falhou: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _delete_release_if_exists(repo: str, tag: str) -> None:
    subprocess.run(
        [_gh_executable(), "release", "delete", tag, "--repo", repo, "--yes", "--cleanup-tag"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def _repo_slug() -> str:
    result = _run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return result.stdout.strip()


def _zip_staging_dir(staging_dir: Path, slug: str) -> Path:
    zip_path = PROJECT_ROOT / "cache" / "github_render" / slug / "render_input.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in staging_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(staging_dir))
    return zip_path


def _find_run_id(repo: str, slug: str, attempts: int = 10) -> str:
    expected_name = f"render-{slug}"
    for _ in range(attempts):
        time.sleep(2)
        result = _run_gh(
            [
                "run",
                "list",
                "--repo",
                repo,
                "--workflow",
                WORKFLOW_FILE,
                "--limit",
                "5",
                "--json",
                "databaseId,displayTitle",
            ]
        )
        for run in json.loads(result.stdout):
            if run["displayTitle"] == expected_name:
                return str(run["databaseId"])
    raise RuntimeError("Não encontrei o run do GitHub Actions depois de disparar o workflow.")


def _run_status(repo: str, run_id: str) -> dict:
    result = _run_gh(["run", "view", run_id, "--repo", repo, "--json", "status,conclusion,url"])
    return json.loads(result.stdout)


def _download_artifact(repo: str, run_id: str, slug: str) -> Path:
    download_dir = PROJECT_ROOT / "cache" / "github_render" / slug / "artifact"
    if download_dir.exists():
        shutil.rmtree(download_dir)
    _run_gh(["run", "download", run_id, "--repo", repo, "--name", "video", "--dir", str(download_dir)])

    downloaded_video = download_dir / "video.mp4"
    if not downloaded_video.exists():
        raise RuntimeError(f"Artifact baixado mas video.mp4 não encontrado em {download_dir}")

    final_path = output_dir(slug) / "video.mp4"
    shutil.move(str(downloaded_video), str(final_path))
    shutil.rmtree(download_dir, ignore_errors=True)
    return final_path


def render_via_github_actions(
    composition_path: Path, slug: str, on_status: Optional[OnStatus] = None
) -> Path:
    def status(msg: str) -> None:
        print(msg)
        if on_status is not None:
            on_status(msg)

    repo = _repo_slug()
    release_tag = f"render-{slug}"

    status("Preparando arquivos para envio...")
    staging_dir = stage_media_for_render(composition_path, slug)
    try:
        zip_path = _zip_staging_dir(staging_dir, slug)

        status("Enviando arquivos para o GitHub...")
        _delete_release_if_exists(repo, release_tag)  # sobra de uma tentativa anterior, se houver
        _run_gh(
            [
                "release",
                "create",
                release_tag,
                str(zip_path),
                "--repo",
                repo,
                "--title",
                f"Render input: {slug}",
                "--notes",
                "Arquivo temporário usado só para transportar mídia até o GitHub Actions. "
                "Apagado automaticamente após o render.",
            ]
        )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    status("Disparando o render no GitHub Actions...")
    _run_gh(
        [
            "workflow",
            "run",
            WORKFLOW_FILE,
            "--repo",
            repo,
            "-f",
            f"slug={slug}",
            "-f",
            f"release_tag={release_tag}",
        ]
    )

    status("Na fila do GitHub Actions...")
    run_id = _find_run_id(repo, slug)

    started_at = time.time()
    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        info = _run_status(repo, run_id)
        if info["status"] != "completed":
            elapsed_min = int((time.time() - started_at) / 60)
            status(f"Renderizando no GitHub (rodando há {elapsed_min}min)...")
            continue
        if info["conclusion"] != "success":
            raise RuntimeError(
                f"Render no GitHub Actions falhou ({info['conclusion']}). Veja o log: {info['url']}"
            )
        break

    status("Baixando o vídeo renderizado...")
    video_path = _download_artifact(repo, run_id, slug)

    status("Limpando arquivos temporários no GitHub...")
    _delete_release_if_exists(repo, release_tag)

    return video_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Uso: python -m modules.github_render <composition.json> <slug>")
        sys.exit(1)

    result_path = render_via_github_actions(Path(sys.argv[1]), sys.argv[2], on_status=print)
    print(f"Vídeo final: {result_path}")
