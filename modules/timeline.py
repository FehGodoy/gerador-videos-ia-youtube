"""
Editor de timeline manual (modo de mídia própria, painel web): fatia a
narração de um bloco em trechos de ~3s a partir dos timestamps por palavra
que a Cartesia já devolve (modules/narration.py), gera tradução + dica de
IA por trecho, e guarda qual arquivo do lote de mídia própria (modules/
media_pool.py) o usuário atribuiu a cada um.

Separado de composition_builder.py (que fatia em CENAS pra render) porque o
grão é diferente e a origem também: aqui é ~3s fixo, decidido no momento em
que o bloco é gerado no painel, antes de existir job nenhum; lá é por shot
visual, decidido durante a montagem do composition.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from modules.config import cache_dir, load_config
from modules.keyword_extractor import _LANGUAGE_NAMES, _call_anthropic, _call_openai

logger = logging.getLogger(__name__)

# Alvo de duração de cada trecho — mesmo valor pedido pelo usuário ("a cada
# 3 segundos"). Trechos reais variam um pouco pra sempre fechar em fronteira
# de palavra (nunca cortar uma palavra ao meio).
DEFAULT_TARGET_SECONDS = 3.0

# Efeitos que o usuário pode escolher manualmente por trecho, no editor de
# timeline (modo mídia própria) — espelha os mesmos efeitos "de plano
# único" e "de galeria" que o pipeline automático decide sozinho (ver
# composition_builder.py::_EffectPicker e keyword_extractor.py), mas aqui
# a escolha é sempre do usuário, trecho a trecho, sem sorteio nem IA.
# min_media/max_media dizem quantos arquivos o trecho precisa ter anexado
# pra esse efeito funcionar (split_screen/comparison_slider são sempre
# exatamente 2; gallery_grid/masonry aceitam de 2 a 6, mesmo teto que
# composition_builder.py::_scene_gallery já aplica pro modo automático).
EFFECT_CATALOG: dict[str, dict] = {
    "padrao": {"label": "Padrão (mídia única)", "min_media": 1, "max_media": 1},
    "parallax_pan": {"label": "Parallax pan (mídia única)", "min_media": 1, "max_media": 1},
    "split_screen": {"label": "Split screen (2 lado a lado)", "min_media": 2, "max_media": 2},
    "comparison_slider": {"label": "Antes/depois", "min_media": 2, "max_media": 2},
    "gallery_grid": {"label": "Grade de galeria (2 a 6 mídias)", "min_media": 2, "max_media": 6},
    "masonry": {"label": "Colagem (2 a 6 mídias)", "min_media": 2, "max_media": 6},
}
DEFAULT_EFFECT = "padrao"
GALLERY_EFFECTS = {"split_screen", "comparison_slider", "gallery_grid", "masonry"}


def effect_media_bounds(effect: str) -> tuple[int, int]:
    spec = EFFECT_CATALOG.get(effect, EFFECT_CATALOG[DEFAULT_EFFECT])
    return spec["min_media"], spec["max_media"]

# Sobe manualmente sempre que _HINTS_PROMPT_TEMPLATE ou _parse_hints mudarem
# de formato — mesmo princípio de ANALYSIS_VERSION em keyword_extractor.py.
TIMELINE_HINTS_VERSION = 2

_HINTS_MAX_TOKENS = 2000

_HINTS_PROMPT_TEMPLATE = """Você ajuda um criador de vídeos documentários que sobe a própria mídia \
(fotos/vídeos) a decidir o que colocar em cada trecho da narração. O roteiro abaixo está em \
{language_name}. Responda com UM ÚNICO objeto JSON, sem markdown:

{{"slots": [{{"index": 0, "translation_pt": "...", "hint": "...", "image_prompt": "..."}}, ...]}}

Um item por trecho numerado abaixo, na mesma ordem, onde:
- "translation_pt": tradução literal do trecho pra português do Brasil (se o roteiro já estiver \
em português, repita o texto original sem alterar).
- "hint": dica curta (até 12 palavras), em português, do tipo de foto ou vídeo que combina com \
aquele trecho — só pra ajudar o usuário a escolher entre as mídias que ele já tem, não é busca.
- "image_prompt": prompt curto e direto (até 20 palavras), em inglês, pronto pra colar num gerador \
de imagem por IA (Midjourney, DALL-E, etc.) caso o usuário prefira gerar a imagem em vez de usar \
uma mídia própria — descreva a cena visualmente (sujeito, cenário, enquadramento), sem falar sobre \
o vídeo em si.

Trechos:
{numbered_slots}
"""

_RETRY_SUFFIX = (
    "\n\nATENÇÃO: sua resposta anterior não era um JSON válido. Responda AGORA apenas com o "
    "objeto JSON pedido, começando com { e terminando com }, sem nenhum texto antes ou depois."
)


def chunk_captions(captions: list[dict], target_seconds: float = DEFAULT_TARGET_SECONDS) -> list[dict]:
    """Fatia os timestamps por palavra de um beat (ver
    narration.synthesize_beat) em trechos de ~`target_seconds`, sempre
    fechando depois de uma palavra inteira — nunca no meio.

    Caminhada gulosa: acumula palavra por palavra até a duração acumulada
    bater o alvo, fecha o trecho ali. O resto do fim (menor que o alvo, já
    que só sobra quando as palavras acabam antes de bater o alvo de novo)
    é absorvido pelo ÚLTIMO trecho fechado em vez de virar um trechinho
    minúsculo sozinho.

    Retorna [{"index", "text", "start_seconds", "end_seconds", "effect",
    "media": []}, ...] com segundos relativos ao início do bloco (mesmo
    referencial de `captions`) — o offset pra timeline global do vídeo é
    aplicado depois, em composition_builder.py, mesmo padrão que
    build_narration já usa pra beats inteiros.
    """
    if not captions:
        return []

    slots: list[dict] = []
    bucket: list[dict] = []
    bucket_start = captions[0]["start_seconds"]

    def flush(end_seconds: float) -> None:
        slots.append(
            {
                "index": len(slots),
                "text": " ".join(w["word"] for w in bucket),
                "start_seconds": round(bucket_start, 3),
                "end_seconds": round(end_seconds, 3),
                "translation_pt": "",
                "hint": "",
                "image_prompt": "",
                "effect": DEFAULT_EFFECT,
                "media": [],
            }
        )

    for word in captions:
        bucket.append(word)
        if word["end_seconds"] - bucket_start >= target_seconds:
            flush(word["end_seconds"])
            bucket = []
            bucket_start = word["end_seconds"]

    if bucket:
        if slots:
            last = slots[-1]
            last["text"] = f"{last['text']} {' '.join(w['word'] for w in bucket)}".strip()
            last["end_seconds"] = round(bucket[-1]["end_seconds"], 3)
        else:
            flush(bucket[-1]["end_seconds"])

    return slots


def _manifest_path(slug: str, beat_id: int) -> Path:
    return cache_dir("timeline", slug) / f"beat_{beat_id:03d}.json"


def save_manifest(slug: str, beat_id: int, slots: list[dict]) -> None:
    _manifest_path(slug, beat_id).write_text(
        json.dumps(slots, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_manifest(slug: str, beat_id: int) -> list[dict] | None:
    path = _manifest_path(slug, beat_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _hints_cache_path(slug: str, beat_id: int) -> Path:
    return cache_dir("timeline", slug) / f"beat_{beat_id:03d}_hints.json"


def _cache_key(beat_text: str, language: str, model: str) -> str:
    payload = f"{TIMELINE_HINTS_VERSION}|{beat_text}|{language}|{model}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _parse_hints(raw_response: str, n_slots: int) -> list[dict] | None:
    text = raw_response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    data = json.loads(text)
    slots_raw = data.get("slots") if isinstance(data, dict) else None
    if not isinstance(slots_raw, list):
        return None

    by_index: dict[int, dict] = {}
    for item in slots_raw:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int):
            continue
        translation = item.get("translation_pt")
        hint = item.get("hint")
        image_prompt = item.get("image_prompt")
        by_index[idx] = {
            "translation_pt": translation.strip() if isinstance(translation, str) else "",
            "hint": hint.strip() if isinstance(hint, str) else "",
            "image_prompt": image_prompt.strip() if isinstance(image_prompt, str) else "",
        }
    if len(by_index) < n_slots:
        return None
    return [by_index[i] for i in range(n_slots)]


def generate_slot_hints(
    slots: list[dict], beat_text: str, language: str, slug: str, beat_id: int
) -> list[dict]:
    """Tradução pra português + dica curta de mídia, um item por trecho —
    UMA chamada de LLM pro bloco inteiro (evita N chamadas separadas),
    mesmo padrão de keyword_extractor.analyze_beat (cache em disco,
    retry num JSON malformado). Nunca levanta exceção: dica/tradução são
    só apoio visual, uma falha aqui não pode travar o usuário de atribuir
    mídia — cai pra string vazia em cada item.
    """
    if not slots:
        return []

    cfg = load_config()
    kw_cfg = cfg["keywords"]
    call = {"anthropic": _call_anthropic, "openai": _call_openai}.get(kw_cfg["provider"])

    cache_path = _hints_cache_path(slug, beat_id)
    cache_key = _cache_key(beat_text, language, kw_cfg["model"])
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("cache_key") == cache_key and len(cached.get("hints", [])) == len(slots):
            return cached["hints"]

    hints = None
    if call is not None:
        numbered = "\n".join(f"{s['index']}. {s['text']}" for s in slots)
        prompt = _HINTS_PROMPT_TEMPLATE.format(
            language_name=_LANGUAGE_NAMES.get(language, "português"),
            numbered_slots=numbered,
        )
        for attempt_prompt in (prompt, prompt + _RETRY_SUFFIX):
            try:
                hints = _parse_hints(call(attempt_prompt, kw_cfg["model"]), len(slots))
                if hints is not None:
                    break
            except Exception:
                logger.warning(
                    "Geração de dica/tradução do beat %d falhou.", beat_id, exc_info=True
                )

    if hints is None:
        logger.warning("Beat %d: dica/tradução ficou vazia (LLM indisponível ou resposta ruim).", beat_id)
        hints = [{"translation_pt": "", "hint": "", "image_prompt": ""} for _ in slots]

    cache_path.write_text(
        json.dumps({"cache_key": cache_key, "hints": hints}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return hints
