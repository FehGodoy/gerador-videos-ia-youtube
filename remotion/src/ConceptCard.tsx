import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";

const { fontFamily } = loadFont();

const ACCENT = "#ff8c42";

/**
 * Card com a frase-chave do trecho, em tela cheia.
 *
 * Entra em dois casos: quando o diretor visual classificou o shot como TEXT
 * (ideia abstrata, nenhuma imagem literal serve) e quando nenhuma mídia
 * encontrada passou do threshold de relevância. O princípio é o do plano:
 * material que só lembra o assunto é pior que assumir que não achamos nada.
 *
 * Sem footage atrás de propósito — se houvesse um clipe bom, não estaríamos
 * aqui. O movimento vem da tipografia e de um gradiente lento.
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
  // deriva lenta do fundo pra cena não ficar completamente estática
  const drift = interpolate(frame, [0, durationInFrames], [0, 8], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(ellipse 1200px 700px at ${46 + drift}% ${38 - drift / 2}%, #241c16, #0f0d0c 70%)`,
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
            background: ACCENT,
            borderRadius: 3,
            margin: "0 auto 34px",
            transform: `scaleX(${enter})`,
          }}
        />
        <div
          style={{
            fontSize: 76,
            fontWeight: 600,
            color: "#f3ece0",
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
