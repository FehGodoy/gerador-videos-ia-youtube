import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

export type GalleryItem = { clipPath: string; mediaType?: "image" | "video" };

const GRADIENTS = [
  "linear-gradient(135deg, #3b82f6, #1d4ed8)",
  "linear-gradient(135deg, #a855f7, #7c3aed)",
  "linear-gradient(135deg, #4361ee, #3b82f6)",
  "linear-gradient(135deg, #7209b7, #a855f7)",
  "linear-gradient(135deg, #1d4ed8, #4361ee)",
  "linear-gradient(135deg, #7c3aed, #7209b7)",
];
const DELAYS = [0, 4, 8, 12, 16, 20];

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático, só disponível pra uso manual/futuro. Adaptado do original:
 * cada célula aceita uma mídia real (clipPath); célula sem mídia cai no
 * gradiente do template original, pra sempre preencher a grade 2x3 mesmo
 * com menos de 6 itens.
 *
 * Grade 2x3 com entrada escalonada (spring): canto superior esquerdo
 * primeiro, inferior direito por último.
 */
export const GalleryGrid: React.FC<{ items?: GalleryItem[] }> = ({ items = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill style={{ backgroundColor: "#111827", alignItems: "center", justifyContent: "center", padding: 32 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gridTemplateRows: "1fr 1fr",
          gap: 16,
          width: "90%",
          height: "80%",
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
                borderRadius: 10,
                overflow: "hidden",
                transform: `scale(${scale})`,
                opacity: s,
                background: item ? undefined : GRADIENTS[i],
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
