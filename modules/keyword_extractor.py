"""
Passo 3 do pipeline: análise de cada beat numa única chamada de LLM —
termos de busca visual + classificação concreto/estatístico + dados
estruturados pra gráfico animado quando fizer sentido (beat com número,
percentual, ano marcante ou índice que vale destacar na tela, ver
remotion/src/AnimatedChart.tsx).
"""
from __future__ import annotations

import json
import logging
import os

from modules.config import cache_dir, load_config
from modules.script_parser import Beat

logger = logging.getLogger(__name__)

# Usado quando o LLM falha (erro de API, resposta não parseável) para o beat
# não travar o pipeline inteiro — genérico o bastante pra retornar *algum*
# footage temático em vez de quebrar a etapa de busca, e trata o beat como
# "concreto" (sem gráfico) por ser a opção mais segura no fallback.
FALLBACK_ANALYSIS = {
    "type": "concreto",
    "search_terms": ["cinematic b-roll", "documentary background", "abstract texture"],
    "chart": None,
}

_PROMPT_TEMPLATE = """Você é um assistente de pré-produção para um vídeo documentário estilo \
"faceless YouTube". Analise o trecho de narração abaixo (em português) e responda com um único \
JSON, sem markdown:

{{
  "type": "concreto" ou "estatistico",
  "search_terms": ["termo1", "termo2", ...],
  "chart": null ou {{
    "tipo": "crescimento" | "queda" | "comparacao" | "destaque",
    "label": "string curta explicando o dado",
    "valor_inicial": number ou null,
    "valor_final": number,
    "unidade": "string curta, ex: %, anos, mil, x"
  }}
}}

Regras:
- "search_terms": SEMPRE presente, {n_terms} termos em INGLÊS pra bancos de vídeo de stock \
(Pexels/Pixabay). Concretos e filmáveis (lugar, objeto, ação, pessoa) — evite abstrações. Use \
mesmo quando o tipo for "estatistico" (vira fundo temático atrás do gráfico).
- "estatistico": o trecho tem um número, percentual, ano marcante ou índice que vale destacar na \
tela — ex: "caiu 20%", "dobrou em 3 anos", "em 1969", "1º lugar no ranking".
- "concreto": o trecho descreve algo filmável sem um dado numérico central pra destacar.
- Quando "estatistico" com "chart.tipo" "crescimento"/"queda"/"comparacao": tem uma transição ou \
comparação clara (antes → depois) — preencha valor_inicial E valor_final.
- Quando "estatistico" com "chart.tipo" "destaque": só um valor marcante isolado (ano, índice, \
quantidade), sem comparação — valor_inicial fica null, só valor_final.
- Quando "concreto", "chart" é null.

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
        max_tokens=400,
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
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _parse_analysis(raw_response: str) -> dict:
    text = raw_response.strip()
    # o modelo às vezes envolve o JSON em ```json ... ``` apesar da instrução
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    analysis = json.loads(text)

    if analysis.get("type") not in ("concreto", "estatistico"):
        raise ValueError(f"type inválido na resposta do LLM: {analysis.get('type')!r}")
    terms = analysis.get("search_terms")
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ValueError("search_terms não é uma lista de strings")
    if analysis["type"] == "estatistico" and not isinstance(analysis.get("chart"), dict):
        raise ValueError("type='estatistico' mas chart não veio como objeto")

    return analysis


def analyze_beat(beat: Beat, slug: str) -> dict:
    """Analisa um beat: termos de busca visual + classificação concreto/
    estatístico + dados de gráfico quando aplicável. Cache em disco por beat.

    Retorna {"type": "concreto"|"estatistico", "search_terms": [...],
    "chart": None | {"tipo", "label", "valor_inicial", "valor_final", "unidade"}}.
    """
    cfg = load_config()
    beat_dir = cache_dir("keywords", slug)
    cache_path = beat_dir / f"beat_{beat.id:03d}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    kw_cfg = cfg["keywords"]
    prompt = _PROMPT_TEMPLATE.format(n_terms=kw_cfg["terms_per_beat"], text=beat.text)

    try:
        if kw_cfg["provider"] == "anthropic":
            raw = _call_anthropic(prompt, kw_cfg["model"])
        elif kw_cfg["provider"] == "openai":
            raw = _call_openai(prompt, kw_cfg["model"])
        else:
            raise ValueError(f"provider desconhecido em config.yaml: {kw_cfg['provider']}")
        analysis = _parse_analysis(raw)
    except Exception:
        logger.exception("Falha ao analisar o beat %d, usando fallback genérico.", beat.id)
        analysis = dict(FALLBACK_ANALYSIS)

    cache_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return analysis


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from modules.script_parser import parse_script

    if len(sys.argv) != 2:
        print("Uso: python -m modules.keyword_extractor <caminho-do-roteiro>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    script_beats = parse_script(sys.argv[1])
    script_slug = Path(sys.argv[1]).stem
    for b in script_beats:
        result = analyze_beat(b, script_slug)
        print(f"\nbeat {b.id} [{result['type']}]: {b.text[:70]}")
        print(f"  search_terms: {result['search_terms']}")
        if result["chart"]:
            print(f"  chart: {result['chart']}")
