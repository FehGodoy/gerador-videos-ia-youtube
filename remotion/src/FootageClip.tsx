import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/**
 * `clipPath` vem do composition.json como caminho relativo à raiz do
 * projeto (ex: "cache/footage/abcd1234.mp4"); staticFile() resolve isso via
 * o public dir configurado em remotion.config.ts.
 *
 * `mediaType === "image"` acontece quando a busca (ou a troca manual na
 * revisão) resolveu numa foto em vez de vídeo — sem Ken Burns a imagem
 * ficaria "morta" na tela, então aplica um zoom lento e contínuo.
 *
 * `clipStartSeconds` (trimBefore) existe porque um bloco de narração longo
 * reutiliza o mesmo clipe em várias cenas — começar cada reuso de um ponto
 * diferente evita a sensação de loop.
 *
 * Nem toda mídia é 16:9: uma foto quadrada ou de retrato com objectFit
 * "cover" puro cortava/dava zoom pesado pra preencher a tela (ficava
 * "gigante" e cortava o assunto). As duas ramificações abaixo mostram a
 * mídia INTEIRA, sem cortar (objectFit "contain") — pra 16:9, contain e
 * cover dão exatamente o mesmo resultado, então não tem regressão pro caso
 * comum, só ganho pro caso de proporção diferente.
 */
export const FootageClip: React.FC<{
  clipPath: string;
  mediaType: "video" | "image";
  clipStartSeconds: number;
  durationInFrames: number;
  blurredBackgroundPath?: string;
}> = ({ clipPath, mediaType, clipStartSeconds, durationInFrames, blurredBackgroundPath }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (mediaType === "image") {
    const scale = interpolate(frame, [0, durationInFrames], [1, 1.08], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
    return (
      <AbsoluteFill style={{ backgroundColor: "black" }}>
        {/* Fundo: a mesma foto, borrada e ampliada, preenchendo a tela
            inteira. blurredBackgroundPath vem pré-computado do Python
            (modules/image_effects.py) — já cortado (cover) e borrado, sem
            filter/scale de compensação. Ausente (composition.json antigo)
            = cai no blur ao vivo no Chromium, bem mais caro por frame
            (ver comentário no topo de VideoComposition.tsx pro porquê). */}
        <Img
          src={staticFile(blurredBackgroundPath ?? clipPath)}
          style={
            blurredBackgroundPath
              ? { position: "absolute", width: "100%", height: "100%", objectFit: "cover" }
              : {
                  position: "absolute",
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  filter: "blur(60px)",
                  transform: "scale(1.15)",
                }
          }
        />
        {/* Foto real, proporção original, nunca cortada — o Ken Burns
            (zoom lento) fica só aqui, o fundo desfocado fica parado. */}
        <Img
          src={staticFile(clipPath)}
          style={{
            position: "absolute",
            width: "100%",
            height: "100%",
            objectFit: "contain",
            transform: `scale(${scale})`,
          }}
        />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Fundo simples (sem desfoque de verdade) pra vídeo com proporção
          diferente de 16:9: borrar exigiria decodificar o MESMO vídeo uma
          segunda vez por quadro (um OffthreadVideo a mais), dobrando o custo
          de render — na prática, quase todo vídeo de banco/YouTube já vem
          16:9, então esse fundo raramente aparece mesmo. */}
      <AbsoluteFill
        style={{ background: "radial-gradient(circle, #1c1c1c 0%, #000 100%)" }}
      />
      <OffthreadVideo
        src={staticFile(clipPath)}
        trimBefore={Math.max(0, Math.round(clipStartSeconds * fps))}
        // A única trilha do vídeo é a narração. Clipe de stock costuma vir sem
        // áudio, então isso nunca fez falta — mas material de arquivo vem com
        // som embutido e falaria por cima da narração.
        muted
        style={{ position: "absolute", width: "100%", height: "100%", objectFit: "contain" }}
      />
    </AbsoluteFill>
  );
};
