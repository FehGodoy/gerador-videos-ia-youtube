import React from "react";
import { Composition } from "remotion";
import { VideoComposition } from "./VideoComposition";
import { emptyCompositionData, type CompositionData } from "./types";
import { SubscribePopup, SUBSCRIBE_POPUP_DURATION, type SubscribePopupProps } from "./SubscribePopup";

// Remotion v4 infere o tipo de props da Composition a partir de um schema Zod
// (não usado aqui de propósito, para não duplicar composition.schema.json em
// uma terceira representação). O cast abaixo é só para o TypeScript aceitar o
// componente tipado com CompositionData; em runtime os dados batem porque
// quem os produz é o composition_builder.py validado contra o schema.
export const RemotionRoot: React.FC = () => {
  return (
    <>
    <Composition
      id="VideoComposition"
      component={VideoComposition as unknown as React.FC<Record<string, unknown>>}
      fps={30}
      width={1920}
      height={1080}
      durationInFrames={30} // sobrescrito por calculateMetadata a partir do composition.json real
      defaultProps={emptyCompositionData as unknown as Record<string, unknown>}
      calculateMetadata={async ({ props }) => {
        const data = props as unknown as CompositionData;
        const durationInFrames = Math.max(
          1,
          Math.round(data.audio.duration_seconds * data.fps)
        );
        return {
          durationInFrames,
          fps: data.fps,
          width: data.width,
          height: data.height,
        };
      }}
    />
    {/* Composição standalone só pra pré-visualizar a SubscribeBar isolada no
        Remotion Studio — o uso real é dentro de VideoComposition.tsx, ligado
        a todo vídeo gerado com um canal cujo @handle esteja configurado (ver
        SubscribePopup.tsx pro porquê dos caminhos de asset). durationSec
        default bate com SUBSCRIBE_POPUP_DURATION (40s a 30fps). */}
    <Composition
      id="SubscribePopup"
      component={SubscribePopup}
      fps={30}
      width={1920}
      height={1080}
      durationInFrames={SUBSCRIBE_POPUP_DURATION}
      defaultProps={
        {
          channelName: "Canal Exemplo",
          channelHandle: "@canal-exemplo",
          avatarSrc: "",
          cycleSec: 8,
          durationSec: SUBSCRIBE_POPUP_DURATION / 30,
        } satisfies SubscribePopupProps
      }
    />
  </>
  );
};
