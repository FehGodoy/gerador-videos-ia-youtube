import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import type { ChartData } from "./types";
import { CARD_RADIUS, CARD_SHADOW, INK_COLOR, RUST_ACCENT } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["500", "700", "900"], subsets: ["latin", "latin-ext"] });

const CARD_INSET = 90;

// A contagem tem duração fixa e curta — antes ela era esticada pela duração
// inteira do beat, e num bloco de 70s o número ficava subindo por 70s.
// Depois disso o valor final fica parado até a cena acabar.
const COUNT_SECONDS = 2.5;
const COUNT_DELAY_SECONDS = 0.4;

function formatValue(value: number, unidade: string): string {
  const trimmedUnit = unidade.trim();
  const isYear = /^anos?$/i.test(trimmedUnit);
  const numberStr = isYear
    ? String(Math.round(value))
    : Number.isInteger(value)
    ? value.toLocaleString("pt-BR")
    : value.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  if (!trimmedUnit || isYear) return numberStr;
  return trimmedUnit === "%" ? `${numberStr}%` : `${numberStr} ${trimmedUnit}`;
}

/**
 * Usado quando beat.type === "estatistico" — em vez de footage num card
 * (<FootageClip>), destaca o dado numérico do trecho (número, percentual,
 * ano, índice) sobre um fundo desfocado e escurecido do footage temático
 * já buscado pra esse beat, confinado num card sobre o papel. Sem footage
 * (raro), o número fica direto sobre o papel, sem card — nesse caso o
 * texto vira escuro (não tem fundo escuro pra sustentar texto branco).
 */
export const AnimatedChart: React.FC<{
  chart: ChartData;
  backgroundClipPath: string | null;
  backgroundMediaType: "video" | "image";
  backgroundBlurredPath?: string | null;
}> = ({ chart, backgroundClipPath, backgroundMediaType, backgroundBlurredPath }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entrance = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const hasComparison = chart.valor_inicial !== null;

  const progressStart = Math.round(COUNT_DELAY_SECONDS * fps);
  const progressEnd = progressStart + Math.round(COUNT_SECONDS * fps);
  const progress = interpolate(frame, [progressStart, progressEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const displayValue = hasComparison
    ? chart.valor_inicial! + (chart.valor_final - chart.valor_inicial!) * progress
    : chart.valor_final;

  const arrow = chart.tipo === "queda" ? "▼" : "▲";
  const onDarkCard = Boolean(backgroundClipPath);
  const textColor = onDarkCard ? "white" : INK_COLOR;
  const subTextColor = onDarkCard ? "rgba(255,255,255,0.68)" : "rgba(26,21,18,0.68)";

  return (
    <AbsoluteFill>
      {backgroundClipPath && (
        <AbsoluteFill
          style={{ inset: CARD_INSET, borderRadius: CARD_RADIUS, overflow: "hidden", boxShadow: CARD_SHADOW }}
        >
          {backgroundMediaType === "image" ? (
            // backgroundBlurredPath pré-computado em Python (modules/
            // image_effects.py, radius=10 ~ blur(20px)) — brightness/
            // saturate continuam ao vivo (baratos, não são convolução
            // espacial). Ausente = cai no blur ao vivo (composition.json
            // antigo); mantém scale(1.15) só nesse caso, pra esconder a
            // franja de amostragem transparente do blur ao vivo.
            <Img
              src={staticFile(backgroundBlurredPath ?? backgroundClipPath)}
              style={
                backgroundBlurredPath
                  ? {
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      filter: "brightness(0.32) saturate(0.9)",
                    }
                  : {
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      filter: "blur(20px) brightness(0.32) saturate(0.9)",
                      transform: "scale(1.12)",
                    }
              }
            />
          ) : (
            <OffthreadVideo
              src={staticFile(backgroundClipPath)}
              muted
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                filter: "blur(20px) brightness(0.32) saturate(0.9)",
                transform: "scale(1.12)",
              }}
            />
          )}
        </AbsoluteFill>
      )}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <div
          style={{
            fontFamily,
            opacity: entrance,
            transform: `scale(${0.92 + entrance * 0.08})`,
            textAlign: "center",
            color: textColor,
            padding: "0 80px",
          }}
        >
          {hasComparison && (
            <div
              style={{
                fontSize: 30,
                fontWeight: 500,
                color: subTextColor,
                marginBottom: 10,
              }}
            >
              {arrow} de {formatValue(chart.valor_inicial as number, chart.unidade)} para
            </div>
          )}
          <div
            style={{
              fontSize: hasComparison ? 128 : 148,
              fontWeight: 900,
              lineHeight: 1,
              letterSpacing: -2,
              color: onDarkCard ? "white" : RUST_ACCENT,
            }}
          >
            {formatValue(displayValue, chart.unidade)}
          </div>
          <div
            style={{
              fontSize: 32,
              fontWeight: 500,
              marginTop: 22,
              color: subTextColor,
              maxWidth: 900,
            }}
          >
            {chart.label}
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
