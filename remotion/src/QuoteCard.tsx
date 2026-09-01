import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import type { QuoteData } from "./types";
import { DECORATIVE_ACCENT, INK_COLOR, RUST_ACCENT } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["400", "600", "700", "900"], subsets: ["latin", "latin-ext"] });

/**
 * Fase 4: citação textual atribuída a alguém — entra quando o diretor visual
 * identifica uma fala/declaração real no trecho da narração. Sobre o papel
 * compartilhado, sem fundo próprio.
 */
export const QuoteCard: React.FC<{ data: QuoteData; durationInFrames: number }> = ({
  data,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.7 }, durationInFrames: 24 });
  const exit = interpolate(frame, [durationInFrames - 12, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "center",
        padding: "0 200px",
      }}
    >
      <div
        style={{
          fontFamily,
          opacity: Math.min(enter, exit),
          transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
          textAlign: "center",
        }}
      >
        <div
          style={{ fontSize: 120, fontWeight: 700, color: RUST_ACCENT, lineHeight: 0.4, marginBottom: 20 }}
        >
          &ldquo;
        </div>
        <div
          style={{
            fontSize: 58,
            fontWeight: 900,
            color: INK_COLOR,
            lineHeight: 1.3,
            letterSpacing: -1,
            maxWidth: 1300,
          }}
        >
          {data.text}
        </div>
        <div
          style={{
            width: 60,
            height: 4,
            background: DECORATIVE_ACCENT,
            borderRadius: 2,
            margin: "34px auto 20px",
            transform: `scaleX(${enter})`,
          }}
        />
        <div style={{ fontSize: 30, fontWeight: 600, color: INK_COLOR }}>{data.author}</div>
        {data.context && (
          <div
            style={{ fontSize: 22, fontWeight: 400, color: "rgba(26,21,18,0.6)", marginTop: 6 }}
          >
            {data.context}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
