import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/**
 * `clipPath` vem do composition.json como caminho relativo à raiz do
 * projeto (ex: "cache/footage/abcd1234.mp4"); staticFile() resolve isso via
 * o public dir configurado em remotion.config.ts.
 *
 * `mediaType === "image"` acontece quando a busca (ou a troca manual na
 * revisão) resolveu numa foto em vez de vídeo — sem Ken Burns a imagem
 * ficaria "morta" na tela, então aplica um zoom lento e contínuo.
 *
 * `clipStartSeconds` (trimBefore) existe porque um bloco de narração longo
 * reutiliza o mesmo clipe em várias cenas — começar cada reuso de um ponto
 * diferente evita a sensação de loop.
 */
export const FootageClip: React.FC<{
  clipPath: string;
  mediaType: "video" | "image";
  clipStartSeconds: number;
  durationInFrames: number;
}> = ({ clipPath, mediaType, clipStartSeconds, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

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
        trimBefore={Math.max(0, Math.round(clipStartSeconds * fps))}
        // A única trilha do vídeo é a narração. Clipe de stock costuma vir sem
        // áudio, então isso nunca fez falta — mas material de arquivo vem com
        // som embutido e falaria por cima da narração.
        muted
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </AbsoluteFill>
  );
};
