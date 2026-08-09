"""
Passo 3 do pipeline (Fase 1: só extração de keywords, sem classificação
concreto/estatístico — isso é trabalho da Fase 2).

Para cada beat, pede a um LLM barato 2-4 termos de busca visual em inglês,
priorizando termos concretos e filmáveis (evita abstrações como "liberdade").
"""
from __future__ import annotations

import json
import logging
import os

from modules.config import cache_dir, load_config
from modules.script_parser import Beat

logger = logging.getLogger(__name__)

# Usado quando o LLM falha (erro de API, resposta não parseável) para o beat
# não travar o pipeline inteiro — genérico o bastante para retornar *algum*
# footage temático em vez de quebrar a etapa de busca.
FALLBACK_TERMS = ["cinematic b-roll", "documentary background", "abstract texture"]

_PROMPT_TEMPLATE = """Você é um pesquisador visual para um vídeo documentário estilo \
"faceless YouTube". Dado um trecho de narração em português, gere entre 2 e {n} termos \
de busca em INGLÊS para bancos de vídeo de stock (Pexels/Pixabay).

Regras:
- Termos concretos e filmáveis (lugar, objeto, ação, pessoa) — evite abstrações.
- Priorize o que pode ser literalmente mostrado na tela.
- Responda APENAS com um array JSON de strings, nada mais.

Trecho: "{text}"
"""


def _call_anthropic(prompt: str, model: str) -> str:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY não definida no .env.")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_openai(prompt: str, model: str) -> str:
    try:
        import openai
    except ImportError as e:
        raise RuntimeError(
            "keywords.provider está como 'openai' em config.yaml, mas o pacote "
            "'openai' não está instalado. Rode: pip install openai"
        ) from e

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não definida no .env.")
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _parse_terms(raw_response: str) -> list[str]:
    text = raw_response.strip()
    # o modelo às vezes envolve o JSON em ```json ... ``` apesar da instrução
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    terms = json.loads(text)
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ValueError("resposta do LLM não é uma lista de strings")
    return terms


def extract_keywords(beat: Beat, slug: str) -> list[str]:
    """Retorna os termos de busca visual para um beat, com cache em disco."""
    cfg = load_config()
    beat_dir = cache_dir("keywords", slug)
    cache_path = beat_dir / f"beat_{beat.id:03d}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    kw_cfg = cfg["keywords"]
    prompt = _PROMPT_TEMPLATE.format(n=kw_cfg["terms_per_beat"] + 1, text=beat.text)

    try:
        if kw_cfg["provider"] == "anthropic":
            raw = _call_anthropic(prompt, kw_cfg["model"])
        elif kw_cfg["provider"] == "openai":
            raw = _call_openai(prompt, kw_cfg["model"])
        else:
            raise ValueError(f"provider desconhecido em config.yaml: {kw_cfg['provider']}")
        terms = _parse_terms(raw)
    except Exception:
        logger.exception(
            "Falha ao extrair keywords do beat %d, usando fallback genérico.", beat.id
        )
        terms = list(FALLBACK_TERMS)

    cache_path.write_text(json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8")
    return terms


if __name__ == "__main__":
    import sys

    from modules.script_parser import parse_script
    from pathlib import Path

    if len(sys.argv) != 2:
        print("Uso: python -m modules.keyword_extractor <caminho-do-roteiro>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    script_beats = parse_script(sys.argv[1])
    script_slug = Path(sys.argv[1]).stem
    for b in script_beats:
        print(b.id, extract_keywords(b, script_slug))
