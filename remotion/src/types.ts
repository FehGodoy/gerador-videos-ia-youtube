/**
 * Espelha composition.schema.json (raiz do projeto) — fonte da verdade do
 * formato. Se o schema mudar, atualize este arquivo junto.
 */

export interface Caption {
  word: string;
  start_seconds: number;
  end_seconds: number;
}

export interface Footage {
  clip_path: string;
  source:
    | "pexels"
    | "pixabay"
    | "wikimedia"
    | "nasa"
    | "youtube"
    | "google_images"
    | "manual"
    | "fallback"
    | "cache";
  media_type: "video" | "image";
  // "parallax_pan" troca o Ken Burns/cover padrão do <FootageClip> pelo
  // efeito <ParallaxPan>. Ausente = "default". Decidido algoritmicamente em
  // composition_builder.py, não pela IA.
  render_style?: "default" | "parallax_pan";
  search_terms: string[];
  // Nota 0-100 que a IA de visão deu a esta mídia (ver footage_ranker).
  // Não é renderizada — serve pra revisão e pro threshold de fallback.
  relevance_score?: number | null;
  ai_reasoning?: string;
  // Só em fontes que exigem crédito (Wikimedia). Não é renderizado na tela —
  // serve pra montar os créditos na descrição do vídeo.
  attribution?: { author: string; license: string; page: string; title: string };
}

// Item de uma cena "gallery" — mesmo formato que Footage, sem
// relevance_score/ai_reasoning/render_style (nota individual e Ken
// Burns/parallax não fazem sentido dentro de uma colagem).
export interface GalleryMediaItem {
  clip_path: string;
  source?: Footage["source"];
  media_type: "video" | "image";
  search_terms: string[];
  attribution?: { author: string; license: string; page: string; title: string };
}

// Fase 5: efeito multi-mídia (Split Screen/Comparison Slider/Gallery Grid/
// Masonry) — decisão semântica do diretor visual (keyword_extractor.py),
// diferente de transition_in/render_style que são algorítmicos.
export interface GalleryData {
  effect: "split_screen" | "comparison_slider" | "gallery_grid" | "masonry";
  items: GalleryMediaItem[];
}

// Presente quando beat.type === "estatistico". Renderizado por <AnimatedChart>.
export interface ChartData {
  tipo: "crescimento" | "queda" | "comparacao" | "destaque";
  label: string;
  // null quando tipo === "destaque" (valor isolado, sem comparação antes/depois)
  valor_inicial: number | null;
  valor_final: number;
  unidade: string;
  // Só usado na montagem (Python), pra ancorar a cena de gráfico no instante
  // em que o dado é falado. Não é lido por nenhum componente.
  trigger?: string;
}

// Fase 4: componentes de motion graphics pra dado estruturado que não é
// número/comparação (isso já é o <AnimatedChart>) — cronologia, citação,
// lista ordenada. Cada "kind" tem seu componente próprio em remotion/src/.
export interface TimelineData {
  label?: string;
  events: { year: string; text: string }[];
}

export interface QuoteData {
  text: string;
  author: string;
  context?: string;
}

export interface RankingData {
  label?: string;
  items: { label: string; value: string }[];
}

export type MotionGraphicData =
  | { kind: "timeline"; data: TimelineData }
  | { kind: "quote"; data: QuoteData }
  | { kind: "ranking"; data: RankingData };

// Um corte visual dentro do beat. Um bloco de narração longo vira vários —
// nenhum passa da duração do clipe que o preenche, senão o último frame
// congela na tela pelo resto do bloco.
export interface Scene {
  start_seconds: number;
  end_seconds: number;
  // "concept" = nenhuma mídia passou do threshold de relevância, ou o diretor
  // classificou o trecho como abstrato; vira card com a frase-chave.
  // "motion_graphic" (Fase 4) = Timeline/QuoteCard/RankingList.
  // "gallery" (Fase 5) = Split Screen/Comparison Slider/Gallery Grid/Masonry.
  kind: "footage" | "chart" | "concept" | "motion_graphic" | "gallery";
  visual_strategy?: "FOOTAGE" | "NEWS" | "IMAGE" | "MOTION_GRAPHIC" | "TEXT";
  // offset dentro do clipe (trimBefore), pra reuso do mesmo clipe não parecer loop
  clip_start_seconds: number;
  // Transição usada pro Remotion entrar nesta cena vindo da anterior.
  // Ausente = "fade" (comportamento de antes da Fase 5). Algorítmico, ver
  // Footage.render_style acima pro mesmo princípio.
  transition_in?: "fade" | "whip_pan" | "film_burn";
  // índice do shot que gerou esta cena — usado só pelo painel (revisão/troca
  // manual de mídia), não é lido por nenhum componente de render
  shot_slot?: number | null;
  footage: Footage | null;
  concept_text?: string;
  motion_graphic?: MotionGraphicData | null;
  gallery?: GalleryData | null;
}

// Selo de informação sobreposto ao footage, no segundo em que o dado é falado.
// Não é cena: vive numa camada acima da TransitionSeries, com tempo absoluto,
// então não interfere nos cortes nem na compensação das transições.
export interface Highlight {
  kind: "numero" | "comparacao" | "termo";
  start_seconds: number;
  end_seconds: number;
  valor?: string;
  unidade?: string;
  label?: string;
  de?: string;
  para?: string;
  termo?: string;
  definicao?: string;
}

export interface Beat {
  id: number;
  text: string;
  start_seconds: number;
  end_seconds: number;
  type: "concreto" | "estatistico";
  // nomes próprios citados no trecho; usados na busca e no ranking (Python),
  // não renderizados
  entities?: string[];
  scenes: Scene[];
  chart: ChartData | null;
  highlights: Highlight[];
  captions: Caption[];
}

export interface CompositionData {
  fps: number;
  width: number;
  height: number;
  audio: {
    path: string;
    duration_seconds: number;
  };
  music: { path: string; volume: number } | null;
  subscribe_popup: SubscribePopupData | null;
  beats: Beat[];
}

export interface SubscribePopupData {
  channel_name: string;
  channel_handle: string;
  avatar_path: string | null;
  cycle_seconds: number;
  offset_seconds: number;
  subscribe_text: string;
  subscribed_text: string;
}

// Usado como defaultProps no Root para o Remotion Studio ter algo válido para
// mostrar antes de um composition.json real ser passado via --props.
export const emptyCompositionData: CompositionData = {
  fps: 30,
  width: 1920,
  height: 1080,
  audio: { path: "", duration_seconds: 1 },
  music: null,
  subscribe_popup: null,
  beats: [],
};
