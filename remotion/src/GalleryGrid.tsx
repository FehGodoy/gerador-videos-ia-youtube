import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { CARD_SHADOW } from "./theme";

export type GalleryItem = { clipPath: string; mediaType?: "image" | "video" };

// Célula sem mídia (mantém a grade 2x3 preenchida mesmo com menos de 6
// itens) — tom neutro de papel, não mais gradiente laranja: sobre o fundo
// claro compartilhado, uma célula "vazia" deve ler como espaço reservado,
// não como conteúdo decorativo chamando atenção.
const EMPTY_FILL = "rgba(26,21,18,0.06)";
const DELAYS = [0, 4, 8, 12, 16, 20];

/**
 * Fase 5, effect="gallery_grid": 3-6 itens relacionados numa grade —
 * decidido pela IA em keyword_extractor.py. Cada célula vira um card
 * (cantos arredondados + sombra) sobre o papel compartilhado.
 *
 * Grade 2x3 com entrada escalonada (spring): canto superior esquerdo
 * primeiro, inferior direito por último.
 */
export const GalleryGrid: React.FC<{ items?: GalleryItem[] }> = ({ items = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", padding: 60 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gridTemplateRows: "1fr 1fr",
          gap: 32,
          width: "100%",
          height: "100%",
        }}
      >
        {DELAYS.map((delay, i) => {
          const item = items[i];
          const s = spring({ frame: Math.max(frame - delay, 0), fps, config: { damping: 12, stiffness: 100 } });
          const scale = 0.8 + s * 0.2;

          return (
            <div
              key={i}
              style={{
                borderRadius: 20,
                overflow: "hidden",
                boxShadow: CARD_SHADOW,
                transform: `scale(${scale})`,
                opacity: s,
                background: item ? undefined : EMPTY_FILL,
              }}
            >
              {item &&
                (item.mediaType === "video" ? (
                  <OffthreadVideo src={staticFile(item.clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                ) : (
                  <Img src={staticFile(item.clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                ))}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export default GalleryGrid;
