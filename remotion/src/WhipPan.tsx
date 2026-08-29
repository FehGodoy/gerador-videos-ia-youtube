import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático, só disponível pra uso manual/futuro (ex: como transição
 * entre dois clipes específicos, no lugar do crossfade padrão de
 * VideoComposition.tsx). Adaptado do original: as duas cenas aceitam mídia
 * real; sem clipPath, cai no gradiente + rótulo do template original.
 *
 * Cena A sai rápido pra esquerda enquanto cena B entra pela direita, com
 * scaleX esticando as duas durante o movimento pra simular motion blur.
 */
const Scene: React.FC<{ clipPath?: string; mediaType?: "image" | "video"; label: string; gradient: string; translateX: number; stretchX: number }> = ({
  clipPath,
  mediaType = "image",
  label,
  gradient,
  translateX,
  stretchX,
}) => (
  <div style={{ position: "absolute", inset: 0, overflow: "hidden", transform: `translateX(${translateX}%) scaleX(${stretchX})` }}>
    {clipPath ? (
      mediaType === "video" ? (
        <OffthreadVideo src={staticFile(clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      )
    ) : (
      <div style={{ width: "100%", height: "100%", background: gradient, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <h2 style={{ color: "white", fontSize: 56, fontWeight: 400, margin: 0 }}>{label}</h2>
      </div>
    )}
  </div>
);

export const WhipPan: React.FC<{
  fromClipPath?: string;
  fromMediaType?: "image" | "video";
  fromLabel?: string;
  toClipPath?: string;
  toMediaType?: "image" | "video";
  toLabel?: string;
}> = ({ fromClipPath, fromMediaType, fromLabel = "Cena A", toClipPath, toMediaType, toLabel = "Cena B" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const panStart = fps * 1;
  const panEnd = fps * 1.4;

  const translateFrom = interpolate(frame, [panStart, panEnd], [0, -100], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const translateTo = interpolate(frame, [panStart, panEnd], [100, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const stretchX = interpolate(frame, [panStart, (panStart + panEnd) / 2, panEnd], [1, 1.6, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#111827" }}>
      <Scene clipPath={fromClipPath} mediaType={fromMediaType} label={fromLabel} gradient="linear-gradient(135deg, #1e3a5f, #111827)" translateX={translateFrom} stretchX={stretchX} />
      <Scene clipPath={toClipPath} mediaType={toMediaType} label={toLabel} gradient="linear-gradient(135deg, #3b1f5e, #111827)" translateX={translateTo} stretchX={stretchX} />
    </AbsoluteFill>
  );
};

export default WhipPan;
