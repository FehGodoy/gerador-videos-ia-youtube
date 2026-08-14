import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import type { CompositionData } from "./types";
import { FootageClip } from "./FootageClip";
import { AnimatedChart } from "./AnimatedChart";
import { HighlightOverlay } from "./HighlightOverlay";
import { ConceptCard } from "./ConceptCard";

const TRANSITION_FRAMES = 9; // ~300ms a 30fps

/**
 * Componente raiz: achata as cenas de todos os beats numa única
 * <TransitionSeries> com crossfade entre cortes. Cada cena escolhe
 * <AnimatedChart> (kind "chart") ou <FootageClip> em tela cheia. Sem
 * legendas — o usuário não quis.
 *
 * Sincronia com o áudio: <TransitionSeries> SOBREPÕE as sequências vizinhas
 * pra fazer o crossfade, então a série encolhe TRANSITION_FRAMES a cada
 * corte. Com dezenas de cenas isso somaria dezenas de segundos de
 * dessincronia. Compensa somando TRANSITION_FRAMES na duração de toda cena
 * menos a última: cada cena passa a começar exatamente no seu
 * start_seconds e o total volta a bater com a duração do áudio.
 * O <Audio> fica fora da <TransitionSeries> (irmão, não filho), então a
 * sobreposição visual nunca afeta o áudio.
 */
export const VideoComposition: React.FC<CompositionData> = (data) => {
  const { fps } = useVideoConfig();

  const scenes = data.beats.flatMap((beat) =>
    beat.scenes.map((scene) => ({ scene, chart: beat.chart, key: `${beat.id}-${scene.start_seconds}` }))
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {data.audio.path && <Audio src={staticFile(data.audio.path)} />}
      <TransitionSeries>
        {scenes.map(({ scene, chart, key }, index) => {
          const isLast = index === scenes.length - 1;
          const baseFrames = Math.max(
            1,
            Math.round((scene.end_seconds - scene.start_seconds) * fps)
          );
          const durationInFrames = isLast ? baseFrames : baseFrames + TRANSITION_FRAMES;

          return (
            <React.Fragment key={key}>
              {index > 0 && (
                <TransitionSeries.Transition
                  presentation={fade()}
                  timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
                />
              )}
              <TransitionSeries.Sequence durationInFrames={durationInFrames}>
                {scene.kind === "chart" && chart ? (
                  <AnimatedChart
                    chart={chart}
                    backgroundClipPath={scene.footage?.clip_path ?? null}
                    backgroundMediaType={scene.footage?.media_type ?? "video"}
                  />
                ) : scene.kind === "concept" && scene.concept_text ? (
                  <ConceptCard
                    text={scene.concept_text}
                    durationInFrames={durationInFrames}
                  />
                ) : scene.footage?.clip_path ? (
                  <FootageClip
                    clipPath={scene.footage.clip_path}
                    mediaType={scene.footage.media_type}
                    clipStartSeconds={scene.clip_start_seconds}
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

      {/* Camada de selos de informação, ACIMA dos cortes. Usa <Sequence> com
          tempo absoluto em vez de virar cena: a <TransitionSeries> sobrepõe
          frames entre cenas vizinhas, então qualquer coisa colocada lá dentro
          precisaria entrar na compensação do crossfade. Aqui não — cada selo
          cai exatamente no segundo em que o dado é falado. */}
      {data.beats.flatMap((beat) =>
        beat.highlights.map((highlight) => {
          const from = Math.round(highlight.start_seconds * fps);
          const durationInFrames = Math.max(
            1,
            Math.round((highlight.end_seconds - highlight.start_seconds) * fps)
          );
          return (
            <Sequence
              key={`${beat.id}-hl-${highlight.start_seconds}`}
              from={from}
              durationInFrames={durationInFrames}
            >
              <HighlightOverlay highlight={highlight} durationInFrames={durationInFrames} />
            </Sequence>
          );
        })
      )}
    </AbsoluteFill>
  );
};
