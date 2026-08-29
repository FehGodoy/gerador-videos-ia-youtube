import React from "react";
import { AbsoluteFill, interpolate } from "remotion";
import type { TransitionPresentation, TransitionPresentationComponentProps } from "@remotion/transitions";

export type WhipPanProps = {
  /** Quanto a cena estica horizontalmente no auge do movimento (motion blur). */
  stretch?: number;
};

/**
 * `TransitionPresentation` de verdade pro efeito Whip Pan — NÃO é o mesmo
 * componente que `remotion/src/WhipPan.tsx` (aquele é uma peça de
 * demonstração isolada, renderiza as duas cenas ele mesmo com gradientes
 * fixos; uma presentation de transição é chamada duas vezes pelo
 * `TransitionSeries` — uma pro lado que está saindo, outra pro que está
 * entrando — cada uma só embrulhando o `children` real daquele lado).
 *
 * Reaproveita a mesma matemática (translateX + scaleX pra simular motion
 * blur), só que sobre `presentationProgress` (0→1 ao longo da duração
 * configurada da transição) em vez de frame/fps fixos.
 */
const WhipPanPresentation: React.FC<TransitionPresentationComponentProps<WhipPanProps>> = ({
  children,
  presentationProgress,
  presentationDirection,
  passedProps: { stretch = 1.6 },
}) => {
  const translate =
    presentationDirection === "exiting"
      ? interpolate(presentationProgress, [0, 1], [0, -100])
      : interpolate(presentationProgress, [0, 1], [100, 0]);

  const stretchX = interpolate(presentationProgress, [0, 0.5, 1], [1, stretch, 1]);

  return (
    <AbsoluteFill style={{ transform: `translateX(${translate}%) scaleX(${stretchX})` }}>
      {children}
    </AbsoluteFill>
  );
};

export const whipPan = (props?: WhipPanProps): TransitionPresentation<WhipPanProps> => ({
  component: WhipPanPresentation,
  props: props ?? {},
});
