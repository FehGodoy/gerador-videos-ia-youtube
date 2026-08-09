import React from "react";
import { AbsoluteFill, Audio, staticFile, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import type { CompositionData } from "./types";
import { FootageClip } from "./FootageClip";
import { AnimatedChart } from "./AnimatedChart";

// ~300ms a 30fps. Cabe dentro do silêncio de 350ms que a narração já insere
// entre beats (config.yaml: narration.silence_between_beats_ms) — o
// crossfade consome esse intervalo ocioso em vez de cortar conteúdo falado.
const TRANSITION_FRAMES = 9;

/**
 * Componente raiz: itera os beats do composition.json e monta a sequência
 * com crossfade entre cortes (<TransitionSeries> + fade). Escolhe
 * <AnimatedChart> (beat.type === "estatistico") ou <FootageClip> em tela
 * cheia por beat. Sem legendas — o usuário não quis.
 *
 * Cada beat é estendido visualmente até o start_seconds do PRÓXIMO beat (em
 * vez de parar no próprio end_seconds) — absorve o silêncio entre beats na
 * narração, evita um buraco preto entre cortes, e dá à transição onde
 * sobrepor sem cortar fala. O <Audio> fica fora do <TransitionSeries> (irmão,
 * não filho), então a sobreposição visual do crossfade nunca afeta o áudio.
 */
export const VideoComposition: React.FC<CompositionData> = (data) => {
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {data.audio.path && <Audio src={staticFile(data.audio.path)} />}
      <TransitionSeries>
        {data.beats.map((beat, index) => {
          const nextBeat = data.beats[index + 1];
          const visualEndSeconds = nextBeat ? nextBeat.start_seconds : data.audio.duration_seconds;
          const durationInFrames = Math.max(
            1,
            Math.round((visualEndSeconds - beat.start_seconds) * fps)
          );

          return (
            <React.Fragment key={beat.id}>
              {index > 0 && (
                <TransitionSeries.Transition
                  presentation={fade()}
                  timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
                />
              )}
              <TransitionSeries.Sequence durationInFrames={durationInFrames}>
                {beat.type === "estatistico" && beat.chart ? (
                  <AnimatedChart
                    chart={beat.chart}
                    backgroundClipPath={beat.footage?.clip_path ?? null}
                    backgroundMediaType={beat.footage?.media_type ?? "video"}
                    durationInFrames={durationInFrames}
                  />
                ) : beat.footage?.clip_path ? (
                  <FootageClip
                    clipPath={beat.footage.clip_path}
                    mediaType={beat.footage.media_type}
                    durationInFrames={durationInFrames}
                  />
                ) : (
                  <AbsoluteFill style={{ backgroundColor: "#111111" }} />
                )}
              </TransitionSeries.Sequence>
            </React.Fragment>
          );
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
