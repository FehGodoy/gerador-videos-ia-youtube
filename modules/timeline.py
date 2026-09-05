"""
Editor de timeline manual (modo de mídia própria, painel web): fatia a
narração de um bloco em trechos de ~4s a partir dos timestamps por palavra
que a Cartesia já devolve (modules/narration.py), gera tradução + dica de
IA por trecho, e guarda qual arquivo do lote de mídia própria (modules/
media_pool.py) o usuário atribuiu a cada um.

Separado de composition_builder.py (que fatia em CENAS pra render) porque o
grão é diferente e a origem também: aqui é ~4s fixo, decidido no momento em
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

# Alvo de duração de cada trecho — mesmo valor pedido pelo usuário. Trechos
# reais variam um pouco pra sempre fechar em fronteira de palavra (nunca
# cortar uma palavra ao meio).
DEFAULT_TARGET_SECONDS = 4.0

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
# 3 = ganhou needs_media (classifica trecho abstrato/transição como "vira
# texto", sem precisar de mídia anexada). 4 = image_prompt passou a exigir
# que texto visível NA IMAGEM (infográfico, linha do tempo etc.) fique no
# idioma do roteiro — antes vazava pra português por padrão mesmo em vídeo
# em outro idioma (bug real reportado pelo usuário, com print de um
# infográfico da Toyota Camry em inglês que saiu com texto em português).
TIMELINE_HINTS_VERSION = 4

_HINTS_MAX_TOKENS = 2000

_HINTS_PROMPT_TEMPLATE = """Você ajuda um criador de vídeos documentários que sobe a própria mídia \
(fotos/vídeos) a decidir o que colocar em cada trecho da narração. O roteiro abaixo está em \
{language_name}. Responda com UM ÚNICO objeto JSON, sem markdown:

{{"slots": [{{"index": 0, "translation_pt": "...", "hint": "...", "image_prompt": "...", \
"needs_media": true}}, ...]}}

Um item por trecho numerado abaixo, na mesma ordem, onde:
- "translation_pt": tradução literal do trecho pra português do Brasil (se o roteiro já estiver \
em português, repita o texto original sem alterar).
- "hint": dica curta (até 12 palavras), em português, do tipo de foto ou vídeo que combina com \
aquele trecho — só pra ajudar o usuário a escolher entre as mídias que ele já tem, não é busca.
- "image_prompt": prompt curto e direto (até 20 palavras), em inglês, pronto pra colar num gerador \
de imagem por IA (Midjourney, DALL-E, etc.) caso o usuário prefira gerar a imagem em vez de usar \
uma mídia própria — descreva a cena visualmente (sujeito, cenário, enquadramento), sem falar sobre \
o vídeo em si. Regra crítica quando a cena pedir QUALQUER texto visível na própria imagem \
(infográfico, linha do tempo, gráfico, placa, capa, letreiro, legenda): esse texto tem que estar \
em {language_name} — o idioma do ROTEIRO/vídeo, não português por padrão — e o prompt precisa \
citar as palavras exatas entre aspas (ex.: "with the timeline labeled '1982', '1987', '1992' in \
{language_name}"), nunca deixar a IA de imagem escolher o idioma sozinha.
- "needs_media": false SOMENTE quando o trecho é abstrato/transição — uma reflexão, uma frase-ponte, \
uma pergunta retórica — sem NADA concreto e específico pra mostrar; esse trecho vira um card de \
texto na tela em vez de pedir foto/vídeo. Na dúvida, ou quando há qualquer coisa filmável/fotografável \
(pessoa, lugar, objeto, ação, evento), responda true.

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
                "needs_media": True,
                # True quando o usuário já respondeu explicitamente (botão
                # "usar mídia aqui mesmo assim"/"isso não precisa de
                # mídia") — trava esse campo contra a próxima resposta da
                # IA (ver webapp/server.py::generate_block_hints).
                "needs_media_overridden": False,
                "effect": DEFAULT_EFFECT,
                "media": [],
                # Aviso de possível desalinhamento (webapp/folder_sync.py) —
                # {"gap_seconds", "expected_seconds"} quando o download
                # deste trecho demorou bem mais que o normal aprendido, ou
                # None. Nunca bloqueia nada, é só uma dica pro usuário
                # conferir; some ao reatribuir/remover a mídia do trecho.
                "sync_warning": None,
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


def delete_manifest(slug: str, beat_id: int) -> None:
    """Apaga o manifesto de um bloco removido no painel — sem isso, o
    sincronizador de pasta do rascunho inteiro (webapp/folder_sync.py)
    continuaria descobrindo o arquivo em disco e oferecendo vaga pra um
    bloco que o usuário já achava excluído."""
    _manifest_path(slug, beat_id).unlink(missing_ok=True)


def list_block_ids(slug: str) -> list[int]:
    """Ids dos blocos que já têm manifesto (foram fatiados), em ordem
    crescente — descobertos pelos arquivos beat_NNN.json em disco, não por
    uma lista mandada pelo painel. Usado tanto pelo sincronizador de pasta
    (webapp/folder_sync.py, pra saber a ordem de preenchimento do rascunho
    inteiro) quanto por shift_media_from abaixo."""
    ids = []
    for path in cache_dir("timeline", slug).glob("beat_*.json"):
        if path.stem.endswith("_hints"):
            continue
        try:
            ids.append(int(path.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return sorted(ids)


def is_single_media_slot(slot: dict) -> bool:
    """True quando o trecho é elegível pro preenchimento automático de
    mídia única (padrão/parallax_pan) — mesmo critério de
    folder_sync._next_empty_position: trecho marcado needs_media=false
    (IA ou usuário decidiu que vira texto) e efeito de galeria (2+
    posições, decidido sempre à mão) nunca entram nessa conta."""
    if slot.get("needs_media") is False:
        return False
    effect = slot.get("effect") or DEFAULT_EFFECT
    return effect not in GALLERY_EFFECTS


def shift_media_from(slug: str, from_block_id: int, from_slot_index: int) -> dict:
    """Corrige o desalinhamento causado por um download que falhou no meio
    da sincronização automática de pasta (webapp/folder_sync.py): ela só
    enxerga arquivos chegando numa pasta, nunca sabe que um download
    falhou, então quando isso acontece o próximo download bem-sucedido cai
    no trecho ERRADO (o que devia ter recebido o arquivo que falhou) — e
    tudo que vem depois fica uma posição adiantado.

    O trecho indicado é o PRIMEIRO com a mídia errada, na visão de quem
    está revisando — ele guardava a mídia que na verdade pertence ao
    próximo trecho elegível (`is_single_media_slot`, mesmo filtro do
    sincronizador — vale através de vários blocos, na mesma ordem que o
    sincronizador usa, ver list_block_ids), porque o download DELE é que
    falhou, não o dele mesmo. Empurra a mídia de cada trecho seguinte uma
    posição PRA FRENTE (o trecho seguinte herda o que o anterior tinha) e
    deixa o trecho indicado (o primeiro da cadeia) VAZIO — é ali que falta
    a mídia de verdade, pronta pra você baixar e encaixar.

    Se houver MAIS de um download que falhou no mesmo bloco/rascunho, cada
    chamada resolve só o PRIMEIRO buraco a partir do ponto indicado —
    repare que o padrão errado continua a partir de onde o próximo buraco
    começa, e chame de novo a partir dali (nessa ordem, do mais cedo pro
    mais tarde: aplicar fora de ordem re-embaralha uma correção já feita).

    Retorna {"shifted": int, "cleared": {"block_id", "slot_index"} | None}
    — "shifted" é quantos trechos mudaram de mídia; "cleared" é o próprio
    trecho indicado, agora vazio (None só quando o trecho indicado não é
    elegível e não sobra nenhum outro elegível a partir dali).
    """
    chain: list[dict] = []
    manifests: dict[int, list[dict]] = {}
    started = False
    for block_id in list_block_ids(slug):
        manifest = load_manifest(slug, block_id)
        if manifest is None:
            continue
        manifests[block_id] = manifest
        for slot in manifest:
            if block_id == from_block_id and slot["index"] == from_slot_index:
                started = True
            if not started or not is_single_media_slot(slot):
                continue
            chain.append((block_id, slot))

    if not chain:
        return {"shifted": 0, "cleared": None}

    # De trás pra frente: cada trecho herda o que o ANTERIOR tinha (antes
    # de ser sobrescrito) — precisa ir do fim pro começo pra nunca ler um
    # valor que essa mesma passada já sobrescreveu.
    shifted = 0
    for i in range(len(chain) - 1, 0, -1):
        _, slot_current = chain[i]
        _, slot_prev = chain[i - 1]
        slot_current["media"] = list(slot_prev.get("media") or [])
        shifted += 1

    first_block_id, first_slot = chain[0]
    first_slot["media"] = []

    for block_id, manifest in manifests.items():
        save_manifest(slug, block_id, manifest)

    return {
        "shifted": shifted,
        "cleared": {"block_id": first_block_id, "slot_index": first_slot["index"]},
    }


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
        needs_media = item.get("needs_media")
        by_index[idx] = {
            "translation_pt": translation.strip() if isinstance(translation, str) else "",
            "hint": hint.strip() if isinstance(hint, str) else "",
            "image_prompt": image_prompt.strip() if isinstance(image_prompt, str) else "",
            # ausente/tipo errado -> True (comportamento de hoje: pede
            # mídia) — nunca deixa um trecho sem exigência por causa de
            # resposta incompleta da IA.
            "needs_media": needs_media if isinstance(needs_media, bool) else True,
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

    O fallback de string vazia NÃO é gravado no cache (bug real: gerar
    vários blocos em sequência dispara uma chamada de LLM por bloco em
    paralelo, sem fila nenhuma — um provider instável/limite de taxa
    momentâneo derrubava só as últimas, e como o resultado vazio ficava
    cacheado pra sempre, nem reabrir o bloco tentava de novo). Só o
    resultado de uma resposta de verdade é persistido — uma falha
    transitória pode ser tentada de novo depois (ver
    webapp/static/app.js::fetchSlotHints, botão "Gerar de novo").
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
        return [
            {"translation_pt": "", "hint": "", "image_prompt": "", "needs_media": True}
            for _ in slots
        ]

    cache_path.write_text(
        json.dumps({"cache_key": cache_key, "hints": hints}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return hints
