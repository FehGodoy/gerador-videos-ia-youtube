import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import { CARD_RADIUS, CARD_SHADOW, DECORATIVE_ACCENT, INK_COLOR } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["500"], subsets: ["latin", "latin-ext"] });

/**
 * Fase 5, effect="comparison_slider": divisória vertical varre da esquerda
 * pra direita revelando o "depois" por baixo do "antes" (mesma foto, dois
 * momentos, ou duas fotos comparadas) — decidido pela IA em
 * keyword_extractor.py. Card único (cantos arredondados + sombra) sobre o
 * papel compartilhado.
 */
const Side: React.FC<{ clipPath?: string; mediaType?: "image" | "video"; label: string }> = ({
  clipPath,
  mediaType = "image",
  label,
}) =>
  clipPath ? (
    mediaType === "video" ? (
      <OffthreadVideo src={staticFile(clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    ) : (
      <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    )
  ) : (
    <div style={{ width: "100%", height: "100%", background: "rgba(26,21,18,0.06)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span style={{ fontFamily, color: INK_COLOR, fontSize: 32, opacity: 0.75 }}>{label}</span>
    </div>
  );

export const ImageComparisonSlider: React.FC<{
  beforeClipPath?: string;
  beforeMediaType?: "image" | "video";
  beforeLabel?: string;
  afterClipPath?: string;
  afterMediaType?: "image" | "video";
  afterLabel?: string;
  fillScreen?: boolean;
}> = ({
  beforeClipPath,
  beforeMediaType,
  beforeLabel = "Antes",
  afterClipPath,
  afterMediaType,
  afterLabel = "Depois",
  fillScreen,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const dividerPercent = interpolate(frame, [10, fps * 3], [5, 95], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          width: fillScreen ? "100%" : "85%",
          height: fillScreen ? "100%" : "75%",
          borderRadius: fillScreen ? 0 : CARD_RADIUS,
          overflow: "hidden",
          boxShadow: fillScreen ? "none" : CARD_SHADOW,
          position: "relative",
        }}
      >
        <div style={{ position: "absolute", inset: 0 }}>
          <Side clipPath={afterClipPath} mediaType={afterMediaType} label={afterLabel} />
        </div>
        <div style={{ position: "absolute", inset: 0, clipPath: `inset(0 ${100 - dividerPercent}% 0 0)` }}>
          <Side clipPath={beforeClipPath} mediaType={beforeMediaType} label={beforeLabel} />
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
              border: `3px solid ${DECORATIVE_ACCENT}`,
            }}
          />
        </div>
      </div>
    </AbsoluteFill>
  );
};

export default ImageComparisonSlider;
