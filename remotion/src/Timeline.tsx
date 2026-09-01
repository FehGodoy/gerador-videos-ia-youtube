import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import type { TimelineData } from "./types";
import { DECORATIVE_ACCENT, INK_COLOR, RUST_ACCENT } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["500", "600", "700"], subsets: ["latin", "latin-ext"] });

// Espaçamento entre a entrada de cada evento — revelar tudo de uma vez faria
// uma cronologia de 5 datas virar um bloco de texto ilegível em 6s de tela.
const STAGGER_FRAMES = 8;

/**
 * Fase 4: sequência cronológica de eventos datados (ex: "1957... depois
 * 1969..."). Cada evento entra em cascata em vez de tudo de uma vez, pro
 * espectador acompanhar a ordem em que os fatos aconteceram. Sobre o papel
 * compartilhado, sem fundo próprio.
 */
export const Timeline: React.FC<{ data: TimelineData; durationInFrames: number }> = ({
  data,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const exit = interpolate(frame, [durationInFrames - 12, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "center",
        padding: "0 160px",
      }}
    >
      <div style={{ fontFamily, opacity: exit, maxWidth: 1300, width: "100%" }}>
        {data.label && (
          <div
            style={{
              fontSize: 26,
              fontWeight: 600,
              letterSpacing: 2,
              textTransform: "uppercase",
              color: "rgba(26,21,18,0.55)",
              marginBottom: 40,
            }}
          >
            {data.label}
          </div>
        )}
        {data.events.map((event, i) => {
          const enter = spring({
            frame: frame - i * STAGGER_FRAMES,
            fps,
            config: { damping: 200, mass: 0.7 },
            durationInFrames: 20,
          });
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 28,
                opacity: enter,
                transform: `translateX(${interpolate(enter, [0, 1], [-24, 0])}px)`,
                marginBottom: i < data.events.length - 1 ? 26 : 0,
                borderLeft: `4px solid ${DECORATIVE_ACCENT}`,
                paddingLeft: 28,
              }}
            >
              <div style={{ fontSize: 52, fontWeight: 700, color: RUST_ACCENT, minWidth: 160 }}>
                {event.year}
              </div>
              <div style={{ fontSize: 34, fontWeight: 500, color: INK_COLOR, lineHeight: 1.3 }}>
                {event.text}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
