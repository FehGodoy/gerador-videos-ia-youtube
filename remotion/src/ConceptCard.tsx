import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";
import { DECORATIVE_ACCENT, INK_COLOR } from "./theme";

const { fontFamily } = loadFont("normal", { weights: ["600", "900"], subsets: ["latin", "latin-ext"] });

/**
 * Card com a frase-chave do trecho, sobre o papel compartilhado (sem fundo
 * próprio).
 *
 * Entra em dois casos: quando o diretor visual classificou o shot como TEXT
 * (ideia abstrata, nenhuma imagem literal serve) e quando nenhuma mídia
 * encontrada passou do threshold de relevância. O princípio é o do plano:
 * material que só lembra o assunto é pior que assumir que não achamos nada.
 *
 * Sem footage atrás de propósito — se houvesse um clipe bom, não estaríamos
 * aqui. O movimento vem só da tipografia.
 */
export const ConceptCard: React.FC<{ text: string; durationInFrames: number }> = ({
  text,
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
        padding: "0 180px",
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
          style={{
            width: 84,
            height: 5,
            background: DECORATIVE_ACCENT,
            borderRadius: 3,
            margin: "0 auto 34px",
            transform: `scaleX(${enter})`,
          }}
        />
        <div
          style={{
            fontSize: 76,
            fontWeight: 900,
            color: INK_COLOR,
            lineHeight: 1.22,
            letterSpacing: -1.5,
            maxWidth: 1400,
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
};
