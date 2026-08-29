import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";

const { fontFamily } = loadFont();
const ACCENT = "#ff8c42";

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático (composition_builder decide isso), só disponível pra uso
 * manual/futuro. Adaptado do original: aceita mídia real (clipPath) em vez
 * de gradiente fixo; sem clipPath, cai no gradiente + rótulo do template
 * original.
 *
 * Cores/fonte trocadas na integração: os gradientes originais eram azul/
 * roxo genérico de template, sem nenhuma relação com o resto do vídeo —
 * ConceptCard/Timeline/QuoteCard/RankingList (já em produção) usam Poppins
 * + laranja #ff8c42 sobre fundo marrom-escuro; esse efeito precisa da MESMA
 * identidade pra não destoar quando aparecer no meio de um vídeo real.
 *
 * Dois painéis entram deslizando de lados opostos e se encontram no centro
 * (spring), com uma linha divisória que aparece depois que eles assentam.
 */
const Panel: React.FC<{
  clipPath?: string;
  mediaType?: "image" | "video";
  label: string;
  gradient: string;
  translateX: number;
}> = ({ clipPath, mediaType = "image", label, gradient, translateX }) => (
  <div
    style={{
      width: "50%",
      height: "100%",
      overflow: "hidden",
      transform: `translateX(${translateX}%)`,
      position: "relative",
    }}
  >
    {clipPath ? (
      mediaType === "video" ? (
        <OffthreadVideo src={staticFile(clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      )
    ) : (
      <div
        style={{
          width: "100%",
          height: "100%",
          background: gradient,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <h2 style={{ fontFamily, color: "white", fontSize: 56, fontWeight: 500, margin: 0 }}>{label}</h2>
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
  const leftTranslateX = interpolate(leftSlide, [0, 1], [-100, 0]);
  const rightTranslateX = interpolate(rightSlide, [0, 1], [100, 0]);

  const dividerOpacity = interpolate(frame, [fps * 0.6, fps * 0.9], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#0f0d0c", flexDirection: "row" }}>
      <Panel
        clipPath={leftClipPath}
        mediaType={leftMediaType}
        label={leftLabel}
        gradient={`linear-gradient(135deg, #5c3220, #241c16)`}
        translateX={leftTranslateX}
      />
      <Panel
        clipPath={rightClipPath}
        mediaType={rightMediaType}
        label={rightLabel}
        gradient={`linear-gradient(135deg, ${ACCENT}, #c76a2e)`}
        translateX={rightTranslateX}
      />
      <div
        style={{
          position: "absolute",
          top: "10%",
          bottom: "10%",
          left: "50%",
          transform: "translateX(-50%)",
          width: 2,
          background: "linear-gradient(180deg, transparent, rgba(255,255,255,0.8), transparent)",
          opacity: dividerOpacity,
        }}
      />
    </AbsoluteFill>
  );
};

export default SplitScreen;
