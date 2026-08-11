"""
Monta o composition.json final: junta beats + narração + footage + legendas,
valida contra composition.schema.json antes de salvar. Tipo do beat
(concreto/estatístico) e dados de gráfico vêm de modules/keyword_extractor.py
(analyze_beat) — footage é buscado pra todo beat, mesmo estatístico, pra
servir de fundo desfocado atrás do gráfico animado.

Cada beat é fatiado em CENAS (`beat.scenes`): um bloco de narração pode ter
minutos, e um clipe de stock tem ~10-30s. Antes o beat inteiro era uma cena
só, então o clipe acabava e o Remotion congelava no último frame pelo resto
do bloco (chegou a 3min de imagem parada num teste real). Agora o beat é
preenchido por vários shots visualmente distintos que se revezam em cortes
de ~7s, e nenhuma cena é mais longa que o clipe que a preenche.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import jsonschema

from modules.captions import ensure_captions
from modules.config import PROJECT_ROOT, load_config, output_dir
from modules.footage_search import search_and_download_footage
from modules.keyword_extractor import analyze_beat
from modules.narration import build_narration
from modules.script_parser import Beat, parse_script

logger = logging.getLogger(__name__)

SCHEMA_PATH = PROJECT_ROOT / "composition.schema.json"

# (beat_id, stage, status) — stage: narration|keywords|footage|captions,
# status: running|done. Usado pelo painel web para progresso ao vivo; o CLI
# não passa nada e o comportamento de hoje não muda.
OnBeatProgress = Callable[[int, str, str], None]


def _path_str(p) -> str:
    """Caminho relativo à raiz do projeto (POSIX), não absoluto.

    O Remotion não aceita caminhos de arquivo absolutos locais como `src` de
    <Audio>/<OffthreadVideo> — só URLs http(s) ou staticFile(), que resolve
    relativo ao public dir (configurado em remotion.config.ts como a raiz
    deste projeto). Por isso todo caminho no composition.json precisa ser
    relativo à raiz, não absoluto.
    """
    return Path(p).resolve().relative_to(PROJECT_ROOT).as_posix()


# Margem pra sobreposição do crossfade não passar do fim do clipe
# (ver TRANSITION_FRAMES em remotion/src/VideoComposition.tsx).
_TRANSITION_MARGIN_SECONDS = 0.4
# Abaixo disso um corte vira "piscada" — usado pra decidir se vale encolher a
# cena anterior em vez de deixar uma sobra minúscula no fim do beat.
_MIN_SCENE_SECONDS = 1.2
# Clipe sem duração conhecida (fallback local): corta curto por segurança.
_UNKNOWN_CLIP_SECONDS = 4.0
# Em quantos pontos distintos do clipe os reusos podem começar. Só ajuda quando
# o clipe é bem mais longo que a cena — daí min_duration_seconds em config.yaml.
_OFFSET_STEPS = 4


def _max_scene_seconds(footage: dict, scene_seconds: float) -> float:
    """Quanto tempo esse footage aguenta na tela sem congelar no último frame.

    Imagem nunca congela (tem Ken Burns em FootageClip.tsx), então usa o alvo
    cheio. Vídeo é limitado pela própria duração, com margem pro crossfade.
    """
    if footage.get("clip_path") is None or footage.get("media_type") == "image":
        return scene_seconds
    duration = footage.get("duration")
    if not duration:
        return min(scene_seconds, _UNKNOWN_CLIP_SECONDS)
    return max(_MIN_SCENE_SECONDS, min(scene_seconds, duration - _TRANSITION_MARGIN_SECONDS))


def _scene_footage(footage: dict) -> dict | None:
    if not footage.get("clip_path"):
        return None
    return {
        "clip_path": _path_str(footage["clip_path"]),
        "source": footage["source"],
        "media_type": footage["media_type"],
        "search_terms": footage["search_terms"],
    }


def _tile_scenes(
    start_seconds: float,
    end_seconds: float,
    shots: list[dict],
    cfg: dict,
    has_chart: bool,
    chart_at_seconds: float | None = None,
) -> list[dict]:
    """Preenche o intervalo [start, end) com cenas curtas, revezando os shots.

    Nenhuma cena passa da duração do clipe que a preenche — é isso que impede
    o congelamento. Quando o mesmo clipe é reutilizado (beat longo, poucos
    shots), cada reuso começa de um ponto diferente do clipe
    (`clip_start_seconds`) pra não parecer o mesmo trecho em loop.

    `chart_at_seconds` é o instante em que o dado do gráfico é FALADO (ancorado
    nos timestamps por palavra). A cena de gráfico é encaixada ali no meio do
    bloco em vez de sempre no começo — num bloco de 4 minutos, o começo pode
    estar minutos longe de quando o número é dito. Sem âncora, cai no começo.
    """
    scene_seconds = cfg["footage"]["scene_seconds"]
    scenes: list[dict] = []
    cursor = start_seconds

    # Um shot pode ter vindo sem clipe (busca vazia, download falhou e não há
    # nem fallback local). Antes isso virava tela preta pelo tempo daquele
    # shot; melhor reaproveitar os clipes que os outros shots do mesmo beat
    # conseguiram — só fica preto se o beat inteiro ficou sem nada.
    shots = [s for s in shots if s.get("clip_path")]

    if not shots:
        if end_seconds - cursor > 0.01:
            scenes.append(
                {
                    "start_seconds": round(cursor, 3),
                    "end_seconds": round(end_seconds, 3),
                    "kind": "footage",
                    "clip_start_seconds": 0.0,
                    "footage": None,
                }
            )
        return scenes

    chart_seconds = min(cfg["footage"]["chart_scene_seconds"], end_seconds - start_seconds)
    chart_start = None
    if has_chart:
        if chart_at_seconds is None:
            chart_start = start_seconds
        else:
            # não deixa o gráfico vazar do bloco nem sobrar um rabicho curto
            # depois dele
            latest = end_seconds - chart_seconds - _MIN_SCENE_SECONDS
            chart_start = min(max(chart_at_seconds, start_seconds), max(start_seconds, latest))

    reuse_count: dict[str, int] = {}
    index = 0

    def emit_chart() -> None:
        nonlocal cursor, index
        scenes.append(
            {
                "start_seconds": round(cursor, 3),
                "end_seconds": round(cursor + chart_seconds, 3),
                "kind": "chart",
                "clip_start_seconds": 0.0,
                "footage": _scene_footage(shots[index % len(shots)]),
            }
        )
        cursor += chart_seconds
        # avança o rodízio: emendar o mesmo clipe em tela cheia logo depois de
        # aparecer desfocado atrás do gráfico parece repetição
        if len(shots) > 1:
            index += 1

    if chart_start is not None and chart_start <= start_seconds + 0.01:
        emit_chart()
        chart_start = None

    while end_seconds - cursor > 0.01:
        # chegou a hora do gráfico: corta a sequência de footage e encaixa
        if chart_start is not None and cursor >= chart_start - 0.01:
            emit_chart()
            chart_start = None
            continue

        footage = shots[index % len(shots)]
        # com gráfico pendente, esta cena termina exatamente onde ele começa
        limit = chart_start if chart_start is not None else end_seconds
        remaining = limit - cursor
        duration = min(_max_scene_seconds(footage, scene_seconds), remaining)

        # sobra curta demais pro próximo corte: encolhe esta cena pra sobra
        # virar uma cena decente; se não der, absorve a sobra aqui mesmo.
        tail = remaining - duration
        if 0 < tail < _MIN_SCENE_SECONDS:
            shrunk = duration - (_MIN_SCENE_SECONDS - tail)
            duration = shrunk if shrunk >= _MIN_SCENE_SECONDS else remaining

        clip_path = footage.get("clip_path") or ""
        used = reuse_count.get(clip_path, 0)
        # Espalha os reusos em pontos distintos do clipe. Multiplicar pela
        # duração da cena e tirar o módulo da folga não funcionava: com um
        # clipe de 8s e cena de 7s a folga é 1s, e 7 % 1 dá sempre 0 — todos
        # os reusos começavam do mesmo lugar.
        slack = (footage.get("duration") or 0) - duration
        clip_start = (
            round(slack * ((used % _OFFSET_STEPS) / _OFFSET_STEPS), 3) if slack > 0.5 else 0.0
        )
        reuse_count[clip_path] = used + 1

        scenes.append(
            {
                "start_seconds": round(cursor, 3),
                "end_seconds": round(cursor + duration, 3),
                "kind": "footage",
                "clip_start_seconds": clip_start,
                "footage": _scene_footage(footage),
            }
        )
        cursor += duration
        index += 1

    return scenes


_WORD_NOISE = str.maketrans("", "", ".,;:!?\"'()[]—–")


def _normalize_word(word: str) -> str:
    return word.lower().translate(_WORD_NOISE).strip()


def _find_spoken_at(trigger: object, captions: list[dict]) -> float | None:
    """Segundo em que `trigger` (recorte literal da narração) começa a ser dito.

    None quando o trigger não bate palavra a palavra — quem chama decide o
    fallback em vez de posicionar no lugar errado.
    """
    if not isinstance(trigger, str) or not trigger.strip():
        return None
    needle = [w for w in (_normalize_word(w) for w in trigger.split()) if w]
    if not needle:
        return None
    words = [_normalize_word(c["word"]) for c in captions]
    for i in range(len(words) - len(needle) + 1):
        if words[i : i + len(needle)] == needle:
            return captions[i]["start_seconds"]
    return None


def _anchor_highlights(
    highlights: list[dict], captions: list[dict], chart_ranges: list[tuple[float, float]], cfg: dict
) -> list[dict]:
    """Posiciona cada destaque no segundo em que a informação é FALADA.

    O `trigger` que o LLM devolveu é um recorte literal da narração; aqui ele
    é casado com a sequência de palavras do beat (que já vem com timestamp por
    palavra da Cartesia, na timeline global) pra achar o instante exato. Sem
    isso o selo apareceria no início do bloco — que pode estar minutos longe do
    momento em que o dado é dito.

    Descarta destaque cujo trigger não bate, que colidiria com o anterior, ou
    que cairia em cima de uma cena de gráfico (informação duplicada na tela).
    """
    hl_cfg = cfg["highlights"]
    duration = hl_cfg["duration_seconds"]
    min_gap = hl_cfg["min_gap_seconds"]

    anchored: list[dict] = []
    for highlight in highlights:
        start_seconds = _find_spoken_at(highlight["trigger"], captions)
        if start_seconds is None:
            logger.info("Destaque sem âncora nos timestamps: %r", highlight["trigger"])
            continue

        end_seconds = start_seconds + duration
        if any(start_seconds < c_end and end_seconds > c_start for c_start, c_end in chart_ranges):
            continue

        payload = {k: v for k, v in highlight.items() if k != "trigger"}
        anchored.append(
            {**payload, "start_seconds": round(start_seconds, 3), "end_seconds": round(end_seconds, 3)}
        )

    anchored.sort(key=lambda h: h["start_seconds"])
    spaced: list[dict] = []
    for highlight in anchored:
        if spaced and highlight["start_seconds"] < spaced[-1]["end_seconds"] + min_gap:
            continue
        spaced.append(highlight)
    return spaced


def validate_composition(data: dict) -> None:
    """Valida um composition.json (ou dict equivalente) contra o schema.
    Levanta jsonschema.ValidationError se algo estiver fora do formato."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


def _assemble_composition(
    beats: list[Beat],
    slug: str,
    on_beat_progress: OnBeatProgress | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
) -> dict:
    """Monta o composition.json a partir de uma lista de beats já pronta —
    tanto faz se vieram do parsing de um arquivo de roteiro (CLI) ou já
    chegaram prontos (painel web, onde o usuário monta os blocos um a um).
    """
    cfg = load_config()

    on_narration_beat_done = None
    if on_beat_progress is not None:
        on_narration_beat_done = lambda beat_id: on_beat_progress(beat_id, "narration", "done")
    narration = build_narration(
        beats,
        slug,
        on_beat_done=on_narration_beat_done,
        voice_id=voice_id,
        language=language,
        speed=speed,
    )

    beats_by_id = {b["id"]: b for b in narration["beats"]}
    footage_cfg = cfg["footage"]

    composition_beats = []
    for position, beat in enumerate(beats):
        beat_timing = beats_by_id[beat.id]

        # A cena vai até o começo do PRÓXIMO beat (não até o fim da fala deste)
        # pra absorver o silêncio entre beats — sem isso sobrava um buraco
        # preto entre blocos e a transição não teria onde sobrepor.
        next_beat = beats[position + 1] if position + 1 < len(beats) else None
        visual_end = (
            beats_by_id[next_beat.id]["start_seconds"]
            if next_beat
            else narration["duration_seconds"]
        )
        visual_start = beat_timing["start_seconds"]
        visual_duration = max(0.1, visual_end - visual_start)

        # quanto mais longo o bloco, mais ideias visuais distintas ele precisa
        n_shots = max(
            1,
            min(
                footage_cfg["max_shots_per_beat"],
                round(visual_duration / footage_cfg["seconds_per_shot"]),
            ),
        )

        n_highlights = min(
            cfg["highlights"]["max_per_beat"],
            round(visual_duration / cfg["highlights"]["seconds_per_highlight"]),
        )

        if on_beat_progress is not None:
            on_beat_progress(beat.id, "keywords", "running")
        analysis = analyze_beat(
            beat,
            slug,
            n_shots=n_shots,
            language=language or cfg["narration"]["language"],
            n_highlights=n_highlights,
        )
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "keywords", "done")
            on_beat_progress(beat.id, "footage", "running")

        # footage é buscado pra todo beat, mesmo "estatistico" — o primeiro
        # shot também vira o fundo desfocado atrás do gráfico (AnimatedChart)
        shots = [
            search_and_download_footage(beat.id, beat.text, shot["terms"], slug=slug, slot=slot)
            for slot, shot in enumerate(analysis["shots"])
        ]
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "footage", "done")
            on_beat_progress(beat.id, "captions", "running")
        captions = ensure_captions(beat_timing, slug)
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "captions", "done")

        has_chart = analysis["type"] == "estatistico" and analysis["chart"] is not None
        chart_at = (
            _find_spoken_at((analysis["chart"] or {}).get("trigger"), captions)
            if has_chart
            else None
        )
        scenes = _tile_scenes(visual_start, visual_end, shots, cfg, has_chart, chart_at)
        chart_ranges = [
            (s["start_seconds"], s["end_seconds"]) for s in scenes if s["kind"] == "chart"
        ]
        composition_beats.append(
            {
                "id": beat.id,
                "text": beat.text,
                "start_seconds": beat_timing["start_seconds"],
                "end_seconds": beat_timing["end_seconds"],
                "type": analysis["type"],
                "chart": analysis["chart"],
                "scenes": scenes,
                "highlights": _anchor_highlights(
                    analysis.get("highlights", []), captions, chart_ranges, cfg
                ),
                "captions": captions,
            }
        )

    composition = {
        "fps": cfg["video"]["fps"],
        "width": cfg["video"]["width"],
        "height": cfg["video"]["height"],
        "audio": {
            "path": _path_str(narration["audio_path"]),
            "duration_seconds": narration["duration_seconds"],
        },
        "music": None,
        "beats": composition_beats,
    }

    validate_composition(composition)

    out_path = output_dir(slug) / "composition.json"
    out_path.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("composition.json salvo em %s", out_path)

    return composition


def build_composition(
    script_path: str,
    slug: str | None = None,
    on_beat_progress: OnBeatProgress | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
) -> dict:
    """Usada pelo CLI (pipeline.py): lê e divide um arquivo de roteiro."""
    script_path = Path(script_path)
    slug = slug or script_path.stem
    beats = parse_script(script_path)
    return _assemble_composition(beats, slug, on_beat_progress, voice_id, language, speed)


def build_composition_from_beats(
    beats: list[Beat],
    slug: str,
    on_beat_progress: OnBeatProgress | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
) -> dict:
    """Usada pelo painel web: os beats já vêm prontos (o usuário monta o
    roteiro bloco a bloco na interface), sem precisar de um arquivo no disco."""
    return _assemble_composition(beats, slug, on_beat_progress, voice_id, language, speed)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m modules.composition_builder <caminho-do-roteiro>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    build_composition(sys.argv[1])
