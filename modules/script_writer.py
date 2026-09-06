"""
Geração automática de roteiro no ESTILO de um canal — a partir de um TEMA
(a IA usa conhecimento geral) ou de uma TRANSCRIÇÃO de outro vídeo (.txt,
adaptada/reescrita, nunca copiada literalmente). O "estilo do canal" é
few-shot puro: roteiros de exemplo que o usuário já escreveu
(webapp/channels.py::script_examples), não uma descrição de regras — mais
fiel ao jeito real dele escrever.

Devolve só o TEXTO já fatiado em blocos (mesmo grão que o usuário cola
manualmente hoje em `#block-text`) — não sabe nada de slug/voz/narração.
Quem transforma cada bloco em narração de verdade (e, encadeado nisso,
dispara a geração automática de imagem) é o front-end
(webapp/static/app.js::createNarrationBlock, chamado em loop).
"""
from __future__ import annotations

import json
import logging
import time

from modules.config import load_config
from modules.keyword_extractor import _LANGUAGE_NAMES, _call_anthropic, _call_openai

logger = logging.getLogger(__name__)

# Resposta é um roteiro inteiro (podem ser milhares de palavras) — bem
# maior que o teto de 4000 usado pelas outras chamadas de
# keyword_extractor.py (dimensionado pra uma análise de beat, não um
# roteiro completo). _call_anthropic/_call_openai aceitam max_tokens
# opcional exatamente pra permitir esse teto maior sem mudar quem já usa
# o padrão.
_SCRIPT_MAX_TOKENS = 8000

# ~150 palavras/minuto é uma estimativa comum de ritmo de narração falada
# — usada só como guia de tamanho pro prompt, não precisa ser exata.
_WORDS_PER_MINUTE = 150
_DEFAULT_TARGET_MINUTES = 10.0

_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 3

_RETRY_SUFFIX = (
    "\n\nATENÇÃO: sua resposta anterior não era um JSON válido. Responda AGORA apenas com o "
    "objeto JSON pedido, começando com { e terminando com }, sem nenhum texto antes ou depois, "
    "sem markdown."
)

_SCRIPT_PROMPT_TEMPLATE = """Você é um roteirista escrevendo um roteiro de vídeo documentário estilo \
"faceless YouTube", em {language_name}, no ESTILO de um canal específico.

REGRA CRÍTICA DE IDIOMA (a mais importante desta tarefa — respostas erram nisso com frequência, \
preste atenção mesmo que a transcrição-fonte abaixo esteja em outro idioma e você acabe "pensando" \
nele agora): o roteiro que você escrever tem que estar 100% em {language_name}, do início ao fim — \
mesmo quando o conteúdo de origem (a transcrição de outro vídeo, se for o caso) estiver num idioma \
diferente. Leia a fonte no idioma original dela, mas ESCREVA o roteiro novo inteiro em \
{language_name}, sem misturar nem uma frase no idioma da fonte.
  ERRADO: a transcrição-fonte estava em outro idioma e alguma frase (ou palavra solta) do roteiro \
final saiu nesse idioma da fonte em vez de {language_name}.
  CERTO: ler o conteúdo no idioma original da fonte, mas escrever o roteiro inteiro em \
{language_name}, sem nenhuma frase remanescente no idioma da fonte.

Abaixo estão roteiros de EXEMPLO que esse canal já publicou — imite o tom, o ritmo das frases, a \
estrutura de abertura/fechamento e o tipo de vocabulário usado, mas escreva um roteiro TOTALMENTE NOVO, \
nunca reaproveitando frases dos exemplos:

{examples_block}

{source_instruction}

O roteiro completo deve ter aproximadamente {target_words} palavras no total (~{target_minutes:.0f} \
minutos de narração falada).

Responda com UM ÚNICO objeto JSON, sem markdown:
{{"blocks": ["texto do primeiro bloco...", "texto do segundo bloco...", ...]}}

Cada item de "blocks" é um BLOCO de narração contínua (um trecho que seria narrado de uma vez, tipo um \
parágrafo grande) — divida o roteiro em blocos de tamanho parecido, o suficiente pra cobrir a duração \
pedida (normalmente entre 4 e 8 blocos). Escreva só o texto que será narrado, sem indicações de cena, \
títulos ou marcações."""


def _parse_script(raw_response: str, min_blocks: int = 1) -> list[str] | None:
    text = raw_response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    data = json.loads(text)
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return None
    clean = [b.strip() for b in blocks if isinstance(b, str) and b.strip()]
    if len(clean) < min_blocks:
        return None
    return clean


def generate_script(
    examples: list[str],
    language: str,
    *,
    topic: str | None = None,
    transcript: str | None = None,
    target_minutes: float | None = None,
) -> list[str]:
    """Gera um roteiro novo no estilo do canal e devolve já fatiado em
    blocos. Exige exatamente um de `topic`/`transcript` — o outro tem que
    vir `None`."""
    if bool(topic) == bool(transcript):
        raise ValueError("Informe exatamente um dos dois: topic OU transcript.")

    cfg = load_config()
    kw_cfg = cfg["keywords"]
    call = {"anthropic": _call_anthropic, "openai": _call_openai}.get(kw_cfg["provider"])
    if call is None:
        raise RuntimeError(f"Provider de LLM desconhecido em config.yaml: {kw_cfg['provider']!r}")

    minutes = target_minutes or _DEFAULT_TARGET_MINUTES
    target_words = round(minutes * _WORDS_PER_MINUTE)

    examples_block = (
        "\n\n".join(f"--- Exemplo {i + 1} ---\n{ex}" for i, ex in enumerate(examples))
        if examples
        else "(nenhum exemplo fornecido ainda pra este canal — escreva num tom documentário neutro, direto ao ponto.)"
    )
    if topic:
        source_instruction = f"Escreva sobre o seguinte tema:\n{topic}"
    else:
        source_instruction = (
            "Use as informações abaixo (transcrição de outro vídeo) SOMENTE como referência de "
            "conteúdo/fatos — é PROIBIDO copiar frases literais dela; reescreva tudo com suas próprias "
            "palavras, seguindo o estilo dos exemplos acima:\n\n" + transcript
        )

    prompt = _SCRIPT_PROMPT_TEMPLATE.format(
        language_name=_LANGUAGE_NAMES.get(language, "português"),
        examples_block=examples_block,
        source_instruction=source_instruction,
        target_words=target_words,
        target_minutes=minutes,
    )

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(_RETRY_DELAY_SECONDS * attempt)
        attempt_prompt = prompt if attempt == 0 else prompt + _RETRY_SUFFIX
        try:
            raw = call(attempt_prompt, kw_cfg["model"], _SCRIPT_MAX_TOKENS)
            blocks = _parse_script(raw)
            if blocks:
                return blocks
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Geração de roteiro falhou (tentativa %d/%d): %s",
                attempt + 1, _MAX_ATTEMPTS, exc, exc_info=True,
            )

    raise RuntimeError(f"Falha ao gerar roteiro: {last_error or 'resposta da IA não veio no formato esperado'}")
