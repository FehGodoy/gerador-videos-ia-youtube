import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig } from "remotion";
import type { CompositionData } from "./types";
import { FootageClip } from "./FootageClip";
import { CaptionOverlay } from "./CaptionOverlay";

/**
 * Componente raiz: itera os beats do composition.json e monta a sequência.
 * Fase 1: <Sequence> simples, sem <TransitionSeries>/cross-fade (Fase 2) e
 * sem <AnimatedChart> para beats "estatistico" (também Fase 2 — nesta fase
 * todo beat vem como "concreto").
 */
export const VideoComposition: React.FC<CompositionData> = (data) => {
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {data.audio.path && <Audio src={staticFile(data.audio.path)} />}
      {data.beats.map((beat) => {
        const from = Math.round(beat.start_seconds * fps);
        const durationInFrames = Math.max(
          1,
          Math.round((beat.end_seconds - beat.start_seconds) * fps)
        );

        return (
          <Sequence key={beat.id} from={from} durationInFrames={durationInFrames}>
            {beat.footage?.clip_path ? (
              <FootageClip clipPath={beat.footage.clip_path} />
            ) : (
              <AbsoluteFill style={{ backgroundColor: "#111111" }} />
            )}
            <CaptionOverlay text={beat.text} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
