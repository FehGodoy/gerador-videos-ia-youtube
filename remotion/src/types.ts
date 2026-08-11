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
  source: "pexels" | "pixabay" | "wikimedia" | "fallback" | "cache";
  media_type: "video" | "image";
  search_terms: string[];
  // Só em fontes que exigem crédito (Wikimedia). Não é renderizado na tela —
  // serve pra montar os créditos na descrição do vídeo.
  attribution?: { author: string; license: string; page: string; title: string };
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

// Um corte visual dentro do beat. Um bloco de narração longo vira vários —
// nenhum passa da duração do clipe que o preenche, senão o último frame
// congela na tela pelo resto do bloco.
export interface Scene {
  start_seconds: number;
  end_seconds: number;
  kind: "footage" | "chart";
  // offset dentro do clipe (trimBefore), pra reuso do mesmo clipe não parecer loop
  clip_start_seconds: number;
  footage: Footage | null;
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
  beats: Beat[];
}

// Usado como defaultProps no Root para o Remotion Studio ter algo válido para
// mostrar antes de um composition.json real ser passado via --props.
export const emptyCompositionData: CompositionData = {
  fps: 30,
  width: 1920,
  height: 1080,
  audio: { path: "", duration_seconds: 1 },
  music: null,
  beats: [],
};
