import React from "react";
import { AbsoluteFill, OffthreadVideo, staticFile } from "remotion";

/**
 * Fase 1: só renderiza o clipe cortado no tempo certo, sem Ken Burns,
 * transição ou color grading (isso é Fase 2 — ver <VideoComposition>).
 *
 * `clipPath` vem do composition.json como caminho relativo à raiz do
 * projeto (ex: "cache/footage/abcd1234.mp4"); staticFile() resolve isso via
 * o public dir configurado em remotion.config.ts.
 */
export const FootageClip: React.FC<{ clipPath: string }> = ({ clipPath }) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <OffthreadVideo
        src={staticFile(clipPath)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </AbsoluteFill>
  );
};
