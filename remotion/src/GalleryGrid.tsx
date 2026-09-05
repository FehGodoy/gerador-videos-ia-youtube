import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { CARD_SHADOW } from "./theme";

export type GalleryItem = { clipPath: string; mediaType?: "image" | "video" };

const LETTERS = "abcdef";
const DELAY_STEP = 4;

// grid-template-areas por CONTAGEM de itens (2-6, mesmo intervalo do
// schema) — desenhado à mão em vez de deixar o navegador auto-preencher,
// pra nunca sobrar célula vazia (bug real: com menos de 6 itens, a grade
// fixa antiga deixava células quase invisíveis). objectFit: cover em toda
// célula, então a orientação real da foto não importa pro layout ficar
// equilibrado.
const LAYOUTS: Record<number, { columns: string; rows: string; areas: string }> = {
  2: { columns: "1fr 1fr", rows: "1fr", areas: `"a b"` },
  3: { columns: "1fr 1fr", rows: "1fr 1fr", areas: `"a a" "b c"` },
  4: { columns: "1fr 1fr", rows: "1fr 1fr", areas: `"a b" "c d"` },
  5: { columns: "1fr 1fr 1fr", rows: "1fr 1fr", areas: `"a b c" "a d e"` },
  6: { columns: "1fr 1fr 1fr", rows: "1fr 1fr", areas: `"a b c" "d e f"` },
};

const GridCard: React.FC<{ item: GalleryItem; delay: number; gridArea?: string }> = ({ item, delay, gridArea }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: Math.max(frame - delay, 0), fps, config: { damping: 12, stiffness: 100 } });
  const scale = 0.8 + s * 0.2;

  return (
    <div
      style={{
        gridArea,
        // width/height 100% explícitos: item de CSS Grid estica de graça
        // pra célula, mas o mesmo componente também é usado no layout
        // "spotlight" (flexbox) — lá, uma div sem height explícito herda
        // "auto" e colapsa pro conteúdo, ficando com altura zero.
        width: "100%",
        height: "100%",
        borderRadius: 20,
        overflow: "hidden",
        boxShadow: CARD_SHADOW,
        transform: `scale(${scale})`,
        opacity: s,
      }}
    >
      {item.mediaType === "video" ? (
        <OffthreadVideo src={staticFile(item.clipPath)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <Img src={staticFile(item.clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      )}
    </div>
  );
};

/**
 * Fase 5, effect="gallery_grid": 2-6 itens relacionados numa grade —
 * decidido pela IA em keyword_extractor.py. `style` (algorítmico, ver
 * modules/composition_builder.py::_EffectPicker) escolhe entre "grid"
 * (grade equilibrada por contagem) e "spotlight" (1 foto dominante + as
 * demais numa coluna ao lado — só faz sentido com 3-5 itens; fora dessa
 * faixa cai pra "grid" mesmo que peçam spotlight).
 */
export const GalleryGrid: React.FC<{ items?: GalleryItem[]; style?: "grid" | "spotlight"; fillScreen?: boolean }> = ({
  items = [],
  style = "grid",
  fillScreen,
}) => {
  const count = Math.min(Math.max(items.length, 2), 6);
  const useSpotlight = style === "spotlight" && count >= 3 && count <= 5;
  const padding = fillScreen ? 0 : 60;

  if (useSpotlight) {
    const [hero, ...rest] = items;
    return (
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", padding }}>
        <div style={{ display: "flex", gap: 32, width: "100%", height: "100%" }}>
          <div style={{ flex: 1.7, height: "100%" }}>
            <GridCard item={hero} delay={0} />
          </div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 32 }}>
            {rest.map((item, i) => (
              <GridCard key={i} item={item} delay={(i + 1) * DELAY_STEP} />
            ))}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  const layout = LAYOUTS[count];
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", padding }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: layout.columns,
          gridTemplateRows: layout.rows,
          gridTemplateAreas: layout.areas,
          gap: 32,
          width: "100%",
          height: "100%",
        }}
      >
        {items.slice(0, count).map((item, i) => (
          <GridCard key={i} item={item} delay={i * DELAY_STEP} gridArea={LETTERS[i]} />
        ))}
      </div>
    </AbsoluteFill>
  );
};

export default GalleryGrid;
