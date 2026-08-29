import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { GalleryItem } from "./GalleryGrid";

// col: qual das 3 colunas; height: proporção do bloco dentro da coluna
// (mesmo layout do template original, fixo — é o "jeito Pinterest" do
// efeito, não faz sentido calcular alturas a partir da mídia real aqui).
// Gradientes quentes (laranja/âmbar/terracota) — mesma identidade de
// ConceptCard/Timeline/QuoteCard/RankingList, não o azul/roxo do template.
const LAYOUT = [
  { col: 0, height: "45%", gradient: "linear-gradient(135deg, #ff8c42, #c76a2e)", delay: 0 },
  { col: 0, height: "50%", gradient: "linear-gradient(135deg, #d9a441, #a67c2e)", delay: 6 },
  { col: 1, height: "55%", gradient: "linear-gradient(135deg, #c76a2e, #8a4a2e)", delay: 3 },
  { col: 1, height: "40%", gradient: "linear-gradient(135deg, #e0954f, #ff8c42)", delay: 9 },
  { col: 2, height: "40%", gradient: "linear-gradient(135deg, #a67c2e, #5c3220)", delay: 5 },
  { col: 2, height: "55%", gradient: "linear-gradient(135deg, #8a4a2e, #5c3220)", delay: 11 },
];

/**
 * Efeito trazido do catálogo de templates da React Video Editor (MCP
 * reactvideoeditor) — ainda NÃO usado por nenhum shot do pipeline
 * automático, só disponível pra uso manual/futuro. Adaptado do original:
 * cada bloco aceita uma mídia real (clipPath); bloco sem mídia cai no
 * gradiente do template original.
 *
 * 3 colunas com blocos de altura variável entrando em spring escalonado —
 * o efeito "Pinterest" vem das alturas fixas do LAYOUT, não da mídia.
 */
export const MasonryGallery: React.FC<{ items?: GalleryItem[] }> = ({ items = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const columns: (typeof LAYOUT[number] & { item?: GalleryItem })[][] = [[], [], []];
  LAYOUT.forEach((block, i) => columns[block.col].push({ ...block, item: items[i] }));

  return (
    <AbsoluteFill style={{ backgroundColor: "#0f0d0c", alignItems: "center", justifyContent: "center", padding: 32 }}>
      <div style={{ display: "flex", gap: 16, width: "90%", height: "85%" }}>
        {columns.map((col, colIdx) => (
          <div key={colIdx} style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16 }}>
            {col.map((block, blockIdx) => {
              const s = spring({ frame: Math.max(frame - block.delay, 0), fps, config: { damping: 12, stiffness: 100 } });
              const scale = 0.8 + s * 0.2;

              return (
                <div
                  key={blockIdx}
                  style={{
                    height: block.height,
                    borderRadius: 10,
                    overflow: "hidden",
                    transform: `scale(${scale})`,
                    opacity: s,
                    background: block.item ? undefined : block.gradient,
                  }}
                >
                  {block.item &&
                    (block.item.mediaType === "video" ? (
                      <OffthreadVideo src={staticFile(block.item.clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    ) : (
                      <Img src={staticFile(block.item.clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    ))}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export default MasonryGallery;
