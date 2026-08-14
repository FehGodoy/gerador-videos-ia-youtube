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

# Como cada shot deve ser resolvido visualmente. FOOTAGE/NEWS/IMAGE viram busca
# de mídia (com viés diferente); MOTION_GRAPHIC e TEXT não buscam nada — viram
# gráfico e card na tela.
VISUAL_STRATEGIES = ("FOOTAGE", "NEWS", "IMAGE", "MOTION_GRAPHIC", "TEXT")
# MOTION_GRAPHIC busca mídia junto: a biblioteca de componentes ainda não
# existe (Fase 4), e sem busca esses shots caíam todos em card de texto — num
# teste real isso levou 1/3 do vídeo a virar texto. Enquanto o componente não
# chega, eles renderizam como footage; a estratégia fica gravada na cena pra
# Fase 4 saber onde entrar. Só TEXT dispensa mídia de propósito.
STRATEGIES_THAT_SEARCH = ("FOOTAGE", "NEWS", "IMAGE", "MOTION_GRAPHIC")

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

_PROMPT_TEMPLATE = """Você é DIRETOR DE VÍDEO de um documentário estilo "faceless YouTube". Seu \
trabalho não é achar um vídeo pra cada frase — é decidir a MELHOR FORMA DE COMUNICAR VISUALMENTE \
cada parte da história, como um editor humano faria. Responda com UM ÚNICO objeto JSON, sem \
markdown e sem lista no nível de cima:

{{
  "type": "concreto" ou "estatistico",
  "entities": ["nome próprio citado no trecho", "..."],
  "shots": [
    {{"strategy": "FOOTAGE",
      "terms": ["busca em inglês", "alternativa", "alternativa"],
      "concept_text": "frase curta em {language_name} pra virar card se nada servir"}}
  ],
  "chart": null ou {{
    "tipo": "crescimento" | "queda" | "comparacao" | "destaque",
    "label": "string curta explicando o dado, escrita em {language_name}",
    "valor_inicial": number ou null,
    "valor_final": number,
    "unidade": "string curta, ex: %, anos, mil, x",
    "trigger": "recorte literal do trecho onde esse dado é dito"
  }},
  "highlights": [
    {{"kind": "numero", "trigger": "trecho literal", "valor": "5.000", "unidade": "milhas",
      "label": "texto curto em {language_name}"}},
    {{"kind": "comparacao", "trigger": "trecho literal", "de": "60 dolares", "para": "4.000 dolares",
      "label": "texto curto em {language_name}"}},
    {{"kind": "termo", "trigger": "trecho literal", "termo": "VTEC",
      "definicao": "explicação de até 10 palavras em {language_name}"}}
  ]
}}

CONTEXTO (só pra você entender onde este trecho se encaixa — NÃO analise estes):
- Antes: "{prev_text}"
- Depois: "{next_text}"

Regras:
- "entities": nomes próprios citados no trecho — pessoas, empresas, marcas, produtos, lugares, \
eventos, times, filmes, fatos históricos. Só o que é NOMEADO; não invente. Lista vazia se não \
houver nenhum. Elas são o que faz a busca achar o assunto exato em vez de um genérico parecido.
- "shots": EXATAMENTE {n_shots} itens, na ordem em que os assuntos aparecem no trecho. Cada item \
é uma imagem/vídeo diferente que vai ocupar um pedaço do trecho na tela. Os shots precisam ser \
VISUALMENTE DIFERENTES entre si (assuntos, objetos ou ângulos distintos que o trecho menciona) — \
não repita a mesma ideia com outras palavras.
- "strategy": decida como um editor, POR SHOT. Não assuma que tudo é footage.
    FOOTAGE  — existe cena filmável genérica que ilustra bem (ação, ambiente, profissão).
    NEWS     — o trecho fala de fato/pessoa/evento REAL e específico, que só material de arquivo \
ou foto documental mostra de verdade.
    IMAGE    — uma foto boa comunica melhor que vídeo (objeto específico, retrato, mapa, \
documento). Também quando o assunto é nomeado e vídeo genérico erraria.
    MOTION_GRAPHIC — a informação é um número, série ou comparação; footage não acrescenta nada.
    TEXT     — a ideia é abstrata e nenhuma imagem literal serve; melhor uma frase na tela.
- "terms": 2 a 4 buscas em INGLÊS, da mais específica pra mais genérica (a primeira que trouxer \
resultado é usada). Quando o shot tiver entidade envolvida, INCLUA O NOME dela na primeira busca \
(ex: "Honda HR-V front view", não "small SUV"). Concretas e filmáveis — evite abstrações.
- "concept_text": frase de 3 a 8 palavras em {language_name} resumindo a IDEIA do shot. Vira um \
card na tela quando a estratégia for TEXT, ou quando nenhuma mídia encontrada for boa o bastante. \
Escreva sempre, em todo shot.
- "estatistico": o trecho tem um número, percentual, ano marcante ou índice que vale destacar na \
tela — ex: "caiu 20%", "dobrou em 3 anos", "em 1969", "1º lugar no ranking".
- "concreto": o trecho descreve algo filmável sem um dado numérico central pra destacar.
- Quando "estatistico" com "chart.tipo" "crescimento"/"queda"/"comparacao": tem uma transição ou \
comparação clara (antes → depois) — preencha valor_inicial E valor_final.
- Quando "estatistico" com "chart.tipo" "destaque": só um valor marcante isolado (ano, índice, \
quantidade), sem comparação — valor_inicial fica null, só valor_final.
- Quando "concreto", "chart" é null.
- "chart.trigger" segue a MESMA regra do "trigger" dos highlights descrita abaixo: recorte literal \
de 3 a 8 palavras copiado do trecho, marcando onde o dado do gráfico é falado. É por ele que o \
gráfico entra na hora certa; sem um trigger válido ele cai no começo do bloco.

Sobre "highlights" — são selos que aparecem sobrepostos ao vídeo NO SEGUNDO EXATO em que a \
informação é falada, para o trecho ficar mais informativo:
- Gere APROXIMADAMENTE {n_highlights} deles, espalhados ao longo do trecho (não amontoe no começo).
- "trigger" é a REGRA MAIS IMPORTANTE: precisa ser um recorte LITERAL de 3 a 8 palavras copiado \
exatamente do trecho abaixo, incluindo a informação. É por ele que o selo é posicionado no tempo; \
se você reescrever, parafrasear ou traduzir, o selo é descartado. Copie e cole do texto.
- Cada trigger deve ser único dentro do trecho e aparecer só uma vez no texto.
- "numero": um valor citado que vale fixar na tela (quantia, distância, prazo, percentual).
- "comparacao": dois valores contrapostos na fala (antes/depois, barato/caro, com/sem).
- "termo": jargão técnico ou sigla que o espectador pode não conhecer.
- NÃO repita aqui o dado que você já colocou em "chart".
- Se o trecho não tiver nada que valha destacar, devolva "highlights": [].
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


_HIGHLIGHT_FIELDS = {
    "numero": ("valor", "label"),
    "comparacao": ("de", "para", "label"),
    "termo": ("termo", "definicao"),
}


def _parse_highlights(raw: object, beat_text: str) -> list[dict]:
    """Valida os destaques e joga fora os que não dá pra posicionar no tempo.

    Um destaque só serve se o `trigger` existir literalmente na narração — é
    por ele que o composition_builder acha o segundo exato da fala nos
    timestamps por palavra. Trigger parafraseado é descartado aqui em vez de
    virar um selo no lugar errado.
    """
    if not isinstance(raw, list):
        return []
    normalized_text = " ".join(beat_text.lower().split())

    clean: list[dict] = []
    seen_triggers: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        fields = _HIGHLIGHT_FIELDS.get(kind)
        if fields is None:
            continue
        trigger = item.get("trigger")
        if not isinstance(trigger, str) or not trigger.strip():
            continue
        normalized_trigger = " ".join(trigger.lower().split())
        if normalized_trigger not in normalized_text:
            logger.info("Destaque descartado, trigger não está na narração: %r", trigger)
            continue
        if normalized_trigger in seen_triggers:
            continue
        values = {f: item.get(f) for f in fields}
        if any(not isinstance(v, str) or not v.strip() for v in values.values()):
            continue
        seen_triggers.add(normalized_trigger)
        clean.append({"kind": kind, "trigger": trigger.strip(), **values})
    return clean


def _parse_analysis(raw_response: str, n_shots: int, beat_text: str = "") -> dict:
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
        strategy = shot.get("strategy")
        if strategy not in VISUAL_STRATEGIES:
            strategy = "FOOTAGE"  # estratégia desconhecida cai no comportamento antigo
        concept = shot.get("concept_text")
        parsed_shots.append(
            {
                "terms": clean,
                "strategy": strategy,
                "concept_text": concept.strip() if isinstance(concept, str) else "",
            }
        )

    entities = analysis.get("entities")
    entities = (
        [e.strip() for e in entities if isinstance(e, str) and e.strip()]
        if isinstance(entities, list)
        else []
    )

    if analysis["type"] == "estatistico" and not isinstance(analysis.get("chart"), dict):
        raise ValueError("type='estatistico' mas chart não veio como objeto")

    # o modelo às vezes devolve mais/menos shots que o pedido; corta o excesso
    # e completa repetindo os últimos, em vez de descartar uma resposta boa.
    while len(parsed_shots) < n_shots:
        parsed_shots.append(dict(parsed_shots[len(parsed_shots) % len(shots)]))

    return {
        "type": analysis["type"],
        "entities": entities,
        "shots": parsed_shots[:n_shots],
        "chart": analysis.get("chart") if analysis["type"] == "estatistico" else None,
        "highlights": _parse_highlights(analysis.get("highlights"), beat_text),
        "n_shots": n_shots,
    }


def _fallback_analysis(n_shots: int) -> dict:
    return {
        "type": "concreto",
        "entities": [],
        "shots": [
            {"terms": list(FALLBACK_TERMS), "strategy": "FOOTAGE", "concept_text": ""}
            for _ in range(n_shots)
        ],
        "chart": None,
        "highlights": [],
        "n_shots": n_shots,
    }


def _context_snippet(text: str | None, limit: int = 240) -> str:
    """Resumo curto do beat vizinho pro diretor entender onde a cena se encaixa.

    Curto de propósito: é contexto, não conteúdo a analisar — mandar o vizinho
    inteiro dobraria o custo da chamada e faria o modelo confundir os trechos.
    """
    if not text:
        return "(início do vídeo)"
    flat = " ".join(text.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def analyze_beat(
    beat: Beat,
    slug: str,
    n_shots: int = 1,
    language: str = "pt",
    n_highlights: int = 0,
    prev_text: str | None = None,
    next_text: str | None = None,
) -> dict:
    """Analisa um beat: `n_shots` ideias visuais distintas (cada uma com seus
    termos de busca) + classificação concreto/estatístico + dados de gráfico
    quando aplicável. Cache em disco por beat.

    `n_shots` e `n_highlights` vêm da duração do beat (ver composition_builder)
    — beats longos precisam de mais material visual pra não ficar um clipe só
    na tela, e de mais selos de informação pra não ficarem "secos". O cache
    guarda os dois valores e é refeito se algum mudar (ex: a narração foi
    regerada numa velocidade diferente e o beat encolheu/cresceu).

    Retorna {"type": "concreto"|"estatistico", "shots": [{"terms": [...]}, ...],
    "chart": None | {...}, "highlights": [...], "n_shots": int}.
    """
    cfg = load_config()
    beat_dir = cache_dir("keywords", slug)
    cache_path = beat_dir / f"beat_{beat.id:03d}.json"

    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        fresh = cached.get("n_shots") == n_shots and cached.get("n_highlights") == n_highlights
        if fresh and cached.get("shots"):
            return cached

    kw_cfg = cfg["keywords"]
    call = {"anthropic": _call_anthropic, "openai": _call_openai}.get(kw_cfg["provider"])
    if call is None:
        raise ValueError(f"provider desconhecido em config.yaml: {kw_cfg['provider']}")

    prompt = _PROMPT_TEMPLATE.format(
        n_shots=n_shots,
        n_highlights=n_highlights,
        text=beat.text,
        language_name=_LANGUAGE_NAMES.get(language, "português"),
        prev_text=_context_snippet(prev_text),
        next_text=_context_snippet(next_text, 240) if next_text else "(fim do vídeo)",
    )

    analysis = None
    for attempt, attempt_prompt in enumerate((prompt, prompt + _RETRY_SUFFIX)):
        try:
            analysis = _parse_analysis(call(attempt_prompt, kw_cfg["model"]), n_shots, beat.text)
            break
        except Exception:
            logger.warning(
                "Análise do beat %d falhou (tentativa %d/2).", beat.id, attempt + 1, exc_info=True
            )
    if analysis is None:
        logger.error("Beat %d: as duas tentativas falharam, usando fallback genérico.", beat.id)
        analysis = _fallback_analysis(n_shots)

    analysis["n_highlights"] = n_highlights
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
        result = analyze_beat(b, script_slug, n_shots=3, n_highlights=3)
        print(f"\nbeat {b.id} [{result['type']}]: {b.text[:70]}")
        if result["entities"]:
            print(f"  entidades: {result['entities']}")
        for i, shot in enumerate(result["shots"]):
            print(f"  shot {i} [{shot['strategy']}]: {shot['terms']}")
        if result["chart"]:
            print(f"  chart: {result['chart']}")
        for h in result["highlights"]:
            print(f"  destaque [{h['kind']}] em {h['trigger']!r}")
