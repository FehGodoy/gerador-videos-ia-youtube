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
 * REAL da mídia — uma foto retrato vira um card retrato, não uma moldura
 * paisagem fixa com barra preta dentro pra completar (o vão ao redor é
 * preenchido pelo próprio papel do fundo, igual às referências do
 * usuário). Isso também elimina a necessidade de um fundo desfocado atrás
 * (`blurred_background_path`) — não tem mais vão dentro do card pra
 * preencher.
 *
 * `width`/`height` (dimensão REAL do arquivo, lida em Python via Pillow —
 * ver composition_builder.py::_image_dimensions) decidem o tamanho final
 * em pixels, calculado aqui como um "contain" manual dentro da área
 * disponível (1920x1080 menos CARD_INSET). Bug real pego testando com
 * fotos antigas escaneadas do usuário (algumas com só ~250px de lado):
 * `maxWidth/maxHeight` sozinho num <img> NUNCA amplia além do tamanho
 * intrínseco, só encolhe — uma foto pequena renderizava minúscula, perdida
 * no papel. Calculando o tamanho em pixels aqui (em vez de confiar em CSS
 * `object-fit`/`aspect-ratio` fazer isso sozinho), o card sempre ocupa o
 * máximo de espaço disponível preservando a proporção real, pra cima OU
 * pra baixo. Sem `width`/`height` no composition.json (dado antigo), cai
 * no fallback de sempre (maxWidth/maxHeight direto na mídia).
 *
 * Sem Ken Burns (zoom lento): era um `scale()` animado ao longo do shot
 * inteiro, caro pra recalcular a cada frame sem GPU no CI. Removido a
 * pedido do usuário nesta mesma sessão.
 */
export const FootageClip: React.FC<{
  clipPath: string;
  mediaType: "video" | "image";
  clipStartSeconds: number;
  width?: number;
  height?: number;
}> = ({ clipPath, mediaType, clipStartSeconds, width, height }) => {
  const { fps, width: videoW, height: videoH } = useVideoConfig();

  // Com dimensão conhecida: wrapper div com tamanho em PIXELS calculado
  // (contain manual), mídia por dentro a 100%/100% preenchendo certinho.
  // Sem dimensão (fallback): maxWidth/maxHeight DIRETO no elemento
  // substituído (<Img>/<OffthreadVideo>) — só eles têm tamanho intrínseco
  // pra servir de base; um <div> só com max-* e sem width/height não tem
  // base nenhuma pra calcular (colapsaria, filho com width:100% vira 0).
  if (mediaType === "image" && width && height) {
    const availW = videoW - CARD_INSET * 2;
    const availH = videoH - CARD_INSET * 2;
    const scale = Math.min(availW / width, availH / height);
    return (
      <AbsoluteFill style={{ inset: CARD_INSET, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div
          style={{
            width: Math.round(width * scale),
            height: Math.round(height * scale),
            borderRadius: CARD_RADIUS,
            boxShadow: CARD_SHADOW,
            overflow: "hidden",
          }}
        >
          <Img src={staticFile(clipPath)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        </div>
      </AbsoluteFill>
    );
  }

  const fallbackStyle: React.CSSProperties = {
    maxWidth: "100%",
    maxHeight: "100%",
    borderRadius: CARD_RADIUS,
    boxShadow: CARD_SHADOW,
  };

  return (
    <AbsoluteFill style={{ inset: CARD_INSET, display: "flex", alignItems: "center", justifyContent: "center" }}>
      {mediaType === "image" ? (
        <Img src={staticFile(clipPath)} style={fallbackStyle} />
      ) : (
        <OffthreadVideo
          src={staticFile(clipPath)}
          trimBefore={Math.max(0, Math.round(clipStartSeconds * fps))}
          // A única trilha do vídeo é a narração. Clipe de stock costuma vir sem
          // áudio, então isso nunca fez falta — mas material de arquivo vem com
          // som embutido e falaria por cima da narração.
          muted
          style={fallbackStyle}
        />
      )}
    </AbsoluteFill>
  );
};
