"""
Canais e vozes favoritas por canal — persistência simples em JSON local.

Não é cache regenerável (como cache/), é preferência do usuário: guardamos o
objeto da voz inteiro (não só o id) pra não depender do manifest daquele
idioma continuar igual depois.
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.config import PROJECT_ROOT

STATE_DIR = PROJECT_ROOT / "state"
CHANNELS_FILE = STATE_DIR / "channels.json"


def _load() -> dict:
    if not CHANNELS_FILE.exists():
        return {}
    return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CHANNELS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_channels() -> list[str]:
    return sorted(_load().keys())


def create_channel(name: str) -> list[str]:
    data = _load()
    if name not in data:
        data[name] = {"favorites": []}
        _save(data)
    return sorted(data.keys())


def get_favorites(channel: str) -> list[dict]:
    return _load().get(channel, {}).get("favorites", [])


def add_favorite(channel: str, voice: dict) -> list[dict]:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    if not any(v["id"] == voice["id"] for v in entry["favorites"]):
        entry["favorites"].append(voice)
        _save(data)
    return entry["favorites"]


def remove_favorite(channel: str, voice_id: str) -> list[dict]:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    entry["favorites"] = [v for v in entry["favorites"] if v["id"] != voice_id]
    _save(data)
    return entry["favorites"]


_DEFAULT_IDENTITY = {
    "handle": "", "avatar_filename": None, "image_style_prompt": "", "character_style_prompt": "",
    "voice_waveform_enabled": False, "script_examples": [],
}


def _new_identity() -> dict:
    """Identidade nova (canal recém-criado) com cada campo mutável (hoje só
    `script_examples`) numa lista PRÓPRIA — nunca `dict(_DEFAULT_IDENTITY)`
    direto, que faria uma cópia RASA e deixaria todo canal novo
    compartilhando a MESMA lista de `_DEFAULT_IDENTITY` (um `append` num
    canal vazaria pros outros)."""
    return {**_DEFAULT_IDENTITY, "script_examples": []}


def get_identity(channel: str) -> dict:
    entry = _load().get(channel, {})
    identity = entry.get("identity", {})
    return {**_DEFAULT_IDENTITY, **identity}


def set_handle(channel: str, handle: str) -> dict:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    identity["handle"] = handle
    _save(data)
    return {**_DEFAULT_IDENTITY, **identity}


def set_avatar_filename(channel: str, filename: str) -> dict:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    identity["avatar_filename"] = filename
    _save(data)
    return {**_DEFAULT_IDENTITY, **identity}


def set_image_style(channel: str, style: str) -> dict:
    """Estilo visual fixo pros prompts de imagem gerados pra vídeos deste
    canal (ex.: "quadro-negro, giz branco e azul claro sobre fundo preto,
    diagramas desenhados à mão") — concatenado em modules/timeline.py::
    generate_slot_hints, não pedido pra IA lembrar de aplicar sozinha."""
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    identity["image_style_prompt"] = style
    _save(data)
    return {**_DEFAULT_IDENTITY, **identity}


def set_voice_waveform_enabled(channel: str, enabled: bool) -> dict:
    """Liga/desliga o overlay de "raio-X da voz" (barras reagindo à
    amplitude real da narração, por todo o vídeo — ver
    modules/composition_builder.py e remotion/src/VoiceWaveform.tsx)
    pra este canal. Default False: não muda a aparência de vídeos de
    canais que não pediram isso."""
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    identity["voice_waveform_enabled"] = bool(enabled)
    _save(data)
    return {**_DEFAULT_IDENTITY, **identity}


def set_character_style(channel: str, style: str) -> dict:
    """Estilo visual que SUBSTITUI `image_style_prompt` nos trechos onde a
    cena tem uma pessoa em destaque (`has_person: true`, ver
    modules/timeline.py::generate_slot_hints) — pedido do usuário pra
    evitar gerar pessoa fotorrealista em canais que preferem um
    "personagem" ilustrado (ex.: "flat vector illustration, bold thick
    black outlines, solid flat colors, expressive cartoon style" pro
    Gesund ab 60)."""
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    identity["character_style_prompt"] = style
    _save(data)
    return {**_DEFAULT_IDENTITY, **identity}


def add_script_example(channel: str, text: str) -> list[str]:
    """Roteiro de exemplo que o usuário já escreveu pra este canal — usado
    como referência de estilo (tom, estrutura, ritmo) na geração
    automática de roteiro (modules/script_writer.py). Guarda o texto
    INTEIRO, não um resumo — é isso que a IA usa como few-shot."""
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    identity.setdefault("script_examples", []).append(text)
    _save(data)
    return identity["script_examples"]


def remove_script_example(channel: str, index: int) -> list[str]:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    identity = entry.setdefault("identity", _new_identity())
    examples = identity.setdefault("script_examples", [])
    if 0 <= index < len(examples):
        examples.pop(index)
    _save(data)
    return examples
