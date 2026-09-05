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

// "polaroid": moldura branca física (mais grossa embaixo, estilo
// instantâneo), cantos quase retos, sombra mais dura e curta (objeto
// pousado na mesa, não flutuando) e rotação mais espalhada — mesma
// mecânica de posicionamento do "clean", só o tratamento visual do card
// muda.
const POLAROID_SHADOW = "0 10px 14px rgba(20,15,10,0.35), 0 2px 3px rgba(20,15,10,0.28)";
const POLAROID_ROTATE_MULT = 1.8;

/**
 * Fase 5, effect="masonry": 2-6 itens relacionados numa colagem sobreposta
 * (não uma grade organizada — ver `GalleryGrid.tsx` pra essa) — decidido
 * pela IA em keyword_extractor.py. `style` (algorítmico, ver
 * modules/composition_builder.py::_EffectPicker) escolhe entre "clean"
 * (card com sombra suave, cantos arredondados) e "polaroid" (moldura
 * branca física, sombra dura, mais rotação).
 */
export const MasonryGallery: React.FC<{
  items?: GalleryItem[];
  style?: "clean" | "polaroid";
  fillScreen?: boolean;
}> = ({ items = [], style = "clean", fillScreen }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const polaroid = style === "polaroid";

  return (
    <AbsoluteFill style={{ padding: fillScreen ? 0 : 90 }}>
      <div style={{ position: "relative", width: "100%", height: "100%" }}>
        {items.slice(0, LAYOUT.length).map((item, i) => {
          const layout = LAYOUT[i];
          const delay = DELAYS[i];
          const s = spring({ frame: Math.max(frame - delay, 0), fps, config: { damping: 12, stiffness: 100 } });
          const scale = 0.85 + s * 0.15;
          const rotate = polaroid ? layout.rotate * POLAROID_ROTATE_MULT : layout.rotate;
          const settle = interpolate(s, [0, 1], [rotate * 1.6, rotate]);
          const media =
            item.mediaType === "video" ? (
              <OffthreadVideo src={staticFile(item.clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
            ) : (
              <Img src={staticFile(item.clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
            );

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
                opacity: s,
                transform: `rotate(${settle}deg) scale(${scale})`,
                ...(polaroid
                  ? { background: "#fdfcf9", padding: "14px 14px 46px", boxShadow: POLAROID_SHADOW, borderRadius: 4 }
                  : { borderRadius: 20, overflow: "hidden", boxShadow: CARD_SHADOW }),
              }}
            >
              {polaroid ? (
                <div style={{ width: "100%", height: "100%", overflow: "hidden", borderRadius: 4 }}>{media}</div>
              ) : (
                media
              )}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export default MasonryGallery;
