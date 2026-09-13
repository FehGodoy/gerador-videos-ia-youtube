import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";

/**
 * "Raio-X da voz": barras reagindo à amplitude real da narração, por todo
 * o vídeo — mesmo estilo/posição aprovados no mockup estático (barras ciano
 * com glow, centralizadas na horizontal, um pouco acima do rodapé).
 *
 * `envelope` já vem PRONTO do Python (modules/narration.py::
 * _compute_waveform_envelope) — RMS por janela, inteiros 0-255,
 * normalizado pelo pico do próprio áudio. Este componente NUNCA decodifica
 * áudio (decisão deliberada: `@remotion/media-utils::useAudioData`
 * carregaria o WAV inteiro em cada aba do Chromium — um vídeo de 40+
 * minutos gera 200+ MB de WAV, ~450MB de Float32Array por aba, risco real
 * de estourar memória com o render já sabidamente sensível a isso nesse
 * projeto). Aqui é só aritmética simples em cima de uma lista de inteiros.
 *
 * A cada frame, mostra uma janela deslizante das últimas `barCount`
 * amostras do envelope até o instante atual de reprodução — as barras vão
 * "andando" da direita pra esquerda conforme a narração avança, reagindo
 * ao volume de verdade (mais alto quando a voz sobe/enfatiza, mais baixo
 * em silêncio/pausas).
 */
export type VoiceWaveformProps = {
  envelope: number[]; // 0-255, um valor a cada 1/samplesPerSecond segundos
  samplesPerSecond: number;
  color: string;
  barCount: number; // quantas barras aparecem na tela ao mesmo tempo
  widthPercent: number; // largura da faixa de barras, em % da largura do vídeo
  bottomPx: number; // distância do rodapé
};

const MAX_BAR_HEIGHT_RATIO = 0.08; // % da altura do vídeo pro pico de amplitude

export const VoiceWaveform: React.FC<VoiceWaveformProps> = ({
  envelope,
  samplesPerSecond,
  color,
  barCount,
  widthPercent,
  bottomPx,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  if (!envelope || envelope.length === 0) {
    return null;
  }

  const currentSeconds = frame / fps;
  const currentSampleIndex = Math.floor(currentSeconds * samplesPerSecond);

  // janela deslizante terminando no instante atual (as barras mais à
  // direita são as mais recentes) — preenche com 0 no início do vídeo,
  // antes de existirem amostras suficientes pra encher a janela inteira.
  const windowValues: number[] = [];
  for (let i = currentSampleIndex - barCount; i < currentSampleIndex; i++) {
    windowValues.push(i >= 0 && i < envelope.length ? envelope[i] : 0);
  }

  const containerWidthPx = (widthPercent / 100) * width;
  const maxBarHeight = height * MAX_BAR_HEIGHT_RATIO;
  const barGap = containerWidthPx / barCount;
  const barWidth = barGap * 0.5;

  return (
    <div
      style={{
        position: "absolute",
        left: "50%",
        bottom: bottomPx,
        transform: "translateX(-50%)",
        width: containerWidthPx,
        height: maxBarHeight,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        zIndex: 40,
        pointerEvents: "none",
      }}
    >
      {windowValues.map((value, i) => {
        const barHeight = Math.max(3, (value / 255) * maxBarHeight);
        return (
          <div
            key={i}
            style={{
              width: barWidth,
              height: barHeight,
              borderRadius: barWidth / 2,
              background: color,
              boxShadow: `0 0 8px ${color}`,
              flexShrink: 0,
            }}
          />
        );
      })}
    </div>
  );
};
