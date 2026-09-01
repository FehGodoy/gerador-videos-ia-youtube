import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import { CARD_RADIUS, CARD_SHADOW, INK_COLOR } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["500"], subsets: ["latin", "latin-ext"] });

const CARD_INSET = 90;
const GAP = 40;

/**
 * Fase 5, effect="split_screen": dois shots relacionados lado a lado —
 * decidido pela IA em keyword_extractor.py quando o trecho compara ou
 * contrasta duas coisas.
 *
 * Cada painel vira um card (cantos arredondados + sombra) sobre o papel
 * compartilhado, com um espaço entre os dois em vez de tela cheia dividida
 * por uma linha — a "linha divisória" fazia sentido quando os painéis
 * cobriam a tela inteira, sem sentido mais com os dois já visualmente
 * separados como cards.
 */
const Panel: React.FC<{
  clipPath?: string;
  mediaType?: "image" | "video";
  label: string;
  translateX: number;
}> = ({ clipPath, mediaType = "image", label, translateX }) => (
  <div
    style={{
      flex: 1,
      height: "100%",
      overflow: "hidden",
      borderRadius: CARD_RADIUS,
      boxShadow: CARD_SHADOW,
      transform: `translateX(${translateX}%)`,
      position: "relative",
      backgroundColor: "rgba(26,21,18,0.06)",
    }}
  >
    {clipPath ? (
      mediaType === "video" ? (
        <OffthreadVideo src={staticFile(clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      )
    ) : (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <h2 style={{ fontFamily, color: INK_COLOR, fontSize: 48, fontWeight: 500, margin: 0 }}>{label}</h2>
      </div>
    )}
  </div>
);

export const SplitScreen: React.FC<{
  leftClipPath?: string;
  leftMediaType?: "image" | "video";
  leftLabel?: string;
  rightClipPath?: string;
  rightMediaType?: "image" | "video";
  rightLabel?: string;
}> = ({ leftClipPath, leftMediaType, leftLabel = "Painel A", rightClipPath, rightMediaType, rightLabel = "Painel B" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const leftSlide = spring({ frame, fps, config: { damping: 15, stiffness: 80 } });
  const rightSlide = spring({ frame: frame - 5, fps, config: { damping: 15, stiffness: 80 } });
  const leftTranslateX = interpolate(leftSlide, [0, 1], [-30, 0]);
  const rightTranslateX = interpolate(rightSlide, [0, 1], [30, 0]);

  return (
    <AbsoluteFill style={{ inset: CARD_INSET, flexDirection: "row", gap: GAP }}>
      <Panel clipPath={leftClipPath} mediaType={leftMediaType} label={leftLabel} translateX={leftTranslateX} />
      <Panel clipPath={rightClipPath} mediaType={rightMediaType} label={rightLabel} translateX={rightTranslateX} />
    </AbsoluteFill>
  );
};

export default SplitScreen;
