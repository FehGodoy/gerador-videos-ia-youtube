import React from "react";
import { Composition } from "remotion";
import { VideoComposition } from "./VideoComposition";
import { emptyCompositionData, type CompositionData } from "./types";

// Remotion v4 infere o tipo de props da Composition a partir de um schema Zod
// (não usado aqui de propósito, para não duplicar composition.schema.json em
// uma terceira representação). O cast abaixo é só para o TypeScript aceitar o
// componente tipado com CompositionData; em runtime os dados batem porque
// quem os produz é o composition_builder.py validado contra o schema.
export const RemotionRoot: React.FC = () => {
  return (
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
  );
};
