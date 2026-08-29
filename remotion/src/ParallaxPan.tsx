import React from "react";
import { AbsoluteFill, Easing, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame } from "remotion";

type Direction = "left-right" | "right-left" | "top-bottom" | "bottom-top";

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático, só disponível pra uso manual/futuro.
 *
 * Reescrito na adaptação: o original usava `next/image` + `@keyframes` CSS
 * com `animation: ... infinite alternate` — não funciona aqui (nem o
 * next/image existe fora de um app Next.js, nem uma animação CSS por
 * tempo real é determinística o bastante pro Remotion renderizar o mesmo
 * frame duas vezes igual, exigido pro render server-side). Reescrito com
 * `interpolate` sobre `useCurrentFrame`, um passe só (sem "alternate" —
 * aqui a cena toca uma vez, não fica em loop) do início ao fim do shot.
 */
export const ParallaxPan: React.FC<{
  clipPath: string;
  mediaType?: "image" | "video";
  durationInFrames: number;
  direction?: Direction;
  scale?: number;
}> = ({ clipPath, mediaType = "image", durationInFrames, direction = "left-right", scale = 1.2 }) => {
  const frame = useCurrentFrame();

  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.quad),
  });

  // -20% é o mesmo deslocamento do template original; a direção decide só
  // o eixo e o sentido (invertido pra "-right-left"/"bottom-top").
  const shift = interpolate(progress, [0, 1], direction.startsWith("right") || direction.startsWith("bottom") ? [-20, 0] : [0, -20]);
  const isVertical = direction === "top-bottom" || direction === "bottom-top";
  const transform = `translate${isVertical ? "Y" : "X"}(${shift}%) scale(${scale})`;

  return (
    <AbsoluteFill style={{ backgroundColor: "black", overflow: "hidden" }}>
      {mediaType === "video" ? (
        <OffthreadVideo src={staticFile(clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover", transform }} />
      ) : (
        <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover", transform }} />
      )}
    </AbsoluteFill>
  );
};

export default ParallaxPan;
