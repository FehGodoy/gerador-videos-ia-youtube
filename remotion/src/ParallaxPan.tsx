import React from "react";
import { AbsoluteFill, Easing, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame } from "remotion";
import { CARD_RADIUS, CARD_SHADOW } from "./theme";

type Direction = "left-right" | "right-left" | "top-bottom" | "bottom-top";

const CARD_INSET = 90;

/**
 * Efeito de câmera (pan + leve zoom) sobre um shot único, ligado no
 * pipeline via `render_style: "parallax_pan"` (decidido algoritmicamente
 * em `composition_builder.py`).
 *
 * Diferente de `FootageClip.tsx` (que agora molda o card ao formato real
 * da mídia, sem quadro fixo): aqui o card usa um quadro FIXO com
 * `objectFit: "cover"` — o movimento de câmera implica ver uma janela
 * móvel sobre uma imagem maior, então o corte de "cover" é esperado (não
 * é um bug a evitar como seria numa exibição estática). Isso também
 * elimina a necessidade de um fundo desfocado atrás: a mídia sempre
 * preenche o quadro inteiro, não sobra vão pra preencher.
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
  const panTransform = `translate${isVertical ? "Y" : "X"}(${shift}%) scale(${scale})`;

  return (
    <AbsoluteFill
      style={{
        inset: CARD_INSET,
        borderRadius: CARD_RADIUS,
        overflow: "hidden",
        boxShadow: CARD_SHADOW,
        backgroundColor: "#000",
      }}
    >
      {mediaType === "video" ? (
        <OffthreadVideo
          src={staticFile(clipPath)}
          muted
          style={{ position: "absolute", width: "100%", height: "100%", objectFit: "cover", transform: panTransform }}
        />
      ) : (
        <Img
          src={staticFile(clipPath)}
          style={{ position: "absolute", width: "100%", height: "100%", objectFit: "cover", transform: panTransform }}
        />
      )}
    </AbsoluteFill>
  );
};

export default ParallaxPan;
