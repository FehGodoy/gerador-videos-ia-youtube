import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import type { RankingData } from "./types";
import { INK_COLOR, RUST_ACCENT } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["500", "600", "700"], subsets: ["latin", "latin-ext"] });

const STAGGER_FRAMES = 7;

/**
 * Fase 4: lista ordenada com valores (ex: "top 5 por PIB"). Itens entram de
 * cima pra baixo, em cascata, respeitando a ordem que o diretor visual decidiu.
 * Sobre o papel compartilhado, sem fundo próprio.
 */
export const RankingList: React.FC<{ data: RankingData; durationInFrames: number }> = ({
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
        padding: "0 200px",
      }}
    >
      <div style={{ fontFamily, opacity: exit, maxWidth: 1200, width: "100%" }}>
        {data.label && (
          <div
            style={{
              fontSize: 26,
              fontWeight: 600,
              letterSpacing: 2,
              textTransform: "uppercase",
              color: "rgba(26,21,18,0.55)",
              marginBottom: 34,
              textAlign: "center",
            }}
          >
            {data.label}
          </div>
        )}
        {data.items.map((item, i) => {
          const enter = spring({
            frame: frame - i * STAGGER_FRAMES,
            fps,
            config: { damping: 200, mass: 0.7 },
            durationInFrames: 18,
          });
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 26,
                opacity: enter,
                transform: `translateX(${interpolate(enter, [0, 1], [-20, 0])}px)`,
                padding: "16px 0",
                borderBottom:
                  i < data.items.length - 1 ? "1px solid rgba(26,21,18,0.14)" : "none",
              }}
            >
              <div style={{ fontSize: 34, fontWeight: 700, color: RUST_ACCENT, minWidth: 56 }}>
                {i + 1}º
              </div>
              <div style={{ fontSize: 36, fontWeight: 500, color: INK_COLOR, flex: 1 }}>
                {item.label}
              </div>
              <div style={{ fontSize: 36, fontWeight: 700, color: INK_COLOR }}>{item.value}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
