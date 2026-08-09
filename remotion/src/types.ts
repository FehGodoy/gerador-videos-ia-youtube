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
  source: "pexels" | "pixabay" | "fallback" | "cache";
  search_terms: string[];
}

// Reservado para a Fase 2 (beats do tipo "estatistico"). Não populado na Fase 1.
export interface ChartData {
  tipo: string;
  label: string;
  valor_inicial: number;
  valor_final: number;
  unidade: string;
}

export interface Beat {
  id: number;
  text: string;
  start_seconds: number;
  end_seconds: number;
  type: "concreto" | "estatistico";
  footage: Footage | null;
  chart: ChartData | null;
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
