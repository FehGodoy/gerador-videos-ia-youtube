import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { GalleryItem } from "./GalleryGrid";
import { CARD_SHADOW } from "./theme";

// Posições em % da área de colagem (não da tela inteira — ver `padding` no
// AbsoluteFill abaixo), com sobreposição deliberada e rotação leve — o
// "jeito colagem físico" vem daqui, não da mídia real. z-index cresce com
// o índice: item mais tarde na lista fica por cima, como fotos empilhadas
// numa mesa. Aceita 2-6 itens (mesmo limite do schema); com menos itens,
// só os primeiros slots são usados — os dois primeiros sozinhos (item
// grande + um sobrepondo) já formam uma colagem coerente.
const LAYOUT = [
  { left: 3, top: 6, width: 54, height: 62, rotate: -3 },
  { left: 40, top: 40, width: 44, height: 52, rotate: 4 },
  { left: 58, top: 4, width: 40, height: 44, rotate: -2 },
  { left: 68, top: 50, width: 30, height: 38, rotate: 5 },
  { left: 4, top: 66, width: 32, height: 30, rotate: 6 },
  { left: 36, top: 2, width: 26, height: 32, rotate: -5 },
];
const DELAYS = [0, 6, 3, 9, 5, 11];

/**
 * Fase 5, effect="masonry": 2-6 itens relacionados numa colagem sobreposta
 * (não uma grade organizada — ver `GalleryGrid.tsx` pra essa) — decidido
 * pela IA em keyword_extractor.py. Cada item vira um card (cantos
 * arredondados + sombra) flutuando sobre o papel compartilhado, deslocado
 * e levemente rotacionado em relação aos outros, dando a sensação de
 * fotos empilhadas fisicamente numa mesa.
 */
export const MasonryGallery: React.FC<{ items?: GalleryItem[] }> = ({ items = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill style={{ padding: 90 }}>
      <div style={{ position: "relative", width: "100%", height: "100%" }}>
        {items.slice(0, LAYOUT.length).map((item, i) => {
          const layout = LAYOUT[i];
          const delay = DELAYS[i];
          const s = spring({ frame: Math.max(frame - delay, 0), fps, config: { damping: 12, stiffness: 100 } });
          const scale = 0.85 + s * 0.15;
          const settle = interpolate(s, [0, 1], [layout.rotate * 1.6, layout.rotate]);

          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: `${layout.left}%`,
                top: `${layout.top}%`,
                width: `${layout.width}%`,
                height: `${layout.height}%`,
                zIndex: i + 1,
                borderRadius: 20,
                overflow: "hidden",
                boxShadow: CARD_SHADOW,
                opacity: s,
                transform: `rotate(${settle}deg) scale(${scale})`,
              }}
            >
              {item.mediaType === "video" ? (
                <OffthreadVideo src={staticFile(item.clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              ) : (
                <Img src={staticFile(item.clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              )}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export default MasonryGallery;
