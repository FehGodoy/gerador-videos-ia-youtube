import React from "react";
import { AbsoluteFill, Audio, Img, Sequence, staticFile, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming, filmBurn } from "@remotion/transitions";
import type { TransitionPresentation } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import type { CompositionData, GalleryData, Scene } from "./types";
import { FootageClip } from "./FootageClip";
import { AnimatedChart } from "./AnimatedChart";
import { HighlightOverlay } from "./HighlightOverlay";
import { ConceptCard } from "./ConceptCard";
import { Timeline } from "./Timeline";
import { QuoteCard } from "./QuoteCard";
import { RankingList } from "./RankingList";
import { ParallaxPan } from "./ParallaxPan";
import { SplitScreen } from "./SplitScreen";
import { ImageComparisonSlider } from "./ImageComparisonSlider";
import { GalleryGrid } from "./GalleryGrid";
import { MasonryGallery } from "./MasonryGallery";
import { whipPan } from "./transitions/whipPan";
import { SubscribeBar } from "./SubscribePopup";
import { PAPER_COLOR } from "./theme";

const TRANSITION_FRAMES = 9; // ~300ms a 30fps

// scene.transition_in é decidido algoritmicamente em composition_builder.py
// (não pela IA — ver o motivo em modules/composition_builder.py). Ausente
// = "fade", mesmo comportamento de antes da Fase 5.
//
// Cada presentation (fade/whipPan/filmBurn) tem seu próprio tipo de props —
// o cast abaixo é só pro TypeScript aceitar as 3 formas diferentes num
// mesmo ponto de uso; <TransitionSeries.Transition> não se importa com o
// tipo exato de PresentationProps em tempo de execução.
function presentationFor(transitionIn: Scene["transition_in"]): TransitionPresentation<Record<string, unknown>> {
  if (transitionIn === "whip_pan") return whipPan() as unknown as TransitionPresentation<Record<string, unknown>>;
  if (transitionIn === "film_burn") return filmBurn({}) as unknown as TransitionPresentation<Record<string, unknown>>;
  return fade() as unknown as TransitionPresentation<Record<string, unknown>>;
}

// Fase 5: Split Screen/Comparison Slider usam os 2 primeiros itens; Gallery
// Grid/Masonry aceitam a lista inteira (2-6). Decisão de QUAL efeito usar é
// da IA (keyword_extractor.py) — aqui só mapeia pros props de cada
// componente já existente em remotion/src/.
function renderGallery(gallery: GalleryData, fillScreen?: boolean) {
  const [first, second] = gallery.items;
  switch (gallery.effect) {
    case "split_screen":
      return (
        <SplitScreen
          leftClipPath={first?.clip_path}
          leftMediaType={first?.media_type}
          rightClipPath={second?.clip_path}
          rightMediaType={second?.media_type}
          fillScreen={fillScreen}
        />
      );
    case "comparison_slider":
      return (
        <ImageComparisonSlider
          beforeClipPath={first?.clip_path}
          beforeMediaType={first?.media_type}
          afterClipPath={second?.clip_path}
          afterMediaType={second?.media_type}
          fillScreen={fillScreen}
        />
      );
    case "masonry":
      return (
        <MasonryGallery
          items={gallery.items.map((i) => ({ clipPath: i.clip_path, mediaType: i.media_type }))}
          style={gallery.style === "polaroid" ? "polaroid" : "clean"}
          fillScreen={fillScreen}
        />
      );
    case "gallery_grid":
    default:
      return (
        <GalleryGrid
          items={gallery.items.map((i) => ({ clipPath: i.clip_path, mediaType: i.media_type }))}
          style={gallery.style === "spotlight" ? "spotlight" : "grid"}
          fillScreen={fillScreen}
        />
      );
  }
}

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
    <AbsoluteFill style={{ backgroundColor: PAPER_COLOR }}>
      {/* Textura de papel única para o vídeo inteiro — PNG estático, sem
          filter CSS nem regeneração por frame (mesma lição de performance
          do blur/Ken Burns/film_burn já resolvidos nesta sessão: qualquer
          efeito caro recalculado por frame é inviável sem GPU no CI). Os
          componentes filhos (FootageClip, ConceptCard, galeria...) não
          pintam mais fundo próprio — ficam transparentes e deixam esse
          papel aparecer por trás, como cards flutuando sobre ele. */}
      <Img
        src={staticFile("assets/texture/paper-grain.png")}
        style={{ position: "absolute", width: "100%", height: "100%", objectFit: "cover", opacity: 0.08 }}
      />
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
                  presentation={presentationFor(scene.transition_in)}
                  timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
                />
              )}
              <TransitionSeries.Sequence durationInFrames={durationInFrames}>
                {scene.kind === "chart" && chart ? (
                  <AnimatedChart
                    chart={chart}
                    backgroundClipPath={scene.footage?.clip_path ?? null}
                    backgroundMediaType={scene.footage?.media_type ?? "video"}
                    backgroundBlurredPath={scene.footage?.blurred_background_path ?? null}
                  />
                ) : scene.kind === "motion_graphic" &&
                  scene.motion_graphic?.kind === "timeline" ? (
                  <Timeline
                    data={scene.motion_graphic.data}
                    durationInFrames={durationInFrames}
                  />
                ) : scene.kind === "motion_graphic" &&
                  scene.motion_graphic?.kind === "quote" ? (
                  <QuoteCard
                    data={scene.motion_graphic.data}
                    durationInFrames={durationInFrames}
                  />
                ) : scene.kind === "motion_graphic" &&
                  scene.motion_graphic?.kind === "ranking" ? (
                  <RankingList
                    data={scene.motion_graphic.data}
                    durationInFrames={durationInFrames}
                  />
                ) : scene.kind === "concept" && scene.concept_text ? (
                  <ConceptCard
                    text={scene.concept_text}
                    durationInFrames={durationInFrames}
                  />
                ) : scene.kind === "gallery" && scene.gallery ? (
                  renderGallery(scene.gallery, scene.fill_screen)
                ) : scene.footage?.clip_path ? (
                  scene.footage.render_style === "parallax_pan" ? (
                    <ParallaxPan
                      clipPath={scene.footage.clip_path}
                      mediaType={scene.footage.media_type}
                      durationInFrames={durationInFrames}
                      fillScreen={scene.fill_screen}
                    />
                  ) : (
                    <FootageClip
                      clipPath={scene.footage.clip_path}
                      mediaType={scene.footage.media_type}
                      clipStartSeconds={scene.clip_start_seconds}
                      width={scene.footage.width}
                      height={scene.footage.height}
                      fillScreen={scene.fill_screen}
                    />
                  )
                ) : (
                  <AbsoluteFill />
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

      {/* Barra de like/inscrever-se/sino, ciclando por todo o vídeo. Irmã
          da TransitionSeries pelo mesmo motivo dos selos acima: SubscribeBar
          lê useCurrentFrame() direto (tempo absoluto), então não pode ficar
          dentro da série que sobrepõe frames pro crossfade. Não usa
          <Sequence> — o componente cicla sozinho via frame % cycleFrames,
          então uma montagem única cobre o vídeo inteiro. */}
      {data.subscribe_popup && (
        <SubscribeBar
          channelName={data.subscribe_popup.channel_name}
          channelHandle={data.subscribe_popup.channel_handle}
          avatarSrc={data.subscribe_popup.avatar_path ? staticFile(data.subscribe_popup.avatar_path) : ""}
          cycleSec={data.subscribe_popup.cycle_seconds}
          offsetSec={data.subscribe_popup.offset_seconds}
          subscribeText={data.subscribe_popup.subscribe_text}
          subscribedText={data.subscribe_popup.subscribed_text}
        />
      )}
    </AbsoluteFill>
  );
};
