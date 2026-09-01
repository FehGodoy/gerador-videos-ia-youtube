import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, staticFile, useVideoConfig } from "remotion";
import { CARD_RADIUS, CARD_SHADOW } from "./theme";

const CARD_INSET = 90;

/**
 * `clipPath` vem do composition.json como caminho relativo à raiz do
 * projeto (ex: "cache/footage/abcd1234.mp4"); staticFile() resolve isso via
 * o public dir configurado em remotion.config.ts.
 *
 * `clipStartSeconds` (trimBefore) existe porque um bloco de narração longo
 * reutiliza o mesmo clipe em várias cenas — começar cada reuso de um ponto
 * diferente evita a sensação de loop.
 *
 * Mídia vira um card (cantos arredondados + sombra) flutuando sobre o
 * papel compartilhado de VideoComposition.tsx. O card se molda ao formato
 * REAL da mídia (maxWidth/maxHeight num container flex-center, sem
 * width/height fixos) — uma foto retrato vira um card retrato, não uma
 * moldura paisagem fixa com barra preta dentro pra completar (o vão ao
 * redor é preenchido pelo próprio papel do fundo, igual às referências do
 * usuário). Isso também elimina a necessidade de um fundo desfocado atrás
 * (`blurred_background_path`) — não tem mais vão dentro do card pra
 * preencher.
 *
 * Sem Ken Burns (zoom lento): era um `scale()` animado ao longo do shot
 * inteiro, caro pra recalcular a cada frame sem GPU no CI. Removido a
 * pedido do usuário nesta mesma sessão.
 */
export const FootageClip: React.FC<{
  clipPath: string;
  mediaType: "video" | "image";
  clipStartSeconds: number;
}> = ({ clipPath, mediaType, clipStartSeconds }) => {
  const { fps } = useVideoConfig();

  const cardStyle: React.CSSProperties = {
    maxWidth: "100%",
    maxHeight: "100%",
    borderRadius: CARD_RADIUS,
    boxShadow: CARD_SHADOW,
  };

  return (
    <AbsoluteFill style={{ inset: CARD_INSET, display: "flex", alignItems: "center", justifyContent: "center" }}>
      {mediaType === "image" ? (
        <Img src={staticFile(clipPath)} style={cardStyle} />
      ) : (
        <OffthreadVideo
          src={staticFile(clipPath)}
          trimBefore={Math.max(0, Math.round(clipStartSeconds * fps))}
          // A única trilha do vídeo é a narração. Clipe de stock costuma vir sem
          // áudio, então isso nunca fez falta — mas material de arquivo vem com
          // som embutido e falaria por cima da narração.
          muted
          style={cardStyle}
        />
      )}
    </AbsoluteFill>
  );
};
