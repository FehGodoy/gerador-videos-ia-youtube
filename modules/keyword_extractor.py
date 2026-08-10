"""
Passo 3 do pipeline: análise de cada beat numa única chamada de LLM —
uma lista de "shots" (ideias visuais distintas que vão se revezar ao longo do
beat, cada uma com seus termos de busca em inglês) + classificação
concreto/estatístico + dados estruturados pra gráfico animado quando fizer
sentido (beat com número, percentual, ano marcante ou índice que vale
destacar na tela, ver remotion/src/AnimatedChart.tsx).

Por que vários shots por beat: um bloco de narração pode ter minutos de
duração, e um clipe de stock tem ~10-30s. Com um único termo de busca por
beat, o mesmo clipe ficava congelado no último frame por minutos. Pedir N
ideias visuais distintas de uma vez (mesma chamada de LLM que já existia)
dá material pra preencher o bloco inteiro com variedade — o fatiamento em
cenas propriamente dito fica em modules/composition_builder.py.
"""
from __future__ import annotations

import json
import logging
import os

from modules.config import cache_dir, load_config
from modules.script_parser import Beat

logger = logging.getLogger(__name__)

# Com 400 (o valor da versão de um shot só) a resposta vinha truncada no meio
# do JSON em beats longos — o modelo tentava descrever cada assunto do trecho
# e estourava o limite, caindo no fallback genérico. 1500 cobre com folga o
# teto de shots por beat.
MAX_TOKENS = 1500

# Último recurso, quando nem a chamada normal nem o retry devolveram algo
# parseável — genérico o bastante pra retornar *algum* footage em vez de
# quebrar a etapa de busca, e trata o beat como "concreto" (sem gráfico) por
# ser a opção mais segura.
FALLBACK_TERMS = ["cinematic b-roll", "documentary background", "abstract texture"]

# Nome do idioma no prompt, pro label do gráfico sair na língua do roteiro.
# Sem isso, um roteiro em inglês ganhava legenda de gráfico em português
# (o prompt inteiro é escrito em pt e o modelo seguia a língua do prompt).
_LANGUAGE_NAMES = {
    "pt": "português",
    "en": "inglês",
    "es": "espanhol",
    "fr": "francês",
    "de": "alemão",
    "it": "italiano",
    "ja": "japonês",
}

_PROMPT_TEMPLATE = """Você é um assistente de pré-produção para um vídeo documentário estilo \
"faceless YouTube". Analise o trecho de narração abaixo e responda com UM ÚNICO objeto JSON, sem \
markdown e sem lista no nível de cima:

{{
  "type": "concreto" ou "estatistico",
  "shots": [
    {{"terms": ["termo em inglês", "alternativa", "alternativa"]}}
  ],
  "chart": null ou {{
    "tipo": "crescimento" | "queda" | "comparacao" | "destaque",
    "label": "string curta explicando o dado, escrita em {language_name}",
    "valor_inicial": number ou null,
    "valor_final": number,
    "unidade": "string curta, ex: %, anos, mil, x"
  }}
}}

Regras:
- "shots": EXATAMENTE {n_shots} itens, na ordem em que os assuntos aparecem no trecho. Cada item \
é uma imagem/vídeo diferente que vai ocupar um pedaço do trecho na tela. Os shots precisam ser \
VISUALMENTE DIFERENTES entre si (assuntos, objetos ou ângulos distintos que o trecho menciona) — \
não repita a mesma ideia com outras palavras.
- "terms": 2 a 3 buscas em INGLÊS pra bancos de vídeo de stock (Pexels/Pixabay), da mais \
específica pra mais genérica (a primeira que trouxer resultado é usada). Concretas e filmáveis \
(lugar, objeto, ação, pessoa) — evite abstrações.
- "estatistico": o trecho tem um número, percentual, ano marcante ou índice que vale destacar na \
tela — ex: "caiu 20%", "dobrou em 3 anos", "em 1969", "1º lugar no ranking".
- "concreto": o trecho descreve algo filmável sem um dado numérico central pra destacar.
- Quando "estatistico" com "chart.tipo" "crescimento"/"queda"/"comparacao": tem uma transição ou \
comparação clara (antes → depois) — preencha valor_inicial E valor_final.
- Quando "estatistico" com "chart.tipo" "destaque": só um valor marcante isolado (ano, índice, \
quantidade), sem comparação — valor_inicial fica null, só valor_final.
- Quando "concreto", "chart" é null.
- Responda o objeto JSON e nada mais, começando com {{ e terminando com }}.

Trecho: "{text}"
"""

# Concatenado depois do .format(), por isso as chaves aqui são literais (não escapadas).
_RETRY_SUFFIX = (
    "\n\nATENÇÃO: sua resposta anterior não era um JSON válido. Responda AGORA apenas com o "
    "objeto JSON pedido, começando com { e terminando com }, sem nenhum texto antes ou depois, "
    "sem markdown, e sem envolver em lista."
)


def _call_anthropic(prompt: str, model: str) -> str:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY não definida no .env.")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
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
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _parse_analysis(raw_response: str, n_shots: int) -> dict:
    text = raw_response.strip()
    # o modelo às vezes envolve o JSON em ```json ... ``` apesar da instrução
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    analysis = json.loads(text)

    if not isinstance(analysis, dict):
        raise ValueError(f"resposta do LLM não é um objeto JSON (veio {type(analysis).__name__})")
    if analysis.get("type") not in ("concreto", "estatistico"):
        raise ValueError(f"type inválido na resposta do LLM: {analysis.get('type')!r}")

    shots = analysis.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shots não é uma lista não-vazia")
    parsed_shots = []
    for shot in shots:
        terms = shot.get("terms") if isinstance(shot, dict) else None
        if not isinstance(terms, list):
            raise ValueError("shot sem lista de terms")
        clean = [t.strip() for t in terms if isinstance(t, str) and t.strip()]
        if not clean:
            raise ValueError("shot com terms vazio")
        parsed_shots.append({"terms": clean})

    if analysis["type"] == "estatistico" and not isinstance(analysis.get("chart"), dict):
        raise ValueError("type='estatistico' mas chart não veio como objeto")

    # o modelo às vezes devolve mais/menos shots que o pedido; corta o excesso
    # e completa repetindo os últimos, em vez de descartar uma resposta boa.
    while len(parsed_shots) < n_shots:
        parsed_shots.append(dict(parsed_shots[len(parsed_shots) % len(shots)]))

    return {
        "type": analysis["type"],
        "shots": parsed_shots[:n_shots],
        "chart": analysis.get("chart") if analysis["type"] == "estatistico" else None,
        "n_shots": n_shots,
    }


def _fallback_analysis(n_shots: int) -> dict:
    return {
        "type": "concreto",
        "shots": [{"terms": list(FALLBACK_TERMS)} for _ in range(n_shots)],
        "chart": None,
        "n_shots": n_shots,
    }


def analyze_beat(beat: Beat, slug: str, n_shots: int = 1, language: str = "pt") -> dict:
    """Analisa um beat: `n_shots` ideias visuais distintas (cada uma com seus
    termos de busca) + classificação concreto/estatístico + dados de gráfico
    quando aplicável. Cache em disco por beat.

    `n_shots` vem da duração do beat (ver composition_builder) — beats longos
    precisam de mais material visual pra não ficar um clipe só na tela. O
    cache guarda o n_shots usado e é refeito se ele mudar (ex: a narração foi
    regerada numa velocidade diferente e o beat encolheu/cresceu).

    Retorna {"type": "concreto"|"estatistico", "shots": [{"terms": [...]}, ...],
    "chart": None | {...}, "n_shots": int}.
    """
    cfg = load_config()
    beat_dir = cache_dir("keywords", slug)
    cache_path = beat_dir / f"beat_{beat.id:03d}.json"

    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("n_shots") == n_shots and cached.get("shots"):
            return cached

    kw_cfg = cfg["keywords"]
    call = {"anthropic": _call_anthropic, "openai": _call_openai}.get(kw_cfg["provider"])
    if call is None:
        raise ValueError(f"provider desconhecido em config.yaml: {kw_cfg['provider']}")

    prompt = _PROMPT_TEMPLATE.format(
        n_shots=n_shots,
        text=beat.text,
        language_name=_LANGUAGE_NAMES.get(language, "português"),
    )

    analysis = None
    for attempt, attempt_prompt in enumerate((prompt, prompt + _RETRY_SUFFIX)):
        try:
            analysis = _parse_analysis(call(attempt_prompt, kw_cfg["model"]), n_shots)
            break
        except Exception:
            logger.warning(
                "Análise do beat %d falhou (tentativa %d/2).", beat.id, attempt + 1, exc_info=True
            )
    if analysis is None:
        logger.error("Beat %d: as duas tentativas falharam, usando fallback genérico.", beat.id)
        analysis = _fallback_analysis(n_shots)

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
        result = analyze_beat(b, script_slug, n_shots=3)
        print(f"\nbeat {b.id} [{result['type']}]: {b.text[:70]}")
        for i, shot in enumerate(result["shots"]):
            print(f"  shot {i}: {shot['terms']}")
        if result["chart"]:
            print(f"  chart: {result['chart']}")
