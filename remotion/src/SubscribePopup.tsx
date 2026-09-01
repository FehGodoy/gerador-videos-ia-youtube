import React from "react";
import { useCurrentFrame, useVideoConfig, Img, interpolate, staticFile } from "remotion";
import { loadFont } from "@remotion/google-fonts/Poppins";

const { fontFamily: POPPINS } = loadFont("normal", { weights: ["500", "700"], subsets: ["latin", "latin-ext"] });

/**
 * Copiado de D:\Videos\Youtube\video_editor_app\remotion\src\SubscribePopup.tsx
 * (outro projeto do usuário) e ligado de verdade no pipeline: `SubscribeBar`
 * é montado em VideoComposition.tsx em todo vídeo gerado cujo canal tenha um
 * @handle configurado no painel (ver modules/composition_builder.py,
 * subscribe_popup no composition.json). O wrapper `SubscribePopup` abaixo
 * continua existindo só como composição standalone no Remotion Studio, pra
 * pré-visualizar a barra isolada sem rodar o pipeline inteiro.
 *
 * Ajustes reais na cópia: fonte trocada de "Neue Haas Grotesk Display Pro"
 * (original) pra Poppins, alinhando com o resto do vídeo. Também os
 * `staticFile("like-ativado.png")` etc.
 * originais assumem que os ícones estão na raiz do public dir do Remotion.
 * Este projeto SOBRESCREVE o public dir (remotion.config.ts) pra apontar
 * pra raiz do repositório (ou pra pasta de staging do render, via
 * REMOTION_PUBLIC_DIR) — não existe (nem é usado) um remotion/public/ aqui,
 * então staticFile("like-ativado.png") bruto procuraria o arquivo na raiz
 * do projeto e nunca acharia. Os 5 ícones foram copiados pra assets/
 * subscribe/ (mesmo padrão de assets/fallback/ e assets/music/ já usados
 * em config.yaml) e os caminhos abaixo apontam pra lá — copiados pro
 * staging do render por modules/renderer.py::stage_media_for_render.
 */
export type SubscribeBarProps = {
  channelName: string;
  channelHandle: string;
  avatarSrc: string;
  cycleSec: number; // de quanto em quanto tempo a barra reaparece
  offsetSec?: number; // atraso da 1ª aparição (default 0)
  subscribeText?: string;  // ex: "Iscriviti" (it) / "Suscríbete" (es)
  subscribedText?: string; // ex: "Iscritto" (it) / "Suscrito" (es)
  fontFamily?: string; // default = Poppins (fonte do canal, se diferente)
  dock?: "bottom-center" | "top-right"; // onde a barra descansa + de onde ela entra (default = "bottom-center")
  scale?: number; // escala geral da barra (default = 1)
  ctaColor?: string; // cor do botão "Iscriviti" não-inscrito (default = vermelho YouTube)
};

export type SubscribePopupProps = SubscribeBarProps & {
  durationSec: number; // duração total da composição (só pro standalone)
};

export const SUBSCRIBE_POPUP_DURATION = 40 * 30;

const FONT = POPPINS;
const ICON = (name: string) => staticFile(`assets/subscribe/${name}`);

// Barra animada reutilizável (overlay) — usada no standalone e dentro de
// qualquer outra composição que queira importar só a barra.
export const SubscribeBar: React.FC<SubscribeBarProps> = ({
  channelName,
  channelHandle,
  avatarSrc,
  cycleSec,
  offsetSec = 0,
  subscribeText = "Iscriviti",
  subscribedText = "Iscritto",
  fontFamily = FONT,
  dock = "bottom-center",
  scale = 1,
  ctaColor = "#ff0000",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const cycleFrames = Math.round(cycleSec * fps);
  // aplica a defasagem da 1ª aparição (módulo seguro pra frames negativos)
  const shifted = ((frame - Math.round(offsetSec * fps)) % cycleFrames + cycleFrames) % cycleFrames;
  const t = shifted / fps; // segundos dentro do ciclo
  const clamp = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const };

  // ── Linha do tempo da animação (dentro do ciclo) ────────────────
  // entra → mão vem da esquerda → clica LIKE → INSCREVER → SINO → segura → sai
  const slideIn = interpolate(t, [0, 0.5], [0, 1], clamp);
  const slideOut = interpolate(t, [6.4, 7.0], [1, 0], clamp);
  const visible = slideIn * slideOut;
  // "bottom-center" (padrão): descansa embaixo, centralizada; entra de baixo pra cima
  // "top-right": descansa no canto superior direito; entra vindo de cima-direita (fora da tela)
  const offsetY = dock === "bottom-center"
    ? interpolate(t, [0, 0.5, 6.4, 7.0], [120, 0, 0, 120], clamp)
    : interpolate(t, [0, 0.5, 6.4, 7.0], [-140, 0, 0, -140], clamp);
  const offsetX = dock === "top-right"
    ? interpolate(t, [0, 0.5, 6.4, 7.0], [220, 0, 0, 220], clamp)
    : 0;

  // Ordem dos cliques: like → inscrever → sino
  const liked = t >= 1.7;        // joinha acende (1º)
  const subscribed = t >= 2.7;   // vira INSCRITO (2º)
  const bellOn = t >= 3.7;       // sino acende (3º)

  // "press" de cada elemento no momento do clique
  const likePress = interpolate(t, [1.55, 1.7, 1.85], [1, 0.8, 1], clamp);
  const subPress = interpolate(t, [2.55, 2.7, 2.85], [1, 0.92, 1], clamp);
  const bellShake = interpolate(t, [3.7, 3.8, 3.9, 4.0], [0, 8, -8, 0], clamp);

  // ── Mãozinha ────────────────────────────────────────────────────
  // Posições medidas a partir da DIREITA da barra (like fica mais à esquerda).
  // Valores ~centro de cada ícone + meia largura da mão, pra o dedo cair no ícone.
  const handRightLike = 375;
  const handRightSub = 175;
  const handRightBell = 0;
  // entra da esquerda (valor alto = mais à esquerda) e para no like; depois sub; depois sino
  const handRight = interpolate(
    t,
    [0.8, 1.5, 2.0, 2.6, 3.1, 3.6],
    [520, handRightLike, handRightLike, handRightSub, handRightSub, handRightBell],
    clamp
  );
  const handPressY =
    interpolate(t, [1.55, 1.7, 1.85], [0, 14, 0], clamp) + // clique no like
    interpolate(t, [2.55, 2.7, 2.85], [0, 14, 0], clamp) + // clique no inscrever
    interpolate(t, [3.6, 3.75, 3.9], [0, 14, 0], clamp);   // clique no sino
  const handOpacity = interpolate(t, [0.7, 1.0, 4.1, 4.4], [0, 1, 1, 0], clamp);

  const BAR_H = 132;

  const dockStyle = dock === "top-right"
    ? { right: 48, top: 48 }
    : { left: "50%" as const, bottom: 110 };
  const dockTransform = dock === "top-right"
    ? `translateX(${offsetX}px) translateY(${offsetY}px) scale(${scale})`
    : `translateX(-50%) translateX(${offsetX}px) translateY(${offsetY}px) scale(${scale})`;
  // ancora o encolhimento no canto certo — a barra não "flutua" pra longe da posição de repouso
  const scaleOrigin = dock === "top-right" ? "top right" : "center bottom";

  return (
    <div
      style={{
        position: "absolute",
        ...dockStyle,
        opacity: visible,
        transform: dockTransform,
        transformOrigin: scaleOrigin,
        zIndex: 50,
      }}
    >
        {/* Barra (pílula branca) */}
        <div
          style={{
            position: "relative",
            display: "flex",
            alignItems: "center",
            gap: 26,
            height: BAR_H,
            padding: "0 34px 0 18px",
            borderRadius: 30,
            background: "#ffffff",
            boxShadow: "0 12px 34px rgba(0,0,0,0.30)",
          }}
        >
          {/* Avatar (opcional) */}
          {avatarSrc ? (
            <Img
              src={avatarSrc}
              style={{
                width: BAR_H - 28,
                height: BAR_H - 28,
                borderRadius: "50%",
                objectFit: "cover",
                flexShrink: 0,
              }}
            />
          ) : null}

          {/* Nome + handle */}
          <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", marginRight: 6 }}>
            <span style={{ fontFamily, fontWeight: 700, fontSize: 34, color: "#0f0f0f", lineHeight: 1.1, whiteSpace: "nowrap" }}>
              {channelName}
            </span>
            <span style={{ fontFamily, fontWeight: 500, fontSize: 22, color: "#9a9a9a", lineHeight: 1.2, whiteSpace: "nowrap" }}>
              {channelHandle}
            </span>
          </div>

          {/* Joinha */}
          <Img
            src={liked ? ICON("like-ativado.png") : ICON("like-desativado.png")}
            style={{ width: 52, height: 52, objectFit: "contain", flexShrink: 0, transform: `scale(${likePress})` }}
          />

          {/* Botão inscrever */}
          <div
            style={{
              transform: `scale(${subPress})`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: 64,
              width: 270, // largura fixa: não muda entre "INSCREVER-SE" e "INSCRITO"
              borderRadius: 32,
              background: subscribed ? "#e5e5e5" : ctaColor,
            }}
          >
            <span style={{ fontFamily, fontWeight: 700, fontSize: 28, letterSpacing: 0.5, color: subscribed ? "#606060" : "#ffffff", whiteSpace: "nowrap", textTransform: "uppercase" }}>
              {subscribed ? subscribedText : subscribeText}
            </span>
          </div>

          {/* Sino */}
          <Img
            src={bellOn ? ICON("bell-novo.png") : ICON("bell-desativado.png")}
            style={{ width: 52, height: 52, objectFit: "contain", flexShrink: 0, transform: `rotate(${bellShake}deg)` }}
          />

          {/* Mãozinha (cursor) */}
          <Img
            src={ICON("hand-cursor.webp")}
            style={{
              position: "absolute",
              right: handRight,
              top: 63,
              width: 84,
              height: 84,
              objectFit: "contain",
              opacity: handOpacity,
              transform: `translateY(${handPressY}px)`,
              filter: "drop-shadow(0 4px 5px rgba(0,0,0,0.35))",
              pointerEvents: "none",
            }}
          />
        </div>
      </div>
  );
};

// Composição standalone (overlay com fundo transparente)
export const SubscribePopup: React.FC<SubscribePopupProps> = ({
  channelName,
  channelHandle,
  avatarSrc,
  cycleSec,
  subscribeText,
  subscribedText,
}) => (
  <div style={{ width: 1920, height: 1080, position: "relative", background: "transparent" }}>
    <SubscribeBar
      channelName={channelName}
      channelHandle={channelHandle}
      avatarSrc={avatarSrc}
      cycleSec={cycleSec}
      subscribeText={subscribeText}
      subscribedText={subscribedText}
    />
  </div>
);
