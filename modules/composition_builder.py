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
import random
from pathlib import Path
from typing import Callable

import jsonschema
from PIL import Image

from modules.captions import ensure_captions
from modules.config import PROJECT_ROOT, cache_dir, load_config, output_dir
from modules.footage_search import search_and_download_footage
from modules.image_effects import get_blurred_background
from modules.keyword_extractor import STRATEGIES_THAT_SEARCH, analyze_beat
from modules.media_pool import PoolDistributor
from modules.narration import build_narration
from modules.script_parser import Beat, parse_script
from modules.timeline import DEFAULT_EFFECT, GALLERY_EFFECTS, load_manifest

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
# Repetir o mesmo shot (vídeo, imagem ou card) mais de 2 vezes no mesmo beat
# fica cansativo de assistir — pedido explícito do usuário. Só ultrapassa
# quando TODOS os shots do beat já bateram o teto (beat longo demais pro
# roteiro visual cobrir sem repetir de novo); melhor um 3º reuso do que um
# buraco na timeline.
_MAX_SHOT_REUSES = 2


def _reuse_cap(shot: dict) -> int:
    """Quantas vezes este shot pode reaparecer no mesmo beat.

    Card conceitual puro (sem clip_path, sem motion_graphic, sem
    gallery_items) não tem como variar entre reaparições — reaparecer é
    mostrar a MESMA frase de novo. Footage/motion_graphic/galeria TÊM como
    variar (novo clip_start, novo item reaproveitado) e mantêm o teto
    normal. Cuidado: um shot de galeria pode não ter clip_path no nível
    raiz (a mídia vive em gallery_items) — checar só clip_path erraria a
    galeria pro cap 1.
    """
    has_variation = bool(shot.get("clip_path")) or bool(shot.get("motion_graphic")) or bool(shot.get("gallery_items"))
    return _MAX_SHOT_REUSES if has_variation else 1


# Texto do botão da SubscribeBar (Inscrever-se/Inscrito), traduzido pro
# idioma da narração — o componente original tinha italiano fixo
# ("Iscriviti"/"Iscritto"), errado pra uma ferramenta multi-idioma. Mesmas
# 7 chaves de _LANGUAGE_NAMES em keyword_extractor.py.
_SUBSCRIBE_LABELS = {
    "pt": ("Inscrever-se", "Inscrito"),
    "en": ("Subscribe", "Subscribed"),
    "es": ("Suscríbete", "Suscrito"),
    "fr": ("S'abonner", "Abonné"),
    "de": ("Abonnieren", "Abonniert"),
    "it": ("Iscriviti", "Iscritto"),
    "ja": ("チャンネル登録", "登録済み"),
}


class _EffectPicker:
    """Decide transição de corte (Whip Pan/Film Burn/fade) e estilo de
    exibição de shot único (Parallax Pan) — Fase 5, ver remotion/src/
    VideoComposition.tsx. Estado de UMA instância por vídeo INTEIRO (não por
    beat), instanciada em _assemble_composition: transição não pode repetir
    o mesmo tipo (fora fade) duas vezes seguidas, e isso só é possível de
    garantir vendo os cortes do vídeo inteiro, não beat a beat.

    Decisão ALGORÍTMICA, não da IA: analyze_beat roda isolado por beat,
    sem ver o resto do vídeo, então não tem como saber que efeito já foi
    usado antes nem bater uma frequência global.
    """

    def __init__(self, cfg: dict):
        effects_cfg = cfg.get("effects") or {}
        weights = effects_cfg.get("transition_weights") or {"fade": 1.0}
        self._transition_options = list(weights.keys())
        self._transition_weights = list(weights.values())
        self._parallax_chance = effects_cfg.get("parallax_pan_chance", 0.0)
        self._last_transition: str | None = None
        self._masonry_style_weights = effects_cfg.get("masonry_style_weights") or {"clean": 1.0}
        self._grid_style_weights = effects_cfg.get("grid_style_weights") or {"grid": 1.0}

    def pick_transition(self) -> str:
        choice = random.choices(self._transition_options, weights=self._transition_weights, k=1)[0]
        if choice != "fade" and choice == self._last_transition:
            # nunca repete o mesmo tipo NÃO-FADE duas vezes seguidas —
            # re-sorteia só entre as opções restantes
            remaining_options = [o for o in self._transition_options if o != choice]
            remaining_weights = [w for o, w in zip(self._transition_options, self._transition_weights) if o != choice]
            if remaining_options:
                choice = random.choices(remaining_options, weights=remaining_weights, k=1)[0]
        self._last_transition = choice
        return choice

    def maybe_parallax(self) -> bool:
        return random.random() < self._parallax_chance

    def pick_gallery_style(self, effect: str | None, item_count: int) -> str | None:
        """Variante visual DENTRO do effect de galeria — ver config.yaml
        (effects.masonry_style_weights/grid_style_weights)."""
        if effect == "masonry":
            options = list(self._masonry_style_weights)
            weights = list(self._masonry_style_weights.values())
            return random.choices(options, weights=weights, k=1)[0]
        if effect == "gallery_grid":
            if item_count < 3 or item_count > 5:
                return "grid"
            options = list(self._grid_style_weights)
            weights = list(self._grid_style_weights.values())
            return random.choices(options, weights=weights, k=1)[0]
        return None


def _max_scene_seconds(footage: dict, scene_seconds: float, chart_scene_seconds: float) -> float:
    """Quanto tempo esse footage/gráfico aguenta na tela sem congelar no
    último frame.

    Motion graphic (Fase 4) é conteúdo renderizado, não mídia — usa o mesmo
    tempo de leitura do <AnimatedChart> (chart_scene_seconds), maior que o
    corte padrão, porque tem texto pra ler (data, citação, lista), não só
    imagem pra olhar. Imagem de footage nunca congela (tem Ken Burns em
    FootageClip.tsx), então usa o alvo cheio. Vídeo é limitado pela própria
    duração, com margem pro crossfade.
    """
    if footage.get("motion_graphic"):
        return chart_scene_seconds
    if footage.get("gallery_items"):
        # Galeria (Fase 5): limitada pelo item de VÍDEO mais curto entre os
        # itens, se houver algum — mesmo raciocínio do vídeo normal abaixo,
        # só que aplicado a cada item em vez de um clipe só. Sem vídeo na
        # galeria (o caso comum — PoolDistributor prioriza foto), usa o
        # alvo cheio como imagem já usa.
        video_durations = [
            item.get("duration")
            for item in footage["gallery_items"]
            if item.get("media_type") == "video" and item.get("duration")
        ]
        if not video_durations:
            return scene_seconds
        shortest = min(video_durations)
        return max(_MIN_SCENE_SECONDS, min(scene_seconds, shortest - _TRANSITION_MARGIN_SECONDS))
    if footage.get("clip_path") is None or footage.get("media_type") == "image":
        return scene_seconds
    duration = footage.get("duration")
    if not duration:
        return min(scene_seconds, _UNKNOWN_CLIP_SECONDS)
    return max(_MIN_SCENE_SECONDS, min(scene_seconds, duration - _TRANSITION_MARGIN_SECONDS))


def _image_dimensions(clip_path: str) -> tuple[int, int] | None:
    """Lê só o header da imagem (não decodifica pixels) pra saber a
    proporção real — FootageClip.tsx precisa disso pra montar o card do
    tamanho certo (ver comentário lá: sem isso, uma foto pequena renderiza
    no tamanho intrínseco dela, minúscula numa tela 1920x1080)."""
    try:
        with Image.open(PROJECT_ROOT / clip_path) as img:
            return img.size
    except Exception:
        logger.warning("Não consegui ler dimensão de %s, card cai no fallback antigo", clip_path, exc_info=True)
        return None


def _scene_footage(footage: dict, cfg: dict) -> dict | None:
    if not footage.get("clip_path"):
        return None
    scene_footage = {
        "clip_path": _path_str(footage["clip_path"]),
        "source": footage["source"],
        "media_type": footage["media_type"],
        "search_terms": footage["search_terms"],
        "relevance_score": footage.get("relevance_score"),
        "ai_reasoning": footage.get("ai_reasoning", ""),
    }
    if footage.get("attribution"):
        scene_footage["attribution"] = footage["attribution"]
    if footage.get("render_style"):
        scene_footage["render_style"] = footage["render_style"]
    if footage["media_type"] == "image":
        # Pré-computado uma vez aqui (build da composição), não a cada
        # frame no Chromium — ver modules/image_effects.py pro porquê.
        scene_footage["blurred_background_path"] = get_blurred_background(
            scene_footage["clip_path"], cfg["video"]["width"], cfg["video"]["height"]
        )
        dims = _image_dimensions(scene_footage["clip_path"])
        if dims:
            scene_footage["width"], scene_footage["height"] = dims
    return scene_footage


def _scene_gallery(gallery: dict, cfg: dict) -> dict | None:
    """Monta o campo scene.gallery (Fase 5: Split Screen/Comparison Slider/
    Gallery Grid/Masonry) — reaproveita _scene_footage por item (mesmo
    formato), só descarta os campos que não fazem sentido numa colagem
    (relevance_score/ai_reasoning: nota individual não existe aqui;
    render_style: Parallax Pan não se aplica a um item de galeria)."""
    items = []
    for item in gallery.get("items") or []:
        scene_item = _scene_footage(item, cfg)
        if scene_item is None:
            continue
        scene_item.pop("relevance_score", None)
        scene_item.pop("ai_reasoning", None)
        scene_item.pop("render_style", None)
        items.append(scene_item)
    if len(items) < 2:
        return None
    result = {"effect": gallery["effect"], "items": items[:6]}
    if gallery.get("style"):
        result["style"] = gallery["style"]
    return result


def _manual_media_footage(item: dict, slug: str) -> dict:
    clip_path = cache_dir("own_media", slug) / item["pool_filename"]
    return {
        "clip_path": str(clip_path),
        "source": "manual",
        "media_type": item["media_type"],
        "search_terms": [],
    }


def _scenes_from_manifest(
    manifest: list[dict],
    slug: str,
    beat_start_seconds: float,
    cfg: dict,
    effect_picker: "_EffectPicker | None",
) -> list[dict]:
    """1 cena por trecho do editor de timeline manual (modules/timeline.py,
    modo de mídia própria) — sem shot-planning por IA nem PoolDistributor:
    o usuário já decidiu exatamente qual mídia (ou mídias, se escolheu um
    efeito de galeria — ver timeline.EFFECT_CATALOG) e qual EFEITO vai em
    cada trecho de ~4s, então não há reuso pra variar nem threshold de
    relevância pra aplicar (webapp/server.py::create_job já bloqueia a
    criação do job até todo trecho ter mídia suficiente pro efeito
    escolhido — os fallbacks abaixo são só defensivos).

    `manifest[i]["start_seconds"]`/`["end_seconds"]` são relativos ao
    início do BLOCO (mesmo referencial de `captions`, ver
    timeline.chunk_captions) — offset pra timeline global aqui, mesmo
    padrão que build_narration já aplica aos beats inteiros.
    """
    scenes = []
    for slot in manifest:
        effect = slot.get("effect") or DEFAULT_EFFECT
        media_items = [m for m in (slot.get("media") or []) if m]
        start_seconds = round(beat_start_seconds + slot["start_seconds"], 3)
        end_seconds = round(beat_start_seconds + slot["end_seconds"], 3)
        transition_in = effect_picker.pick_transition() if effect_picker else "fade"

        if effect in GALLERY_EFFECTS and len(media_items) >= 2:
            gallery_scene = _scene_gallery(
                {
                    "effect": effect,
                    "items": [_manual_media_footage(m, slug) for m in media_items],
                    "style": None,
                },
                cfg,
            )
            if gallery_scene is not None:
                scenes.append(
                    {
                        "start_seconds": start_seconds,
                        "end_seconds": end_seconds,
                        "kind": "gallery",
                        "clip_start_seconds": 0.0,
                        "footage": None,
                        "gallery": gallery_scene,
                        "shot_slot": slot["index"],
                        "transition_in": transition_in,
                    }
                )
                continue

        # padrão/parallax_pan, ou galeria pedida sem mídia suficiente ainda
        # (defensivo — create_job já bloqueia isso antes de chegar aqui):
        # cai pro primeiro item anexado como plano único.
        media = media_items[0] if media_items else None
        scene_footage = None
        if media:
            footage = _manual_media_footage(media, slug)
            if effect == "parallax_pan":
                footage["render_style"] = "parallax_pan"
            scene_footage = _scene_footage(footage, cfg)
        scenes.append(
            {
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "kind": "footage",
                "clip_start_seconds": (media or {}).get("clip_start_seconds") or 0.0,
                "footage": scene_footage,
                "gallery": None,
                "shot_slot": slot["index"],
                "transition_in": transition_in,
            }
        )
    return scenes


def _tile_scenes(
    start_seconds: float,
    end_seconds: float,
    shots: list[dict],
    cfg: dict,
    has_chart: bool,
    chart_at_seconds: float | None = None,
    on_reuse: Callable[[dict], dict] | None = None,
    effect_picker: "_EffectPicker | None" = None,
    on_gallery_reuse: Callable[[dict], dict] | None = None,
) -> list[dict]:
    """Preenche o intervalo [start, end) com cenas curtas, revezando os shots.

    Nenhuma cena passa da duração do clipe que a preenche — é isso que impede
    o congelamento. Quando o mesmo clipe é reutilizado (beat longo, poucos
    shots), cada reuso começa de um ponto diferente do clipe
    (`clip_start_seconds`) pra não parecer o mesmo trecho em loop.

    `on_reuse`: só usado no modo de mídia própria (PoolDistributor.reuse_media).
    Um beat mais longo que a quantidade de shots distintos gerados pra ele
    (comum: pool com dezenas de fotos, mas só 2-3 shots por beat) fazia a
    MESMA foto/trecho de vídeo repetir várias vezes seguidas dentro do beat,
    ignorando o resto do pool. `on_reuse` troca por mídia nova a cada
    reaparição: foto pega a PRÓXIMA do pool (rodízio global, nunca a mesma
    de novo enquanto sobrar opção); vídeo corta um trecho aleatório novo da
    mesma fonte (já chega pré-cortado no tamanho exato do shot, sem folga
    pra deslocar `clip_start_seconds` como o resto da lógica abaixo faz).

    `on_gallery_reuse` (PoolDistributor.reuse_gallery_item): mesmo
    problema, mas pra shot de galeria (Fase 5, 2-6 itens) — callback
    dedicado, não `on_reuse`, porque o de shot único usa o cursor
    PRINCIPAL de foto/vídeo do PoolDistributor, e um item de galeria
    precisa do cursor PRÓPRIO de galeria (`next_gallery_items`), senão
    desalinha o ritmo foto/foto/vídeo do resto do vídeo.

    `chart_at_seconds` é o instante em que o dado do gráfico é FALADO (ancorado
    nos timestamps por palavra). A cena de gráfico é encaixada ali no meio do
    bloco em vez de sempre no começo — num bloco de 4 minutos, o começo pode
    estar minutos longe de quando o número é dito. Sem âncora, cai no começo.
    """
    scene_seconds = cfg["footage"]["scene_seconds"]
    chart_scene_seconds = cfg["footage"]["chart_scene_seconds"]
    min_gallery_scene_seconds = cfg["footage"].get("min_gallery_scene_seconds", 4.5)
    scenes: list[dict] = []
    cursor = start_seconds

    def append_scene(cena: dict) -> None:
        # transition_in é algorítmico (Fase 5) e vale pra TODA cena — o
        # primeiro corte do vídeo inteiro nunca usa isso de verdade
        # (VideoComposition.tsx só desenha <Transition> a partir do índice
        # 1), então atribuir sem exceção aqui não tem efeito nenhum nele.
        cena["transition_in"] = effect_picker.pick_transition() if effect_picker else "fade"
        scenes.append(cena)

    # Threshold: mídia que só lembra o assunto é pior que assumir que não
    # achamos nada. Abaixo do mínimo, o shot perde o clipe e vira card
    # conceitual — é o "não forçar footage" do plano. Motion graphic (Fase 4)
    # nunca passa por aqui: não tem clip_path pra avaliar (nunca buscou mídia),
    # é conteúdo estruturado já validado no keyword_extractor.
    minimo = cfg["ranking"]["alternative_threshold"]
    usaveis, motion_graphics, descartados = [], [], []
    for shot in shots:
        if shot.get("motion_graphic"):
            motion_graphics.append(shot)
            continue
        score = shot.get("relevance_score")
        is_gallery = len(shot.get("gallery_items") or []) >= 2
        if (shot.get("clip_path") or is_gallery) and (score is None or score >= minimo):
            usaveis.append(shot)
            logger.info(
                "Shot aceito (nota %s, fonte %s, identity_status %s, estratégia %s, galeria %s)",
                score,
                shot.get("source"),
                shot.get("identity_status"),
                shot.get("strategy"),
                shot.get("gallery_effect") if is_gallery else None,
            )
        elif shot.get("concept_text"):
            # tira o clip_path: sem isso o tiling ainda enxerga mídia e emite
            # a cena como "footage", deixando o threshold puramente decorativo
            descartados.append({**shot, "clip_path": None})
            logger.info(
                "Shot vira card conceitual (nota %s, fonte %s, identity_status %s, estratégia %s): %r",
                score,
                shot.get("source"),
                shot.get("identity_status"),
                shot.get("strategy"),
                shot["concept_text"],
            )

    # Cards e motion graphics entram no rodízio junto com os clipes, na ordem
    # original — assim um bloco sem mídia boa não vira uma sequência de cards
    # seguidos.
    shots = usaveis + motion_graphics + descartados if (usaveis or motion_graphics) else descartados
    shots = shots or []

    if not shots:
        if end_seconds - cursor > 0.01:
            append_scene(
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
    # Quantas vezes cada SHOT (não clipe — vale igual pra vídeo, imagem e card
    # conceitual/motion graphic) já apareceu neste beat. Separado do
    # `reuse_count` acima: aquele decide DE ONDE no clipe reaproveitado
    # começar (Fase 3); este decide SE aquele shot pode aparecer de novo.
    shot_reuse_count: dict[int, int] = {}
    index = 0
    # Cursor PRÓPRIO do gráfico sobre com_midia (lista menor que `shots`,
    # que `index` acima indexa) — bug real pego testando: os dois
    # compartilhavam `index`, e como com_midia costuma ser bem menor,
    # dava volta (wraparound) cedo demais, reaproveitando uma foto que
    # tinha acabado de aparecer em tela cheia segundos antes.
    chart_index = 0

    def emit_chart() -> None:
        nonlocal cursor, index, chart_index
        # Recalculado aqui (não antes do loop): se on_reuse já trocou a
        # foto de algum shot antes do gráfico aparecer no meio do beat,
        # isso pega a troca — um snapshot tirado antes do loop não veria.
        com_midia = [s for s in shots if s.get("clip_path")]
        fundo = com_midia[chart_index % len(com_midia)] if com_midia else None
        # Evita bater com o clipe da cena IMEDIATAMENTE anterior — achado
        # testando: com poucos shots, o rodízio do loop principal pode
        # coincidentemente já estar no mesmo shot que com_midia[chart_index]
        # aponta, bem antes do gráfico disparar (não é o bug do índice
        # compartilhado, é uma colisão diferente, mesma família).
        if fundo and len(com_midia) > 1 and scenes:
            anterior = (scenes[-1].get("footage") or {}).get("clip_path")
            if anterior and anterior == fundo.get("clip_path"):
                fundo = com_midia[(chart_index + 1) % len(com_midia)]
        append_scene(
            {
                "start_seconds": round(cursor, 3),
                "end_seconds": round(cursor + chart_seconds, 3),
                "kind": "chart",
                "clip_start_seconds": 0.0,
                "footage": _scene_footage(fundo, cfg) if fundo else None,
            }
        )
        cursor += chart_seconds
        if len(com_midia) > 1:
            chart_index += 1
        # avança o rodízio do loop PRINCIPAL (não o do gráfico): emendar o
        # mesmo shot em tela cheia logo depois de aparecer desfocado atrás
        # do gráfico ainda parece repetição — essa proteção já funcionava
        # e continua igual, só o cursor do próprio gráfico é que era o bug.
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

        # Prefere um shot que ainda não bateu o teto de repetições.
        under_cap = [i for i in range(len(shots)) if shot_reuse_count.get(i, 0) < _reuse_cap(shots[i])]
        if under_cap:
            pool = under_cap
        else:
            # Todos bateram o teto (beat mais longo que a variedade de
            # shots aguenta sem repetir). Prefere qualquer shot que AINDA
            # varia entre aparições (footage/motion_graphic/galeria — um
            # reuso a mais muda o clip_start ou o item, então não é
            # idêntico) — só cai pra reaproveitar um card conceitual puro
            # (_reuse_cap == 1, nunca varia, seria a MESMA frase de novo)
            # se não sobrar NENHUM shot com variação — beat 100%
            # conceitual, sem outro conteúdo pra mostrar mesmo.
            com_variacao = [i for i in range(len(shots)) if _reuse_cap(shots[i]) > 1]
            pool = com_variacao or list(range(len(shots)))
        shot_i = pool[index % len(pool)]
        footage = shots[shot_i]
        shot_reuse_count[shot_i] = shot_reuse_count.get(shot_i, 0) + 1
        index += 1

        # com gráfico pendente, esta cena termina exatamente onde ele começa
        limit = chart_start if chart_start is not None else end_seconds
        remaining = limit - cursor
        duration = min(_max_scene_seconds(footage, scene_seconds, chart_scene_seconds), remaining)

        # sobra curta demais pro próximo corte: encolhe esta cena pra sobra
        # virar uma cena decente; se não der, absorve a sobra aqui mesmo.
        tail = remaining - duration
        if 0 < tail < _MIN_SCENE_SECONDS:
            shrunk = duration - (_MIN_SCENE_SECONDS - tail)
            duration = shrunk if shrunk >= _MIN_SCENE_SECONDS else remaining

        if footage.get("motion_graphic"):
            # Fase 4: conteúdo renderizado, não mídia — sem clip_path, sem
            # reuso a espalhar (repetir o mesmo card duas vezes não ganha nada
            # como o footage ganha ao mudar o clip_start; num beat muito longo
            # com poucos shots ele pode aparecer de novo, limitação aceita
            # por ora).
            append_scene(
                {
                    "start_seconds": round(cursor, 3),
                    "end_seconds": round(cursor + duration, 3),
                    "kind": "motion_graphic",
                    "clip_start_seconds": 0.0,
                    "visual_strategy": "MOTION_GRAPHIC",
                    "footage": None,
                    "motion_graphic": footage["motion_graphic"],
                    "shot_slot": footage.get("slot"),
                }
            )
            cursor += duration
            continue

        gallery_items = footage.get("gallery_items")
        if gallery_items:
            if on_gallery_reuse and shot_reuse_count[shot_i] > 1:
                # mesma ideia do reuso de mídia própria abaixo, item por
                # item — em busca por IA (on_gallery_reuse=None aqui) a
                # colagem se repete como está: rebuscar N termos + rankear
                # de novo só porque o beat esgotou ideias de shot custaria
                # caro à toa (mesmo princípio que motion_graphic já usa
                # acima). Callback DEDICADO (não on_reuse, feito pra shot
                # único) — bug real pego testando: usar on_reuse aqui lia/
                # avançava o cursor PRINCIPAL de foto do PoolDistributor,
                # não o de galeria, desalinhando o ritmo do resto do vídeo.
                gallery_items = [on_gallery_reuse(item) for item in gallery_items]
                footage = {**footage, "gallery_items": gallery_items}
                shots[shot_i] = footage

            if effect_picker and "gallery_style" not in footage:
                # decidido uma vez por SHOT (não por cena) e cacheado no dict
                # — mesmo padrão de render_style: um shot reaproveitado várias
                # vezes no beat mantém o mesmo estilo em todas as aparições.
                footage["gallery_style"] = effect_picker.pick_gallery_style(
                    footage.get("gallery_effect"), len(gallery_items)
                )
                shots[shot_i] = footage

            if duration >= min_gallery_scene_seconds:
                gallery_scene = _scene_gallery(
                    {"effect": footage.get("gallery_effect"), "items": gallery_items, "style": footage.get("gallery_style")},
                    cfg,
                )
                if gallery_scene is not None:
                    append_scene(
                        {
                            "start_seconds": round(cursor, 3),
                            "end_seconds": round(cursor + duration, 3),
                            "kind": "gallery",
                            "clip_start_seconds": 0.0,
                            "visual_strategy": footage.get("strategy", "FOOTAGE"),
                            "footage": None,
                            "gallery": gallery_scene,
                            "shot_slot": footage.get("slot"),
                        }
                    )
                    cursor += duration
                    continue
            # corte curto demais pra uma galeria (timing de entrada dela
            # assume ~scene_seconds) ou itens insuficientes de verdade
            # (download falhou depois da checagem inicial) — cai pro 1º
            # item como footage normal, evita perder a cena inteira
            footage = gallery_items[0] if gallery_items else footage

        if (
            on_reuse
            and footage.get("source") == "manual"
            and reuse_count.get(footage.get("clip_path") or "", 0) > 0
        ):
            # shot do pool próprio reaparecendo dentro do beat (beat pede
            # mais cortes de tela do que shots distintos existem): sem isso,
            # a MESMA foto/trecho de vídeo tocava de novo à toa mesmo com
            # dezenas de outros itens no pool esperando a vez. Vídeo já
            # chega pré-cortado no tamanho exato (sem folga pra deslocar
            # clip_start como o resto da lógica abaixo faz), então pede um
            # trecho novo da mesma fonte; foto pede a PRÓXIMA do pool.
            footage = on_reuse(footage)
            shots[shot_i] = footage

        clip_path = footage.get("clip_path") or ""
        used = reuse_count.get(clip_path, 0)
        clip_duration = footage.get("duration") or 0
        good_ranges = footage.get("good_ranges")

        if good_ranges:
            # Fase 3: a IA já viu frames reais do clipe e recomendou onde tem
            # conteúdo útil (evita cair numa transição/tela preta). Revezo
            # entre os intervalos recomendados a cada reuso, do melhor pro
            # pior; se a cena não cabe inteira dentro do intervalo, encosto
            # no início dele mas nunca deixo passar do fim do clipe.
            inicio_sugerido, _ = good_ranges[used % len(good_ranges)]
            clip_start = round(min(max(inicio_sugerido, 0.0), max(0.0, clip_duration - duration)), 3)
        else:
            # Sem recomendação (clipe curto, sem chave de API, análise
            # falhou): espalha os reusos em pontos distintos do clipe.
            # Multiplicar pela duração da cena e tirar o módulo da folga não
            # funcionava: com um clipe de 8s e cena de 7s a folga é 1s, e
            # 7 % 1 dá sempre 0 — todos os reusos começavam do mesmo lugar.
            slack = clip_duration - duration
            clip_start = (
                round(slack * ((used % _OFFSET_STEPS) / _OFFSET_STEPS), 3) if slack > 0.5 else 0.0
            )
        reuse_count[clip_path] = used + 1

        if clip_path and effect_picker and "render_style" not in footage:
            # decidido uma vez por SHOT (não por cena) e cacheado no dict —
            # um shot reaproveitado várias vezes no beat mantém o mesmo
            # estilo em todas as aparições, não fica alternando.
            footage["render_style"] = "parallax_pan" if effect_picker.maybe_parallax() else "default"
            shots[shot_i] = footage

        cena = {
            "start_seconds": round(cursor, 3),
            "end_seconds": round(cursor + duration, 3),
            "kind": "footage" if clip_path else "concept",
            "clip_start_seconds": clip_start,
            "visual_strategy": footage.get("strategy", "FOOTAGE"),
            "footage": _scene_footage(footage, cfg),
            "shot_slot": footage.get("slot"),
        }
        if not clip_path:
            # sem mídia usável: card com a frase-chave, em vez de tela preta
            # ou de um clipe genérico que não representa o trecho
            cena["concept_text"] = footage.get("concept_text", "")
        append_scene(cena)
        cursor += duration

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


def _write_credits(composition: dict, slug: str) -> None:
    """Grava output/<slug>/creditos.txt com a atribuição do que exige crédito.

    Wikimedia Commons é CC BY / CC BY-SA na maioria: creditar é obrigação da
    licença, não cortesia. Sai um arquivo pronto pra colar na descrição do
    vídeo, com uma linha por mídia e sem repetir a mesma foto usada em várias
    cenas. Se nada no vídeo exigir crédito, o arquivo não é criado.
    """
    linhas: dict[str, str] = {}
    for beat in composition["beats"]:
        for scene in beat["scenes"]:
            footage = scene.get("footage") or {}
            attribution = footage.get("attribution")
            if not attribution:
                continue
            titulo = (attribution.get("title") or "").removeprefix("File:")
            linhas[footage["clip_path"]] = (
                f"- {titulo} — {attribution.get('author', 'desconhecido')} "
                f"({attribution.get('license', 'licença desconhecida')})\n"
                f"  {attribution.get('page', '')}"
            )

    path = output_dir(slug) / "creditos.txt"
    if not linhas:
        path.unlink(missing_ok=True)
        return

    path.write_text(
        "Créditos de imagem\n"
        "Estas mídias exigem atribuição pela licença. Cole na descrição do vídeo.\n\n"
        + "\n".join(linhas.values())
        + "\n",
        encoding="utf-8",
    )
    logger.info("creditos.txt gravado com %d atribuição(ões)", len(linhas))


def validate_composition(data: dict) -> None:
    """Valida um composition.json (ou dict equivalente) contra o schema.
    Levanta jsonschema.ValidationError se algo estiver fora do formato."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


def _fetch_gallery_items(
    beat_id: int,
    beat_text: str,
    shot: dict,
    gallery_items_spec: list[dict],
    analysis: dict,
    distributor,
    allowed_sources: list[str] | None,
    google_images_recency: str | None,
) -> list[dict]:
    """Busca as 2-6 mídias de um shot de galeria (Split Screen/Comparison
    Slider/Gallery Grid/Masonry, Fase 5). Modo mídia própria: pega N itens
    do pool (PoolDistributor.next_gallery_items) ignorando os termos —
    mesmo princípio já usado pra shot único desse modo. Modo busca por IA:
    uma chamada a search_and_download_footage POR item, cada um com seus
    próprios termos (`gallery_items_spec[i]["terms"]`) — chamar a MESMA
    busca N vezes devolveria sempre o mesmo candidato (busca e ranking são
    determinísticos), por isso a IA planeja sub-assuntos distintos em vez
    de reusar o "terms" do shot.

    `slug=None` de propósito: pula save/load_candidates_for_review (as duas
    únicas coisas que usam `slug` nessa função) — um item de galeria não é
    revisável nesta entrega (mesmo precedente já aceito pra mídia própria),
    então nem precisa de slot sintético pra evitar colisão de cache — sem
    slug, a checagem de review nunca roda pra esses itens.
    """
    if distributor is not None:
        return distributor.next_gallery_items(len(gallery_items_spec))

    items = []
    for item_spec in gallery_items_spec:
        try:
            found = search_and_download_footage(
                beat_id,
                beat_text,
                item_spec.get("terms") or [],
                slug=None,
                slot=0,
                strategy=shot["strategy"],
                entities=analysis.get("entities") or [],
                allowed_sources=allowed_sources,
                google_images_recency=google_images_recency,
                subject=item_spec.get("subject") or "",
            )
        except Exception:
            logger.exception(
                "Falha buscando item de galeria (beat %d): %r", beat_id, item_spec.get("subject")
            )
            continue
        if found.get("clip_path"):
            items.append(found)
    return items


def _assemble_composition(
    beats: list[Beat],
    slug: str,
    on_beat_progress: OnBeatProgress | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
    allowed_sources: list[str] | None = None,
    google_images_recency: str | None = None,
    media_mode: str = "ai_search",
    subscribe_identity: dict | None = None,
) -> dict:
    """Monta o composition.json a partir de uma lista de beats já pronta —
    tanto faz se vieram do parsing de um arquivo de roteiro (CLI) ou já
    chegaram prontos (painel web, onde o usuário monta os blocos um a um).

    `allowed_sources`: fontes de footage escolhidas pra este vídeo específico
    (None = usa footage.sources do config.yaml sem restrição extra).

    `media_mode`: "ai_search" (padrão, busca automática por IA) ou
    "own_media" (distribui o lote de mídia própria enviado pelo usuário —
    ver modules/media_pool.py — em vez de buscar. `allowed_sources`/
    `google_images_recency` são ignorados nesse modo).

    `subscribe_identity`: `{"channel_name", "handle", "avatar_filename"}` já
    resolvido pelo caller (webapp/server.py — este módulo nunca importa
    webapp/, então não sabe o que é "canal" nem lê state/channels.json
    sozinho). None ou handle vazio = vídeo sem a barra de inscrever-se.
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

    # instanciado uma vez pro vídeo INTEIRO, não por beat — o padrão
    # foto/foto/vídeo precisa ser contínuo do início ao fim, não reiniciar a
    # cada bloco de narração.
    distributor = PoolDistributor(slug, cfg) if media_mode == "own_media" else None
    # idem pra transição/parallax (Fase 5) — só quem vê o vídeo inteiro
    # consegue evitar repetir o mesmo tipo em sequência. Vale pros dois
    # media_mode (ai_search e own_media).
    effect_picker = _EffectPicker(cfg)

    composition_beats = []
    for position, beat in enumerate(beats):
        beat_timing = beats_by_id[beat.id]

        # Editor de timeline manual (painel web, modo de mídia própria):
        # o usuário já escolheu a mídia de cada trecho de ~4s antes de criar
        # o job (ver modules/timeline.py) — pula shot-planning por IA e
        # PoolDistributor inteiramente pra este beat, monta as cenas direto
        # do manifesto. Beat de mídia própria SEM manifesto (CLI, ou job
        # antigo de antes desta feature) cai no fluxo de sempre logo abaixo
        # (PoolDistributor com rodízio automático), sem quebrar compat.
        manifest = load_manifest(slug, beat.id) if media_mode == "own_media" else None
        if manifest is not None:
            if on_beat_progress is not None:
                on_beat_progress(beat.id, "keywords", "done")
                on_beat_progress(beat.id, "footage", "done")
                on_beat_progress(beat.id, "captions", "running")
            captions = ensure_captions(beat_timing, slug)
            if on_beat_progress is not None:
                on_beat_progress(beat.id, "captions", "done")
            scenes = _scenes_from_manifest(
                manifest, slug, beat_timing["start_seconds"], cfg, effect_picker
            )
            composition_beats.append(
                {
                    "id": beat.id,
                    "text": beat.text,
                    "start_seconds": beat_timing["start_seconds"],
                    "end_seconds": beat_timing["end_seconds"],
                    "type": "concreto",
                    "entities": [],
                    "chart": None,
                    "scenes": scenes,
                    "highlights": [],
                    "captions": captions,
                }
            )
            continue

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
            # o diretor visual decide melhor sabendo o que vem antes e depois
            prev_text=beats[position - 1].text if position > 0 else None,
            next_text=next_beat.text if next_beat else None,
        )
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "keywords", "done")
            on_beat_progress(beat.id, "footage", "running")

        # footage é buscado pra todo beat, mesmo "estatistico" — o primeiro
        # shot também vira o fundo desfocado atrás do gráfico (AnimatedChart).
        # Shots com estratégia MOTION_GRAPHIC/TEXT não buscam nada: viram card.
        shots = []
        for slot, shot in enumerate(analysis["shots"]):
            gallery_items_spec = shot.get("gallery_items") if shot.get("effect", "none") != "none" else None
            if shot["strategy"] not in STRATEGIES_THAT_SEARCH:
                found = {"clip_path": None, "relevance_score": None}
            elif gallery_items_spec:
                # Fase 5: Split Screen/Comparison Slider/Gallery Grid/
                # Masonry — a IA decidiu que este shot precisa de várias
                # mídias ao mesmo tempo (ver keyword_extractor.py), não uma
                # busca normal.
                gallery_items = _fetch_gallery_items(
                    beat.id, beat.text, shot, gallery_items_spec, analysis,
                    distributor, allowed_sources, google_images_recency,
                )
                found = (
                    {"clip_path": None, "gallery_items": gallery_items, "gallery_effect": shot["effect"], "relevance_score": None}
                    if len(gallery_items) >= 2
                    else {"clip_path": None, "relevance_score": None}
                )
            elif distributor is not None:
                # modo de mídia própria: ignora terms/entities/identity —
                # esses vêm da análise por IA (ainda roda, é o que decide
                # n_shots/chart/concept_text), mas a ESCOLHA de mídia é só
                # o rodízio fixo, sem julgamento de relevância nenhum.
                found = distributor.next_shot_media(beat.id, slot)
            else:
                found = search_and_download_footage(
                    beat.id,
                    beat.text,
                    shot["terms"],
                    slug=slug,
                    slot=slot,
                    strategy=shot["strategy"],
                    entities=analysis.get("entities") or [],
                    allowed_sources=allowed_sources,
                    google_images_recency=google_images_recency,
                    subject=shot.get("subject") or "",
                    identity_required=shot.get("identity_required") or False,
                    original_language_query=shot.get("original_language_query") or "",
                )
            shots.append(
                {
                    **found,
                    "slot": slot,
                    "strategy": shot["strategy"],
                    "concept_text": shot["concept_text"],
                    "motion_graphic": shot.get("motion_graphic"),
                }
            )
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
        on_reuse = distributor.reuse_media if distributor is not None else None
        on_gallery_reuse = distributor.reuse_gallery_item if distributor is not None else None
        scenes = _tile_scenes(
            visual_start, visual_end, shots, cfg, has_chart, chart_at, on_reuse, effect_picker, on_gallery_reuse
        )
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
                "entities": analysis.get("entities", []),
                "chart": analysis["chart"],
                "scenes": scenes,
                "highlights": _anchor_highlights(
                    analysis.get("highlights", []), captions, chart_ranges, cfg
                ),
                "captions": captions,
            }
        )

    subscribe_popup = None
    if subscribe_identity and subscribe_identity.get("handle"):
        effective_language = language or cfg["narration"]["language"]
        subscribe_text, subscribed_text = _SUBSCRIBE_LABELS.get(
            effective_language, _SUBSCRIBE_LABELS["pt"]
        )
        subscribe_cfg = cfg["subscribe"]
        avatar_filename = subscribe_identity.get("avatar_filename")
        subscribe_popup = {
            "channel_name": subscribe_identity.get("channel_name", ""),
            "channel_handle": subscribe_identity["handle"],
            "avatar_path": (
                f"state/channel_avatars/{avatar_filename}" if avatar_filename else None
            ),
            "cycle_seconds": subscribe_cfg["cycle_seconds"],
            "offset_seconds": subscribe_cfg["offset_seconds"],
            "subscribe_text": subscribe_text,
            "subscribed_text": subscribed_text,
        }

    composition = {
        "fps": cfg["video"]["fps"],
        "width": cfg["video"]["width"],
        "height": cfg["video"]["height"],
        "audio": {
            "path": _path_str(narration["audio_path"]),
            "duration_seconds": narration["duration_seconds"],
        },
        "music": None,
        "subscribe_popup": subscribe_popup,
        "beats": composition_beats,
    }

    validate_composition(composition)

    _write_credits(composition, slug)

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
    allowed_sources: list[str] | None = None,
    google_images_recency: str | None = None,
    media_mode: str = "ai_search",
    subscribe_identity: dict | None = None,
) -> dict:
    """Usada pelo CLI (pipeline.py): lê e divide um arquivo de roteiro."""
    script_path = Path(script_path)
    slug = slug or script_path.stem
    beats = parse_script(script_path)
    return _assemble_composition(
        beats,
        slug,
        on_beat_progress,
        voice_id,
        language,
        speed,
        allowed_sources,
        google_images_recency,
        media_mode,
        subscribe_identity,
    )


def build_composition_from_beats(
    beats: list[Beat],
    slug: str,
    on_beat_progress: OnBeatProgress | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
    allowed_sources: list[str] | None = None,
    google_images_recency: str | None = None,
    media_mode: str = "ai_search",
    subscribe_identity: dict | None = None,
) -> dict:
    """Usada pelo painel web: os beats já vêm prontos (o usuário monta o
    roteiro bloco a bloco na interface), sem precisar de um arquivo no disco."""
    return _assemble_composition(
        beats,
        slug,
        on_beat_progress,
        voice_id,
        language,
        speed,
        allowed_sources,
        google_images_recency,
        media_mode,
        subscribe_identity,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m modules.composition_builder <caminho-do-roteiro>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    build_composition(sys.argv[1])
