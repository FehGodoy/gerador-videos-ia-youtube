import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import type { Highlight } from "./types";

const { fontFamily } = loadFont();

const ACCENT = "#ff8c42";
const EXIT_SECONDS = 0.45;

/**
 * Selo de informação sobreposto ao footage — aparece no segundo em que o dado
 * é falado (ver _anchor_highlights em modules/composition_builder.py).
 *
 * Fica no terço inferior esquerdo de propósito: o centro é do <AnimatedChart>,
 * que toma a tela inteira. Assim os dois nunca disputam o mesmo espaço, e o
 * selo acrescenta informação sem transformar o vídeo em slides.
 */
export const HighlightOverlay: React.FC<{ highlight: Highlight; durationInFrames: number }> = ({
  highlight,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // entrada com mola (deslize + fade) e saída curta por fade
  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.6 }, durationInFrames: 18 });
  const exitStart = durationInFrames - Math.round(EXIT_SECONDS * fps);
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = Math.min(enter, exit);
  const slide = interpolate(enter, [0, 1], [-44, 0]);

  return (
    <AbsoluteFill
      style={{
        fontFamily,
        justifyContent: "flex-end",
        alignItems: "flex-start",
        padding: "0 0 96px 96px",
      }}
    >
      <div
        style={{
          opacity,
          transform: `translateX(${slide}px)`,
          background: "rgba(14, 12, 11, 0.82)",
          borderLeft: `6px solid ${ACCENT}`,
          borderRadius: 10,
          padding: "22px 34px 24px 28px",
          maxWidth: 1150,
          boxShadow: "0 18px 48px rgba(0,0,0,0.45)",
        }}
      >
        <HighlightBody highlight={highlight} />
      </div>
    </AbsoluteFill>
  );
};

const LABEL_STYLE: React.CSSProperties = {
  fontSize: 27,
  fontWeight: 500,
  color: "rgba(255,255,255,0.82)",
  marginTop: 8,
};

const HighlightBody: React.FC<{ highlight: Highlight }> = ({ highlight }) => {
  if (highlight.kind === "comparacao") {
    return (
      <>
        {/* whiteSpace nowrap em cada valor: sem isso "150,000 miles" quebrava
            no meio e a seta ficava perdida entre as duas linhas. O flexWrap
            deixa o segundo valor descer inteiro quando os dois não cabem. */}
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            flexWrap: "wrap",
            columnGap: 18,
            rowGap: 2,
            color: "white",
          }}
        >
          <span
            style={{
              fontSize: 46,
              fontWeight: 700,
              color: "rgba(255,255,255,0.55)",
              whiteSpace: "nowrap",
            }}
          >
            {highlight.de}
          </span>
          <span style={{ fontSize: 34, color: ACCENT }}>→</span>
          <span
            style={{ fontSize: 58, fontWeight: 700, letterSpacing: -1, whiteSpace: "nowrap" }}
          >
            {highlight.para}
          </span>
        </div>
        {highlight.label && <div style={LABEL_STYLE}>{highlight.label}</div>}
      </>
    );
  }

  if (highlight.kind === "termo") {
    return (
      <>
        <div style={{ fontSize: 52, fontWeight: 700, color: "white", letterSpacing: -0.5 }}>
          {highlight.termo}
        </div>
        {highlight.definicao && <div style={LABEL_STYLE}>{highlight.definicao}</div>}
      </>
    );
  }

  return (
    <>
      <div
        style={{
          fontSize: 72,
          fontWeight: 700,
          color: "white",
          lineHeight: 1,
          letterSpacing: -1.5,
        }}
      >
        {highlight.valor}
        {highlight.unidade && (
          <span style={{ fontSize: 38, fontWeight: 600, color: ACCENT, marginLeft: 10 }}>
            {highlight.unidade}
          </span>
        )}
      </div>
      {highlight.label && <div style={LABEL_STYLE}>{highlight.label}</div>}
    </>
  );
};
