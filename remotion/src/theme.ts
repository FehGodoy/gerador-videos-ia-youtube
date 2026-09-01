/**
 * Paleta/identidade visual compartilhada por todo componente do Remotion —
 * fundo de papel + mídia em cards (substituiu o documentário escuro da
 * Fase 5). Centralizado aqui pra não duplicar hex em ~13 arquivos.
 */

// Fundo raiz, pintado uma vez em VideoComposition.tsx — os componentes
// filhos não pintam mais fundo próprio, ficam transparentes por cima dele.
export const PAPER_COLOR = "#faf6ef";

// Texto principal (não preto puro — combina com o tom quente do papel).
export const INK_COLOR = "#1a1512";

// Destaque textual (números, citações, badges) — #ff8c42 original falha
// contraste AA sobre o papel (~2.3:1); este tom passa em texto normal
// (~5.2:1).
export const RUST_ACCENT = "#c2410c";

// Laranja original — só em elementos decorativos de área pequena e
// não-textual (barrinha, borda fina), onde a exigência de contraste não
// se aplica.
export const DECORATIVE_ACCENT = "#ff8c42";

// Sombra de card, em duas camadas (larga e suave, como nas referências do
// usuário) — mesmo valor em todo componente que envolve mídia num card.
export const CARD_SHADOW =
  "0 24px 60px rgba(20,15,10,0.16), 0 8px 20px rgba(20,15,10,0.10)";

export const CARD_RADIUS = 28;
