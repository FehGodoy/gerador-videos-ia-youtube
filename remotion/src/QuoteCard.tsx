import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import type { QuoteData } from "./types";

const { fontFamily } = loadFont();

const ACCENT = "#ff8c42";

/**
 * Fase 4: citação textual atribuída a alguém — entra quando o diretor visual
 * identifica uma fala/declaração real no trecho da narração.
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
  const drift = interpolate(frame, [0, durationInFrames], [0, 8], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(ellipse 1200px 700px at ${46 + drift}% ${38 - drift / 2}%, #241c16, #0f0d0c 70%)`,
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
          style={{ fontSize: 120, fontWeight: 700, color: ACCENT, lineHeight: 0.4, marginBottom: 20 }}
        >
          &ldquo;
        </div>
        <div
          style={{
            fontSize: 58,
            fontWeight: 600,
            color: "#f3ece0",
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
            background: ACCENT,
            borderRadius: 2,
            margin: "34px auto 20px",
            transform: `scaleX(${enter})`,
          }}
        />
        <div style={{ fontSize: 30, fontWeight: 600, color: "#f3ece0" }}>{data.author}</div>
        {data.context && (
          <div
            style={{ fontSize: 22, fontWeight: 400, color: "rgba(255,255,255,0.55)", marginTop: 6 }}
          >
            {data.context}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
