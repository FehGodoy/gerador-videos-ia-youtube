# Prompt de Build: Pipeline de Produção de Vídeo Documentário Automatizado

Cole este documento inteiro no Claude Code (ou ferramenta de IA equivalente) como instrução inicial do projeto.

---

## Contexto

Quero construir uma ferramenta pessoal (uso único, não SaaS, sem multi-usuário) que transforma um
roteiro de vídeo já pronto em um vídeo documentário editado, no estilo de canais de "faceless
YouTube" de nível profissional (referência de qualidade: VidRush AI, Kurzgesagt-lite, canais de
Top 10 e documentário).

**Ponto de partida:** eu já tenho o roteiro pronto em texto (não preciso de geração de roteiro por IA).
**Já tenho:** conta com plano ilimitado na API da Cartesia (text-to-speech).
**Sei programar em:** Python e JavaScript — uso Python para a parte de dados/ML do pipeline e
JavaScript/TypeScript (via Remotion) para a etapa de montagem final do vídeo.

**Arquitetura híbrida:** o projeto terá duas partes que se comunicam por um arquivo de dados
intermediário (JSON), não por acoplamento direto de código:
1. Um pipeline Python cuida de tudo que é dados/ML: parsing do roteiro, narração, extração de
   keywords, busca e ranqueamento de footage, transcrição/timestamps. Ao final, ele produz um
   `composition.json` com tudo que a etapa de renderização precisa (caminhos dos clipes escolhidos,
   timestamps de cada beat, texto das legendas por palavra, caminho do áudio final).
2. Um projeto Remotion (Node/TypeScript) lê esse `composition.json` e renderiza o vídeo final,
   aplicando Ken Burns, transições, legendas estilizadas e overlays via componentes React.
3. O script Python orquestrador chama o render do Remotion via `subprocess` no final do pipeline
   (usando o `@remotion/renderer` ou a CLI `npx remotion render`).

**Licença:** como uso individual (pessoa física, não empresa), o Remotion se enquadra na licença
gratuita, inclusive para uso comercial no canal.

## Objetivo de qualidade

O vídeo final NÃO pode parecer um slideshow básico de imagens de stock cortando na narração sem
critério. O padrão de qualidade exigido inclui:

1. Footage/imagens que combinam semanticamente com o que está sendo narrado naquele trecho
   específico (não só por palavra-chave solta)
2. Efeito Ken Burns (zoom/pan lento) em imagens estáticas para dar sensação de movimento
3. Ritmo de corte variável — cortes mais curtos em trechos de tensão/ação, mais longos em
   trechos reflexivos, não um intervalo fixo de N segundos
4. Transições sutis entre clipes (cross-fade curto, não corte seco abrupto sempre)
5. Color grading consistente entre clipes vindos de fontes diferentes, para não parecer
   um mosaico de vídeos com "cara" visual diferente
6. Legendas estilizadas e sincronizadas com precisão de palavra
7. Trilha sonora de fundo com ducking automático (volume da música cai quando há narração)
8. Thumbnail gerada automaticamente ao final, coerente com o tema do vídeo

## Pipeline a ser implementado

Construa isso como uma série de módulos Python independentes, orquestrados por um script principal,
não como um monólito. Cada etapa deve poder rodar isoladamente (útil para debug e reprocessamento
parcial sem refazer tudo).

### 1. Ingestão de roteiro
- Input: arquivo `.txt` ou `.md` com o roteiro final já escrito por mim
- Dividir o roteiro em "beats" (unidades de sentido — geralmente frases ou pequenos parágrafos),
  preservando o texto original para uso posterior nas legendas

### 2. Narração (Cartesia)
- Gerar áudio de narração via API da Cartesia para o roteiro completo
- Extrair/solicitar timestamps por palavra ou por frase (verificar se a API da Cartesia retorna
  isso nativamente; se não, usar Whisper com `word_timestamps=True` sobre o áudio gerado)
- Salvar áudio final em WAV/MP3 de alta qualidade

### 3. Extração de palavras-chave visuais e classificação de beat
- Para cada beat do roteiro, usar um LLM (Claude Haiku ou GPT-4o-mini, para manter custo baixo)
  para gerar 2-4 termos de busca visual em inglês (bancos de stock indexam melhor em inglês),
  priorizando termos concretos e filmáveis (evitar abstrações como "liberdade" — preferir termos
  como "crowd protest street" quando o contexto permitir)
- No mesmo passo, classificar cada beat em um de dois tipos:
  - `concreto`: descreve algo filmável (lugar, objeto, ação, pessoa) — segue o fluxo normal de
    busca de footage (passo 4)
  - `estatistico`: contém dado numérico, percentual, comparação ou tendência (ex: "quedas de mais
    de 20%", "dobrou em três anos") — não faz sentido buscar footage literal para esse tipo de
    frase, então esse beat é sinalizado para receber um gráfico animado em vez de footage
    (ver passo 5)
- Quando o beat for `estatistico`, o LLM também deve extrair os valores estruturados envolvidos
  (label, valor, unidade, e se aplicável um valor de comparação/anterior) em formato JSON, para
  alimentar o gráfico depois — ex: `{"tipo": "queda", "label": "vendas motor a combustão",
  "valor_inicial": 100, "valor_final": 80, "unidade": "%"}`

### 4. Busca e ranqueamento de footage
- Aplica-se apenas aos beats classificados como `concreto` no passo 3 (beats `estatistico` pulam
  esta etapa e vão direto para o `<AnimatedChart>` na composição — ver passo 5)
- Para beats `estatistico`, ainda buscar UM clipe de footage temático (não literal) para servir de
  fundo desfocado atrás do gráfico animado
- Buscar candidatos nas APIs gratuitas do Pexels e Pixabay Video usando os termos do passo 3
- Baixar os N melhores candidatos por beat (thumbnails primeiro, vídeo completo só do escolhido)
- Implementar reranking semântico usando embeddings CLIP: gerar embedding do texto do beat e
  comparar com embedding visual de um frame representativo de cada clipe candidato, escolhendo o
  de maior similaridade em vez de confiar cegamente na ordem de busca por palavra-chave
- Manter um cache local de clipes já baixados para evitar rebaixar o mesmo material em execuções
  futuras

### 5. Montagem e composição (Remotion)
- Projeto Remotion separado (TypeScript), que recebe o `composition.json` gerado pelo pipeline
  Python e renderiza o vídeo final
- Estrutura de componentes React sugerida:
  - `<VideoComposition>`: componente raiz, itera sobre os beats do JSON e monta a sequência
  - `<FootageClip>`: renderiza cada clipe de footage usando `<OffthreadVideo>` do Remotion,
    aplicando Ken Burns via animação de `scale`/`translate` interpolada com `interpolate()` ao
    longo dos frames do clipe (muito mais suave e controlável que o filtro `zoompan` do FFmpeg)
  - `<TransitionSeries>`: usar o pacote `@remotion/transitions` para cross-fade entre clipes
    consecutivos, com duração variável conforme o ritmo do beat
  - `<CaptionOverlay>`: renderiza as legendas estilizadas usando os timestamps por palavra do
    JSON (fonte, contorno, highlight da palavra atual sendo falada — efeito "karaokê" opcional)
  - `<AnimatedChart>`: usado nos beats marcados como `estatistico` pelo passo 3, em vez de
    `<FootageClip>`. Renderiza um gráfico de barra ou linha (via `recharts` ou `@visx`) animando
    do valor inicial ao valor final durante a duração do beat, com o label e o número em destaque.
    Fundo pode ser um frame desfocado/escurecido do footage mais próximo tematicamente (não
    literal), para não deixar a tela "vazia" enquanto o gráfico anima
  - Correção de cor consistente entre clipes: aplicar um filtro CSS (`brightness`/`contrast`/
    `saturate`) uniforme em `<FootageClip>`, com valores levemente ajustados por fonte de vídeo
    se necessário
- O `<VideoComposition>` decide, beat a beat, se renderiza `<FootageClip>` ou `<AnimatedChart>`
  com base no campo `tipo` (`concreto`/`estatistico`) vindo do `composition.json`
- Ritmo de corte variável já vem definido no `composition.json` pelo pipeline Python (duração de
  cada beat calculada a partir do tempo de fala real daquele trecho, não um valor fixo)
- Renderizar via `npx remotion render` (ou `@remotion/renderer` chamado programaticamente),
  disparado como subprocess pelo orquestrador Python ao final da etapa 4

### 6. Legendas (dados)
- Usar Whisper (local, `faster-whisper` para performance) sobre o áudio de narração final para
  gerar timestamps precisos por palavra
- Incluir esses timestamps no `composition.json` — a renderização visual da legenda (fonte,
  estilo, animação) é responsabilidade do componente `<CaptionOverlay>` do Remotion, não do
  Python; o Python só entrega os dados

### 7. Trilha sonora
- Selecionar música de fundo livre de direitos (biblioteca local pré-baixada do YouTube Audio
  Library ou similar, escolhida manualmente por mim de antemão — não precisa ser automático)
- Implementar audio ducking: reduzir volume da música automaticamente nos trechos com narração
  ativa, usando detecção de voz simples ou apenas os timestamps já conhecidos da narração

### 8. Thumbnail
- Gerar um prompt de imagem a partir do tema geral do roteiro
- Chamar uma API de geração de imagem (Flux ou DALL-E) para produzir a thumbnail final

### 9. Orquestração
- Script principal (`main.py` ou `pipeline.py`) que roda as etapas em sequência
- Sistema de cache/checkpoint por etapa (salvar resultado intermediário de cada módulo em disco),
  para que se o passo 6 falhar eu não precise regerar a narração do zero
- Log claro de progresso no terminal

## Estrutura de projeto sugerida

```
video-pipeline/
├── config.yaml                  # chaves de API, parâmetros (duração de corte, etc.)
├── pipeline.py                  # orquestrador Python: roda etapas 1-4 e 6-8, depois chama o Remotion
├── modules/
│   ├── script_parser.py
│   ├── narration.py             # integração Cartesia
│   ├── keyword_extractor.py     # LLM barato
│   ├── footage_search.py        # Pexels/Pixabay + CLIP reranking
│   ├── captions.py              # Whisper (gera dados, não renderiza)
│   ├── audio_mixer.py           # ducking
│   ├── thumbnail.py
│   └── composition_builder.py   # monta o composition.json final
├── cache/                       # footage baixado, embeddings, checkpoints
├── assets/
│   └── music/                   # trilhas pré-selecionadas
├── output/                      # vídeos finais
├── scripts_input/               # onde eu coloco os roteiros prontos
└── remotion/                    # subprojeto Node/TypeScript
    ├── package.json
    ├── remotion.config.ts
    └── src/
        ├── Composition.tsx      # <VideoComposition>: lê composition.json
        ├── FootageClip.tsx      # clipe + Ken Burns + color grading
        ├── AnimatedChart.tsx    # gráfico animado p/ beats estatísticos
        ├── CaptionOverlay.tsx   # legendas estilizadas
        └── index.ts
```

## Fases de implementação (peço que sejam feitas nesta ordem)

1. **Fase 1 – Pipeline mínimo funcional**: passos 1, 2, 4 (sem reranking CLIP, busca simples por
   keyword), 6, e um `<VideoComposition>` no Remotion o mais simples possível (clipes em sequência,
   sem Ken Burns, sem transição, legenda básica sem estilo). Objetivo: produzir um vídeo completo,
   ainda que básico, de ponta a ponta — incluindo a integração Python → composition.json → Remotion
   render funcionando.
2. **Fase 2 – Qualidade visual**: adicionar Ken Burns, cross-fade (`@remotion/transitions`), color
   grading consistente, reranking CLIP na busca de footage, e o componente `<AnimatedChart>` para
   beats classificados como `estatistico` (incluindo a lógica de classificação no passo 3 e a
   extração de valores estruturados).
3. **Fase 3 – Áudio e acabamento**: trilha sonora com ducking, legendas estilizadas com highlight
   por palavra, thumbnail.

Não pule direto para a Fase 3 sem antes ter a Fase 1 rodando de ponta a ponta, incluindo a chamada
do Remotion a partir do Python — quero validar que a integração entre as duas partes funciona antes
de investir tempo em polimento visual.

## Requisitos técnicos finais

- Python 3.11+ para o pipeline de dados
- Node 18+ e TypeScript para o subprojeto Remotion
- Todas as chaves de API via variáveis de ambiente (`.env`), nunca hardcoded, em ambos os lados
- Tratamento de erro em cada módulo Python (se a busca de footage falhar para um beat, logar e usar
  um fallback genérico em vez de quebrar o pipeline inteiro)
- O `composition.json` deve ter um schema bem definido (documentar com um exemplo comentado ou
  TypeScript type/interface compartilhado) para que mudanças no formato não quebrem o lado Remotion
  silenciosamente
- Comentários explicando decisões não óbvias, já que vou manter esse código sozinho
