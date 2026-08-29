import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";

const { fontFamily } = loadFont();

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático, só disponível pra uso manual/futuro. Adaptado do original:
 * é só o overlay (3 gradientes radiais quentes que derivam e somem), pra
 * compor sobre qualquer conteúdo real via `children` — o original tinha um
 * texto de exemplo fixo por baixo, aqui isso vira o fallback quando não
 * tem children (só pra ver o efeito isolado).
 */
export const FilmBurn: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const totalFrames = fps * 3;
  const intensity = interpolate(frame, [0, totalFrames * 0.5, totalFrames], [0, 0.85, 0], {
    extrapolateRight: "clamp",
  });

  const xShift1 = 50 + Math.sin(frame * 0.05) * 30;
  const yShift1 = 50 + Math.cos(frame * 0.04) * 20;
  const xShift2 = 50 + Math.sin(frame * 0.07 + 2) * 25;
  const yShift2 = 50 + Math.cos(frame * 0.06 + 1) * 30;
  const xShift3 = 50 + Math.sin(frame * 0.03 + 4) * 20;
  const yShift3 = 50 + Math.cos(frame * 0.08 + 3) * 15;

  return (
    <AbsoluteFill style={{ backgroundColor: "#0f0d0c" }}>
      {children ?? (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
          <h2 style={{ fontFamily, color: "white", fontSize: 56, fontWeight: 500, margin: 0 }}>Film Burn</h2>
        </AbsoluteFill>
      )}
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at ${xShift1}% ${yShift1}%, rgba(249, 115, 22, ${intensity * 0.7}), transparent 60%)`,
          pointerEvents: "none",
        }}
      />
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at ${xShift2}% ${yShift2}%, rgba(251, 191, 36, ${intensity * 0.5}), transparent 50%)`,
          pointerEvents: "none",
        }}
      />
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at ${xShift3}% ${yShift3}%, rgba(255, 255, 255, ${intensity * 0.3}), transparent 40%)`,
          pointerEvents: "none",
        }}
      />
    </AbsoluteFill>
  );
};

export default FilmBurn;
