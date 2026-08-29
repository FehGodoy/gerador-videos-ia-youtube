"""
Modo alternativo de mídia: em vez da busca automática por IA
(modules/footage_search.py + modules/footage_ranker.py), o usuário sobe seu
próprio lote de fotos/vídeos e este módulo distribui essa mídia pelo vídeo
inteiro numa sequência fixa (foto, foto, vídeo, ...), sem nenhum julgamento
de relevância — quem escolheu o material foi o próprio usuário.

Separado de footage_search.py (que já é grande e inteiro sobre busca/
ranking) porque não há sobreposição real: aqui não tem fonte externa, não
tem candidato, não tem nota.
"""
from __future__ import annotations

import logging
import random
import subprocess
import uuid
from pathlib import Path

from modules.config import cache_dir

logger = logging.getLogger(__name__)

_POOL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Fixo por decisão do usuário (não configurável por enquanto): 2 fotos pra
# cada vídeo, repetindo pelo vídeo inteiro.
_PATTERN = ("photo", "photo", "video")

# Piso confirmado com o usuário: um trecho de vídeo nunca é mais curto que
# isso, mesmo que a duração-alvo (scene_seconds) não caiba.
_MIN_VIDEO_SEGMENT_SECONDS = 3.0

# Quantas vezes tenta achar um início que não sobreponha um trecho já usado
# da mesma fonte antes de desistir e aceitar a sobreposição mesmo assim
# (degradação graciosa pra fonte curta com muitos reusos, não uma falha).
_MAX_START_ATTEMPTS = 10


def save_pool_upload(slug: str, filename: str, data: bytes) -> dict:
    """Salva um arquivo do lote de mídia própria pro `slug`. Escopado por
    slug (cache_dir("own_media", slug)) — cada vídeo tem seu próprio pool,
    diferente do cache_dir("footage") compartilhado que o upload manual
    single-file (footage_search.save_manual_upload) usa."""
    ext = Path(filename).suffix.lower() or ".jpg"
    media_type = "image" if ext in _POOL_IMAGE_EXTENSIONS else "video"

    dest_name = f"{uuid.uuid4().hex[:16]}{ext}"
    dest = cache_dir("own_media", slug) / dest_name
    dest.write_bytes(data)

    result: dict = {"media_type": media_type, "filename": dest_name, "path": str(dest)}
    if media_type == "video":
        result["duration"] = _probe_duration(dest)
    return result


def list_pool(slug: str) -> dict:
    """{"photos": [Path, ...], "videos": [Path, ...]} — tudo que já foi
    enviado pra este slug, classificado pela extensão."""
    pool_dir = cache_dir("own_media", slug)
    photos, videos = [], []
    for path in sorted(pool_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in _POOL_IMAGE_EXTENSIONS:
            photos.append(path)
        else:
            videos.append(path)
    return {"photos": photos, "videos": videos}


def _probe_duration(path: Path) -> float | None:
    """Duração em segundos via ffprobe. None se não der pra medir (arquivo
    corrompido, upload inválido) — mesmo padrão de
    footage_search._probe_video_duration."""
    try:
        resultado = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return round(float(resultado.stdout.strip()), 2)
    except Exception:
        return None


def _photo_dict(path: Path) -> dict:
    return {
        "clip_path": str(path),
        "source": "manual",
        "media_type": "image",
        "duration": None,
        "search_terms": [],
        "relevance_score": None,
        "ai_reasoning": "",
    }


def _trim_video(source: Path, start: float, end: float, dest: Path) -> None:
    """Corta [start, end) de `source` em `dest` sem recodificar (-c copy,
    quase instantâneo). -ss ANTES de -i: seek rápido, mesmo padrão já usado
    em footage_search._download_youtube pro corte de trecho do YouTube."""
    resultado = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(round(start, 2)), "-to", str(round(end, 2)),
            "-i", str(source),
            "-c", "copy", "-avoid_negative_ts", "make_zero",
            "-f", "mp4",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"corte de trecho com ffmpeg falhou: {resultado.stderr[-300:]}")


class PoolDistributor:
    """Estado do rodízio foto/foto/vídeo pro vídeo INTEIRO — uma instância
    por composição, não por beat (o padrão precisa ser contínuo do início ao
    fim, não reiniciar a cada bloco de narração)."""

    def __init__(self, slug: str, cfg: dict):
        self._scene_seconds = cfg["footage"]["scene_seconds"]
        self._trim_dir = cache_dir("own_media_trimmed", slug)
        pool = list_pool(slug)
        self._photos = pool["photos"]
        self._videos = pool["videos"]
        self._pattern_index = 0
        self._photo_index = 0
        self._video_index = 0
        # cursores independentes dos de cima — next_gallery_items nunca
        # perturba o ritmo foto/foto/vídeo do resto do vídeo
        self._gallery_photo_index = 0
        self._gallery_video_index = 0
        self._trim_counter = 0
        # fonte (str do path) -> lista de pontos de início já usados, pra
        # não sortear um trecho que sobrepõe um já mostrado antes
        self._used_starts: dict[str, list[float]] = {}

    def next_shot_media(self, beat_id: int, slot: int) -> dict:
        """Avança o cursor do padrão foto/foto/vídeo e devolve um dict no
        mesmo formato que search_and_download_footage devolve hoje pra
        _assemble_composition consumir sem distinção."""
        kind = _PATTERN[self._pattern_index % len(_PATTERN)]
        self._pattern_index += 1

        if kind == "photo" and self._photos:
            return self._next_photo()
        if self._videos:
            return self._next_video()
        if self._photos:
            return self._next_photo()
        # os dois pools vazios (modo escolhido mas nada foi enviado): vira
        # card conceitual, mesmo comportamento de "não achei nada bom" já
        # usado hoje quando a busca por IA não acha nada.
        return {"clip_path": None, "relevance_score": None}

    def _next_photo(self) -> dict:
        path = self._photos[self._photo_index % len(self._photos)]
        self._photo_index += 1
        return _photo_dict(path)

    def _next_video(self) -> dict:
        path = self._videos[self._video_index % len(self._videos)]
        self._video_index += 1
        return self._trim_random_segment(path)

    def next_gallery_items(self, n: int) -> list[dict]:
        """Puxa `n` itens pra um shot de galeria (Split Screen/Gallery Grid/
        Comparison Slider/Masonry) — cursor PRÓPRIO
        (`_gallery_photo_index`/`_gallery_video_index`), não avança
        `_pattern_index`/`_photo_index`/`_video_index` principais, senão
        desalinharia o ritmo foto/foto/vídeo do resto do vídeo. Prioriza
        foto (mais barato, mais consistente visualmente numa colagem) — só
        usa vídeo se o pool de fotos estiver vazio."""
        if self._photos:
            items = []
            for _ in range(n):
                path = self._photos[self._gallery_photo_index % len(self._photos)]
                self._gallery_photo_index += 1
                items.append(_photo_dict(path))
            return items
        if self._videos:
            items = []
            for _ in range(n):
                path = self._videos[self._gallery_video_index % len(self._videos)]
                self._gallery_video_index += 1
                items.append(self._trim_random_segment(path))
            return items
        return []

    def reuse_gallery_item(self, item: dict) -> dict:
        """Chamada quando um shot de galeria reaparece dentro do beat — pega
        o PRÓXIMO item pelo cursor de galeria (mesmo de next_gallery_items),
        nunca o cursor principal. Bug real pego testando: usar reuse_media
        (feito pra shot ÚNICO) aqui lia/avançava _photo_index — o cursor
        PRINCIPAL, não o de galeria — desalinhando o ritmo foto/foto/vídeo
        do resto do vídeo, exatamente o que next_gallery_items existe pra
        evitar. Diferente de reuse_media pra vídeo (que corta um trecho novo
        da MESMA fonte): aqui é mais simples e mais consistente só pedir o
        próximo item do rodízio de galeria, sem tentar preservar a fonte
        antiga do item recebido."""
        pulled = self.next_gallery_items(1)
        return pulled[0] if pulled else item

    def reuse_media(self, footage: dict) -> dict:
        """Chamada por _tile_scenes quando um shot do pool precisa reaparecer
        dentro do mesmo beat (o beat pede mais cortes de tela do que shots
        distintos existem). Despacha por media_type — o objetivo dos dois é o
        mesmo (nunca mostrar a mesma mídia de novo à toa quando o pool tem
        mais opção), mas a técnica é diferente pra cada um."""
        if footage.get("media_type") == "video":
            return self._reuse_video(footage)
        return self._next_photo()

    def _reuse_video(self, footage: dict) -> dict:
        """Vídeo já é só o trecho exato do shot (sem folga pra deslocar
        clip_start como o resto da lógica de tiling faz) — corta um trecho
        ALEATÓRIO NOVO da mesma fonte em vez de repetir o trecho exato."""
        source_path = footage.get("source_path")
        if not source_path:
            # não deveria acontecer (todo dict de vídeo do pool carrega
            # source_path) — sem ele não tem como re-sortear, mantém o atual
            return footage
        return self._trim_random_segment(Path(source_path))

    def _trim_random_segment(self, source_path: Path) -> dict:
        duration_total = _probe_duration(source_path)
        if not duration_total or duration_total < _MIN_VIDEO_SEGMENT_SECONDS:
            logger.warning(
                "Vídeo do pool não deu pra medir ou é curto demais (%s), pulando: %s",
                duration_total,
                source_path,
            )
            return {"clip_path": None, "relevance_score": None}

        segment_duration = max(
            _MIN_VIDEO_SEGMENT_SECONDS, min(self._scene_seconds, duration_total)
        )
        start = self._pick_start(str(source_path), duration_total, segment_duration)

        self._trim_counter += 1
        dest = self._trim_dir / f"trecho_{self._trim_counter:04d}.mp4"
        _trim_video(source_path, start, start + segment_duration, dest)

        return {
            "clip_path": str(dest),
            "source": "manual",
            "media_type": "video",
            "duration": round(segment_duration, 2),
            "search_terms": [],
            "relevance_score": None,
            "ai_reasoning": "",
            # não vai pro composition.json (_scene_footage só copia chaves
            # conhecidas) — usado só por reuse_video pra saber de qual
            # arquivo original cortar o próximo trecho.
            "source_path": str(source_path),
        }

    def _pick_start(self, source_key: str, duration_total: float, segment_duration: float) -> float:
        max_start = max(0.0, duration_total - segment_duration)
        used = self._used_starts.setdefault(source_key, [])
        for _ in range(_MAX_START_ATTEMPTS):
            candidate = random.uniform(0.0, max_start) if max_start > 0 else 0.0
            if all(abs(candidate - u) >= segment_duration for u in used):
                used.append(candidate)
                return candidate
        # fonte curta demais pra tantos reusos sem sobrepor: aceita mesmo
        # assim, degradação graciosa em vez de travar o pipeline
        candidate = random.uniform(0.0, max_start) if max_start > 0 else 0.0
        used.append(candidate)
        return candidate
