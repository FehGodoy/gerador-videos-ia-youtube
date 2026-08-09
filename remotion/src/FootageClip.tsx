import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame } from "remotion";

/**
 * `clipPath` vem do composition.json como caminho relativo à raiz do
 * projeto (ex: "cache/footage/abcd1234.mp4"); staticFile() resolve isso via
 * o public dir configurado em remotion.config.ts.
 *
 * `mediaType === "image"` acontece quando a busca (ou a troca manual na
 * revisão) resolveu numa foto em vez de vídeo — sem Ken Burns a imagem
 * ficaria "morta" na tela por toda a duração do beat, então aplica um zoom
 * lento e contínuo (`scale` via interpolate no frame local).
 */
export const FootageClip: React.FC<{
  clipPath: string;
  mediaType: "video" | "image";
  durationInFrames: number;
}> = ({ clipPath, mediaType, durationInFrames }) => {
  const frame = useCurrentFrame();

  if (mediaType === "image") {
    const scale = interpolate(frame, [0, durationInFrames], [1, 1.08], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
    return (
      <AbsoluteFill style={{ backgroundColor: "black" }}>
        <Img
          src={staticFile(clipPath)}
          style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${scale})` }}
        />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <OffthreadVideo
        src={staticFile(clipPath)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </AbsoluteFill>
  );
};
