import React from "react";
import { AbsoluteFill } from "remotion";

/**
 * Fase 1: mostra o texto do beat inteiro, sem estilo nem highlight de palavra.
 * A Fase 3 troca isso por destaque palavra-a-palavra usando beat.captions
 * (já disponível no composition.json, só não é usado ainda aqui).
 */
export const CaptionOverlay: React.FC<{ text: string }> = ({ text }) => {
  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 80,
      }}
    >
      <div
        style={{
          maxWidth: "80%",
          fontFamily: "Arial, sans-serif",
          fontSize: 42,
          fontWeight: 700,
          color: "white",
          textAlign: "center",
          textShadow: "0 2px 8px rgba(0,0,0,0.85)",
          backgroundColor: "rgba(0,0,0,0.35)",
          padding: "12px 24px",
          borderRadius: 8,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};
