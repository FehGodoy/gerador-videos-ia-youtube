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
 *
 * Corrigido depois de ligar no pipeline: a 1ª versão usava objectFit
 * "cover" puro, revertendo o fix de FootageClip.tsx (commit 8138fc3) que
 * evita cortar/dar zoom pesado em mídia com proporção diferente de 16:9.
 * Agora segue o mesmo padrão: imagem usa fundo desfocado (barato, é a
 * mesma imagem estática) + a foto real em "contain" sofrendo o pan; vídeo
 * usa fundo simples (sem desfoque de verdade, decodificar 2x custaria caro
 * no render) + o vídeo real em "contain".
 */
export const ParallaxPan: React.FC<{
  clipPath: string;
  mediaType?: "image" | "video";
  durationInFrames: number;
  direction?: Direction;
  scale?: number;
  blurredBackgroundPath?: string;
}> = ({ clipPath, mediaType = "image", durationInFrames, direction = "left-right", scale = 1.2, blurredBackgroundPath }) => {
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
    <AbsoluteFill style={{ backgroundColor: "black", overflow: "hidden" }}>
      {mediaType === "video" ? (
        <>
          <AbsoluteFill style={{ background: "radial-gradient(circle, #1c1c1c 0%, #000 100%)" }} />
          <OffthreadVideo
            src={staticFile(clipPath)}
            muted
            style={{ position: "absolute", width: "100%", height: "100%", objectFit: "contain", transform: panTransform }}
          />
        </>
      ) : (
        <>
          {/* blurredBackgroundPath pré-computado em Python (modules/
              image_effects.py) — ver o mesmo comentário em FootageClip.tsx.
              Ausente = cai no blur ao vivo (composition.json antigo). */}
          <Img
            src={staticFile(blurredBackgroundPath ?? clipPath)}
            style={
              blurredBackgroundPath
                ? { position: "absolute", width: "100%", height: "100%", objectFit: "cover" }
                : {
                    position: "absolute",
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    filter: "blur(60px)",
                    transform: "scale(1.15)",
                  }
            }
          />
          <Img
            src={staticFile(clipPath)}
            style={{ position: "absolute", width: "100%", height: "100%", objectFit: "contain", transform: panTransform }}
          />
        </>
      )}
    </AbsoluteFill>
  );
};

export default ParallaxPan;
