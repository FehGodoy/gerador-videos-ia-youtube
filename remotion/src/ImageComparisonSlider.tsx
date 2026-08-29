import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";

const { fontFamily } = loadFont();
const ACCENT = "#ff8c42";

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático, só disponível pra uso manual/futuro. Adaptado do original:
 * "antes"/"depois" aceitam mídia real; sem clipPath, cai no gradiente do
 * template original.
 *
 * Divisória vertical varre da esquerda pra direita revelando o "depois"
 * por baixo do "antes" (clipPath dos dois lados).
 */
const Side: React.FC<{ clipPath?: string; mediaType?: "image" | "video"; label: string; gradient: string }> = ({
  clipPath,
  mediaType = "image",
  label,
  gradient,
}) =>
  clipPath ? (
    mediaType === "video" ? (
      <OffthreadVideo src={staticFile(clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    ) : (
      <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    )
  ) : (
    <div style={{ width: "100%", height: "100%", background: gradient, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span style={{ fontFamily, color: "white", fontSize: 32, opacity: 0.85 }}>{label}</span>
    </div>
  );

export const ImageComparisonSlider: React.FC<{
  beforeClipPath?: string;
  beforeMediaType?: "image" | "video";
  beforeLabel?: string;
  afterClipPath?: string;
  afterMediaType?: "image" | "video";
  afterLabel?: string;
}> = ({ beforeClipPath, beforeMediaType, beforeLabel = "Antes", afterClipPath, afterMediaType, afterLabel = "Depois" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const dividerPercent = interpolate(frame, [10, fps * 3], [5, 95], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#0f0d0c", alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: "85%", height: "75%", borderRadius: 12, overflow: "hidden", position: "relative" }}>
        <div style={{ position: "absolute", inset: 0 }}>
          {/* "depois" vívido — mesmo laranja de ConceptCard/Timeline/QuoteCard/RankingList */}
          <Side clipPath={afterClipPath} mediaType={afterMediaType} label={afterLabel} gradient={`linear-gradient(135deg, ${ACCENT}, #d9a441, #c76a2e)`} />
        </div>
        <div style={{ position: "absolute", inset: 0, clipPath: `inset(0 ${100 - dividerPercent}% 0 0)` }}>
          {/* "antes" dessaturado — tom terroso apagado, não cinza-azulado frio */}
          <Side clipPath={beforeClipPath} mediaType={beforeMediaType} label={beforeLabel} gradient="linear-gradient(135deg, #241c16, #3a2f27, #4a3f34)" />
        </div>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: `${dividerPercent}%`, width: 3, backgroundColor: "white", zIndex: 2 }}>
          <div
            style={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              width: 28,
              height: 28,
              borderRadius: "50%",
              backgroundColor: "white",
              border: `3px solid ${ACCENT}`,
            }}
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};

export default ImageComparisonSlider;
