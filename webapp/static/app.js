const STAGE_LABELS = {
  narration: "Narração",
  keywords: "Palavras-chave",
  footage: "Footage",
  captions: "Legendas",
};

const channelSelect = document.getElementById("channel-select");
const newChannelInput = document.getElementById("new-channel-input");
const newChannelBtn = document.getElementById("new-channel-btn");
const languageSelect = document.getElementById("language-select");
const speedSlider = document.getElementById("speed-slider");
const speedValue = document.getElementById("speed-value");
const favoritesTabs = document.getElementById("favorites-tabs");
const voicesStatus = document.getElementById("voices-status");
const voicesGrid = document.getElementById("voices-grid");
const voiceLockedNote = document.getElementById("voice-locked-note");
const blockText = document.getElementById("block-text");
const generateBlockBtn = document.getElementById("generate-block-btn");
const blocksList = document.getElementById("blocks-list");
const generateVideoBtn = document.getElementById("generate-video-btn");
const remoteRenderToggle = document.getElementById("remote-render-toggle");
const sourcesList = document.getElementById("sources-list");
const recencySelect = document.getElementById("recency-select");
const sourcesPicker = document.getElementById("sources-picker");
const ownMediaPicker = document.getElementById("own-media-picker");
const ownMediaInput = document.getElementById("own-media-input");
const ownMediaAddBtn = document.getElementById("own-media-add-btn");
const ownMediaStatus = document.getElementById("own-media-status");
const ownMediaList = document.getElementById("own-media-list");
const mediaModeRadios = document.querySelectorAll('input[name="media-mode"]');
const newDraftBtn = document.getElementById("new-draft-btn");
const stepReview = document.getElementById("step-review");
const reviewBeats = document.getElementById("review-beats");
const confirmRenderBtn = document.getElementById("confirm-render-btn");
const stepProgress = document.getElementById("step-progress");
const beatsList = document.getElementById("beats-list");
const renderStatusWrap = document.getElementById("render-status-wrap");
const renderStatusLabel = document.getElementById("render-status-label");
const renderProgressWrap = document.getElementById("render-progress-wrap");
const renderProgress = document.getElementById("render-progress");
const renderProgressLabel = document.getElementById("render-progress-label");
const stepResult = document.getElementById("step-result");
const resultVideo = document.getElementById("result-video");
const downloadLink = document.getElementById("download-link");
const errorMessage = document.getElementById("error-message");
const serperStatusDot = document.getElementById("serper-status-dot");
const serperStatusText = document.getElementById("serper-status-text");
const serperChangeBtn = document.getElementById("serper-change-btn");
const serperKeyForm = document.getElementById("serper-key-form");
const serperKeyInput = document.getElementById("serper-key-input");
const serperKeySave = document.getElementById("serper-key-save");
const serperKeyFeedback = document.getElementById("serper-key-feedback");
const channelHandleInput = document.getElementById("channel-handle-input");
const channelHandleSave = document.getElementById("channel-handle-save");
const channelHandleFeedback = document.getElementById("channel-handle-feedback");
const channelAvatarInput = document.getElementById("channel-avatar-input");
const channelAvatarBtn = document.getElementById("channel-avatar-btn");
const channelAvatarPreview = document.getElementById("channel-avatar-preview");

let currentChannel = localStorage.getItem("lastChannel") || null;
let favoriteIds = new Set();
let filterMode = "favorites";
let allVoices = [];
let selectedVoiceId = null;
let selectedVoiceLanguage = languageSelect.value;
let draftSlug = null;
let draftLocked = false;
let blocks = [];
let nextBlockId = 0;
let currentJobId = null;
let selectedSources = new Set();
let allSources = [];
let mediaMode = "ai_search";
let poolItemCount = 0;
let blockSlots = {}; // blockId -> trechos da timeline manual (modules/timeline.py)
let collapsedBlocks = new Set(); // blockId -> trechos ocultos (bloco com muitos trechos deixa a página gigante)
let folderSyncState = null; // status do sincronizador de pasta pro rascunho INTEIRO (ver webapp/folder_sync.py) — um só, não mais por bloco
let folderSyncTimer = null; // setInterval id do polling de status
let blockHintsLoading = new Set(); // blockId -> tradução/dica/prompt de imagem ainda sendo gerados (ver fetchSlotHints)

// Espelha modules/timeline.py::EFFECT_CATALOG — mantido em sincronia manual
// (é um catálogo pequeno e estável, não vale o round-trip de buscar do
// servidor toda vez que um card de trecho é montado).
const EFFECT_CATALOG = {
  padrao: { label: "Padrão (mídia única)", min: 1, max: 1 },
  parallax_pan: { label: "Parallax pan (mídia única)", min: 1, max: 1 },
  split_screen: { label: "Split screen (2 lado a lado)", min: 2, max: 2 },
  comparison_slider: { label: "Antes/depois", min: 2, max: 2 },
  gallery_grid: { label: "Grade de galeria (2 a 6 mídias)", min: 2, max: 6 },
  masonry: { label: "Colagem (2 a 6 mídias)", min: 2, max: 6 },
};

function slotEffectSpec(slot) {
  return EFFECT_CATALOG[slot.effect] || EFFECT_CATALOG.padrao;
}

function slotMediaCount(slot) {
  return (slot.media || []).filter(Boolean).length;
}
let mediaPool = { photos: [], videos: [] }; // biblioteca do lote de mídia própria, pro seletor de anexar por trecho

function icon(templateId) {
  const tpl = document.getElementById(templateId);
  return tpl.content.firstElementChild.cloneNode(true);
}

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.classList.remove("hidden");
}

function clearError() {
  errorMessage.classList.add("hidden");
}

// --- Status do Serper (Google Imagens) ---

async function loadSerperStatus() {
  let status;
  try {
    const resp = await fetch("/api/serper-status");
    status = await resp.json();
  } catch {
    serperStatusDot.className = "api-status-dot off";
    serperStatusText.textContent = "Google Imagens: não consegui checar (rede).";
    return;
  }

  if (!status.configured) {
    serperStatusDot.className = "api-status-dot off";
    serperStatusText.textContent = "Google Imagens: sem chave configurada.";
  } else if (status.balance === null) {
    serperStatusDot.className = "api-status-dot off";
    serperStatusText.textContent = "Google Imagens: não consegui checar o saldo.";
  } else if (status.low) {
    serperStatusDot.className = "api-status-dot low";
    serperStatusText.textContent = `Google Imagens: só ${status.balance} créditos restantes — considere trocar a chave.`;
  } else {
    serperStatusDot.className = "api-status-dot ok";
    serperStatusText.textContent = `Google Imagens: ${status.balance} créditos restantes.`;
  }
}

serperChangeBtn.addEventListener("click", () => {
  serperKeyForm.classList.toggle("hidden");
  if (!serperKeyForm.classList.contains("hidden")) {
    serperKeyInput.focus();
  }
});

serperKeySave.addEventListener("click", async () => {
  const apiKey = serperKeyInput.value.trim();
  if (!apiKey) return;
  serperKeyFeedback.textContent = "Verificando...";
  serperKeyFeedback.className = "api-key-feedback";
  serperKeySave.disabled = true;
  try {
    const resp = await fetch("/api/serper-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      serperKeyFeedback.textContent = data.detail || "Não deu certo.";
      serperKeyFeedback.className = "api-key-feedback error";
      return;
    }
    serperKeyFeedback.textContent = `Chave salva. ${data.balance} créditos.`;
    serperKeyFeedback.className = "api-key-feedback ok";
    serperKeyInput.value = "";
    await loadSerperStatus();
  } catch {
    serperKeyFeedback.textContent = "Falha de rede ao salvar.";
    serperKeyFeedback.className = "api-key-feedback error";
  } finally {
    serperKeySave.disabled = false;
  }
});

// --- Fontes de mídia ---

function updateGenerateVideoButton() {
  const mediaReady =
    mediaMode === "own_media" ? poolItemCount > 0 : selectedSources.size > 0;
  generateVideoBtn.disabled = blocks.length === 0 || !mediaReady;
}

// --- Modo de mídia (busca por IA vs. lote próprio) ---

function setMediaMode(mode) {
  mediaMode = mode;
  sourcesPicker.classList.toggle("hidden", mode !== "ai_search");
  ownMediaPicker.classList.toggle("hidden", mode !== "own_media");
  if (mode === "own_media") {
    refreshPoolPreview();
    // bloco gerado enquanto o modo ainda era "busca por IA" não tinha por
    // que gastar chamada de LLM em tradução/dica — pega essa dívida agora
    // que a linha do tempo passa a aparecer de verdade.
    for (const block of blocks) {
      const slots = blockSlots[block.id];
      if (slots && slots.length && !slots.some((s) => s.hint)) {
        fetchSlotHints(block.id, selectedVoiceLanguage);
      }
    }
  }
  renderBlocksList();
  updateGenerateVideoButton();
}

function renderPoolThumbs(pool) {
  mediaPool = pool;
  ownMediaList.innerHTML = "";
  const items = [
    ...pool.photos.map((p) => ({ ...p, kind: "foto", mediaType: "image" })),
    ...pool.videos.map((v) => ({ ...v, kind: "vídeo", mediaType: "video" })),
  ];
  for (const item of items) {
    const thumb = document.createElement("div");
    thumb.className = "own-media-thumb";
    const media =
      item.mediaType === "image"
        ? Object.assign(document.createElement("img"), { src: item.url })
        : Object.assign(document.createElement("video"), { src: item.url, muted: true });
    const kindTag = document.createElement("span");
    kindTag.className = "own-media-thumb-kind";
    kindTag.textContent = item.kind;
    thumb.append(media, kindTag);
    ownMediaList.appendChild(thumb);
  }
  poolItemCount = items.length;
  ownMediaStatus.textContent = poolItemCount
    ? `${pool.photos.length} foto(s), ${pool.videos.length} vídeo(s) na biblioteca.`
    : "Nenhum arquivo enviado ainda.";
  updateGenerateVideoButton();
}

async function refreshPoolPreview() {
  if (!draftSlug) return;
  try {
    const resp = await fetch(`/api/media-pool/${draftSlug}`);
    if (!resp.ok) return;
    renderPoolThumbs(await resp.json());
  } catch {
    // preview é só cortesia visual; falha aqui não bloqueia o upload em si
  }
}

async function uploadPoolFiles(files) {
  if (!draftSlug || !files.length) return;
  ownMediaStatus.textContent = "Enviando...";
  const formData = new FormData();
  for (const file of files) formData.append("files", file);
  try {
    const resp = await fetch(`/api/media-pool/${draftSlug}`, { method: "POST", body: formData });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao enviar (${resp.status})`);
    }
    const { pool } = await resp.json();
    renderPoolThumbs(pool);
  } catch (err) {
    showError(err.message);
    refreshPoolPreview();
  }
}

function renderSourcesGrid() {
  sourcesList.innerHTML = "";
  for (const source of allSources) {
    const label = document.createElement("label");
    label.className = "source-chip";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selectedSources.has(source.id);
    input.addEventListener("change", () => {
      if (input.checked) selectedSources.add(source.id);
      else selectedSources.delete(source.id);
      updateGenerateVideoButton();
    });

    const dot = document.createElement("span");
    dot.className = "source-chip-dot";

    const text = document.createElement("span");
    text.className = "source-chip-label";
    text.textContent = source.label;
    if (source.hint) text.title = source.hint;

    label.append(input, dot, text);
    sourcesList.appendChild(label);
  }
}

async function loadFootageSources() {
  try {
    const resp = await fetch("/api/footage-sources");
    const data = await resp.json();
    allSources = data.sources;
    selectedSources = new Set(data.default);
    renderSourcesGrid();
    updateGenerateVideoButton();

    recencySelect.innerHTML = "";
    for (const opt of data.recency_options) {
      const option = document.createElement("option");
      option.value = opt.id;
      option.textContent = opt.label;
      recencySelect.appendChild(option);
    }
    recencySelect.value = data.recency_default;
  } catch {
    sourcesList.innerHTML = '<p class="hint">Não consegui carregar as fontes disponíveis.</p>';
  }
}

// --- Canais ---

async function loadChannels() {
  const resp = await fetch("/api/channels");
  const list = await resp.json();
  channelSelect.innerHTML = "";
  if (list.length === 0) {
    const opt = document.createElement("option");
    opt.textContent = "Nenhum canal ainda — crie um";
    opt.disabled = true;
    opt.selected = true;
    channelSelect.appendChild(opt);
    currentChannel = null;
    renderVoicesGrid();
    return;
  }
  for (const name of list) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    channelSelect.appendChild(opt);
  }
  currentChannel = list.includes(currentChannel) ? currentChannel : list[0];
  channelSelect.value = currentChannel;
  localStorage.setItem("lastChannel", currentChannel);
  await loadFavorites();
  await loadIdentity();
  await loadVoices(languageSelect.value);
}

// --- Identidade do canal (barra de inscrever-se) ---

async function loadIdentity() {
  channelHandleInput.value = "";
  channelHandleFeedback.textContent = "";
  channelAvatarPreview.classList.add("hidden");
  if (!currentChannel) return;
  const resp = await fetch(`/api/channels/${encodeURIComponent(currentChannel)}/identity`);
  const identity = await resp.json();
  channelHandleInput.value = identity.handle || "";
  if (identity.avatar_url) {
    channelAvatarPreview.src = identity.avatar_url;
    channelAvatarPreview.classList.remove("hidden");
  }
}

channelHandleSave.addEventListener("click", async () => {
  if (!currentChannel) return;
  const handle = channelHandleInput.value.trim();
  channelHandleFeedback.textContent = "Salvando...";
  channelHandleFeedback.className = "api-key-feedback";
  channelHandleSave.disabled = true;
  try {
    const resp = await fetch(`/api/channels/${encodeURIComponent(currentChannel)}/identity`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      channelHandleFeedback.textContent = data.detail || "Não deu certo.";
      channelHandleFeedback.className = "api-key-feedback error";
      return;
    }
    channelHandleFeedback.textContent = "Salvo.";
    channelHandleFeedback.className = "api-key-feedback ok";
  } catch {
    channelHandleFeedback.textContent = "Falha de rede ao salvar.";
    channelHandleFeedback.className = "api-key-feedback error";
  } finally {
    channelHandleSave.disabled = false;
  }
});

channelAvatarBtn.addEventListener("click", () => channelAvatarInput.click());

channelAvatarInput.addEventListener("change", async () => {
  if (!currentChannel || !channelAvatarInput.files[0]) return;
  const formData = new FormData();
  formData.append("file", channelAvatarInput.files[0]);
  try {
    const resp = await fetch(`/api/channels/${encodeURIComponent(currentChannel)}/avatar`, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) {
      showError(data.detail || "Não foi possível enviar o avatar.");
      return;
    }
    channelAvatarPreview.src = data.avatar_url;
    channelAvatarPreview.classList.remove("hidden");
  } catch {
    showError("Falha de rede ao enviar o avatar.");
  } finally {
    channelAvatarInput.value = "";
  }
});

async function loadFavorites() {
  if (!currentChannel) {
    favoriteIds = new Set();
    return;
  }
  const resp = await fetch(`/api/channels/${encodeURIComponent(currentChannel)}/favorites`);
  const favorites = await resp.json();
  favoriteIds = new Set(favorites.map((v) => v.id));
  filterMode = favoriteIds.size > 0 ? "favorites" : "all";
  updateFavoritesTabs();
}

function updateFavoritesTabs() {
  favoritesTabs.querySelectorAll("button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.filter === filterMode);
  });
}

async function toggleFavorite(voice, starBtn) {
  if (!currentChannel) {
    showError("Selecione ou crie um canal antes de favoritar.");
    return;
  }
  const isFavorited = favoriteIds.has(voice.id);
  if (isFavorited) {
    await fetch(`/api/channels/${encodeURIComponent(currentChannel)}/favorites/${voice.id}`, {
      method: "DELETE",
    });
    favoriteIds.delete(voice.id);
  } else {
    await fetch(`/api/channels/${encodeURIComponent(currentChannel)}/favorites`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(voice),
    });
    favoriteIds.add(voice.id);
  }
  starBtn.classList.toggle("favorited", favoriteIds.has(voice.id));
  starBtn.replaceChildren(icon(favoriteIds.has(voice.id) ? "icon-star-filled" : "icon-star-outline"));
  if (filterMode === "favorites") renderVoicesGrid();
}

// --- Vozes ---

async function loadVoices(language) {
  voicesGrid.innerHTML = "";
  if (!currentChannel) {
    voicesStatus.textContent = "Crie ou selecione um canal para começar.";
    voicesStatus.classList.remove("hidden");
    return;
  }
  voicesStatus.textContent = "Carregando vozes (a primeira vez neste idioma pode demorar um pouco)...";
  voicesStatus.classList.remove("hidden");

  try {
    const resp = await fetch(`/api/voices?language=${encodeURIComponent(language)}`);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao carregar vozes (${resp.status})`);
    }
    allVoices = await resp.json();
    voicesStatus.classList.add("hidden");
    renderVoicesGrid();
  } catch (err) {
    voicesStatus.textContent = `Não foi possível carregar as vozes: ${err.message}`;
  }
}

function renderVoicesGrid() {
  voicesGrid.innerHTML = "";
  if (!currentChannel) return;

  const list = filterMode === "favorites" ? allVoices.filter((v) => favoriteIds.has(v.id)) : allVoices;

  if (list.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent =
      filterMode === "favorites"
        ? "Nenhuma voz favoritada neste canal ainda. Veja em \"Todas\" e clique na estrela."
        : "Nenhuma voz encontrada.";
    voicesGrid.appendChild(empty);
    return;
  }

  for (const voice of list) {
    const card = document.createElement("div");
    card.className = "voice-card";
    if (voice.id === selectedVoiceId) card.classList.add("selected");

    const head = document.createElement("div");
    head.className = "voice-card-head";

    const nameWrap = document.createElement("div");
    const name = document.createElement("div");
    name.className = "voice-name";
    name.textContent = voice.name;
    const desc = document.createElement("div");
    desc.className = "voice-desc";
    desc.textContent = voice.description || "";
    nameWrap.append(name, desc);

    const starBtn = document.createElement("button");
    starBtn.className = "favorite-btn" + (favoriteIds.has(voice.id) ? " favorited" : "");
    starBtn.setAttribute("aria-label", "Favoritar voz");
    starBtn.appendChild(icon(favoriteIds.has(voice.id) ? "icon-star-filled" : "icon-star-outline"));
    starBtn.addEventListener("click", () => toggleFavorite(voice, starBtn));

    head.append(nameWrap, starBtn);

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = voice.preview_url;

    const selectBtn = document.createElement("button");
    selectBtn.className = "select-btn";
    selectBtn.textContent = voice.id === selectedVoiceId ? "Selecionada" : "Selecionar";
    selectBtn.disabled = draftLocked && voice.id !== selectedVoiceId;
    selectBtn.addEventListener("click", () => selectVoice(voice));

    card.append(head, audio, selectBtn);
    voicesGrid.appendChild(card);
  }
}

function selectVoice(voice) {
  if (draftLocked) return;
  selectedVoiceId = voice.id;
  selectedVoiceLanguage = languageSelect.value;
  draftSlug = draftSlug || `web-${crypto.randomUUID().slice(0, 10)}`;
  renderVoicesGrid();
  updateGenerateBlockButton();
}

// --- Blocos ---

// A trava de cobertura mínima de mídia pra liberar colar o próximo bloco
// foi removida a pedido do usuário — colar vários blocos em sequência sem
// atribuir mídia entre eles agora é permitido sempre (o rigor de 100% pra
// gerar o vídeo de fato continua em create_job, no fim do fluxo).
function updateGenerateBlockButton() {
  generateBlockBtn.disabled = !selectedVoiceId || !blockText.value.trim();
}

function lockDraft() {
  draftLocked = true;
  languageSelect.disabled = true;
  voiceLockedNote.classList.add("visible");
  renderVoicesGrid();
}

function unlockDraft() {
  draftLocked = false;
  languageSelect.disabled = false;
  voiceLockedNote.classList.remove("visible");
  renderVoicesGrid();
}

async function generateBlock() {
  clearError();
  const text = blockText.value.trim();
  if (!text || !selectedVoiceId) return;

  generateBlockBtn.disabled = true;
  generateBlockBtn.textContent = "Gerando narração...";

  try {
    const blockId = nextBlockId;
    const resp = await fetch("/api/narration-blocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug: draftSlug,
        block_id: blockId,
        text,
        voice_id: selectedVoiceId,
        language: selectedVoiceLanguage,
        speed: Number(speedSlider.value),
      }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao gerar narração (${resp.status})`);
    }
    const result = await resp.json();
    blocks.push({ id: blockId, text, audioUrl: result.audio_url, duration: result.duration_seconds });
    blockSlots[blockId] = result.slots || [];
    nextBlockId += 1;
    blockText.value = "";
    lockDraft();
    renderBlocksList();
    if (mediaMode === "own_media") fetchSlotHints(blockId, selectedVoiceLanguage);
  } catch (err) {
    showError(err.message);
  } finally {
    generateBlockBtn.textContent = "Gerar narração deste bloco";
    updateGenerateBlockButton();
  }
}

async function fetchSlotHints(blockId, language) {
  blockHintsLoading.add(blockId);
  renderBlocksList();
  try {
    const resp = await fetch(`/api/narration-blocks/${draftSlug}/${blockId}/hints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: language || "pt" }),
    });
    if (!resp.ok) return;
    const { slots } = await resp.json();
    blockSlots[blockId] = slots;
  } catch {
    // dica/tradução são só apoio visual — falha aqui não impede atribuir mídia
  } finally {
    blockHintsLoading.delete(blockId);
    renderBlocksList();
  }
}

function renderBlocksList() {
  blocksList.innerHTML = "";
  blocks.forEach((block, index) => {
    const li = document.createElement("li");
    li.className = "block-row";
    li.style.animationDelay = `${index * 40}ms`;

    const head = document.createElement("div");
    head.className = "block-row-head";
    const number = document.createElement("span");
    number.className = "block-number";
    number.textContent = `Bloco ${index + 1} · ${block.duration.toFixed(1)}s`;
    const actions = document.createElement("div");
    actions.className = "block-actions";

    const regenBtn = document.createElement("button");
    regenBtn.className = "ghost icon-btn";
    regenBtn.appendChild(icon("icon-refresh"));
    regenBtn.append("Regenerar");
    regenBtn.addEventListener("click", () => regenerateBlock(block));

    const removeBtn = document.createElement("button");
    removeBtn.className = "danger-ghost icon-btn";
    removeBtn.appendChild(icon("icon-trash"));
    removeBtn.append("Remover");
    removeBtn.addEventListener("click", () => removeBlock(block.id));

    actions.append(regenBtn, removeBtn);

    const slots = blockSlots[block.id] || [];
    if (mediaMode === "own_media" && slots.length) {
      const collapsed = collapsedBlocks.has(block.id);
      const toggleBtn = document.createElement("button");
      toggleBtn.className = "ghost icon-btn";
      toggleBtn.appendChild(icon(collapsed ? "icon-chevron-down" : "icon-chevron-up"));
      toggleBtn.append(collapsed ? `Mostrar trechos (${slots.length})` : "Ocultar trechos");
      toggleBtn.addEventListener("click", () => {
        if (collapsed) collapsedBlocks.delete(block.id);
        else collapsedBlocks.add(block.id);
        renderBlocksList();
      });
      actions.appendChild(toggleBtn);
    }

    head.append(number, actions);

    const text = document.createElement("div");
    text.className = "block-text";
    text.textContent = block.text;

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = block.audioUrl + `?t=${Date.now()}`;

    li.append(head, text, audio);
    if (mediaMode === "own_media" && slots.length) {
      if (collapsedBlocks.has(block.id)) {
        const filled = slots.filter((s) => slotMediaCount(s) >= slotEffectSpec(s).min).length;
        const summary = document.createElement("p");
        summary.className = "hint timeline-strip-collapsed-summary";
        summary.textContent = `${slots.length} trecho(s) — ${filled} com mídia suficiente.`;
        li.appendChild(summary);
      } else {
        li.appendChild(renderSlotStrip(block.id));
      }
      // Ações de bloco inteiro (não de um trecho só) ficam sempre visíveis,
      // mesmo com os trechos colapsados — é exatamente quando um bloco tem
      // trechos demais que essas ações mais importam.
      const prompts = slots.map((s) => s.image_prompt).filter(Boolean);
      if (prompts.length) li.appendChild(renderCopyAllPromptsButton(prompts));
    }
    blocksList.appendChild(li);
  });
  renderDraftFolderSync();
  renderDraftCopyAllPrompts();
  updateGenerateVideoButton();
  updateGenerateBlockButton();
}

// --- Editor de timeline manual (modo de mídia própria) ---

function renderSlotStrip(blockId) {
  const strip = document.createElement("div");
  strip.className = "timeline-strip";
  const slots = blockSlots[blockId] || [];
  if (!slots.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "Fatiando a narração em trechos...";
    strip.appendChild(empty);
    return strip;
  }

  if (blockHintsLoading.has(blockId)) {
    const loading = document.createElement("p");
    loading.className = "hint timeline-hints-loading";
    loading.textContent = "IA gerando tradução, dica e prompt de imagem por trecho...";
    strip.appendChild(loading);
  } else if (slots.every((s) => !s.translation_pt && !s.hint && !s.image_prompt)) {
    // Todo trecho sem NENHUM dos 3 campos de IA depois do fetch já ter
    // terminado — provável falha transitória da IA (ver comentário em
    // modules/timeline.py::generate_slot_hints: uma falha não fica mais
    // cacheada pra sempre, então tentar de novo aqui realmente funciona).
    const retry = document.createElement("div");
    retry.className = "timeline-hints-retry";
    const retryText = document.createElement("span");
    retryText.className = "hint";
    retryText.textContent = "Tradução/dica/prompt de imagem não vieram (provável instabilidade da IA).";
    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "ghost";
    retryBtn.textContent = "Gerar de novo";
    retryBtn.addEventListener("click", () => fetchSlotHints(blockId, selectedVoiceLanguage));
    retry.append(retryText, retryBtn);
    strip.appendChild(retry);
  }

  for (const slot of slots) strip.appendChild(renderSlotCard(blockId, slot));
  return strip;
}

// Um prompt por linha, na mesma ordem dos trechos — pra colar de uma vez
// só num gerador de imagem por IA que aceite lote (em vez de copiar trecho
// por trecho pelo botão individual em cada card).
function renderCopyAllPromptsButton(prompts, label) {
  const wrap = document.createElement("div");
  wrap.className = "timeline-copy-all-wrap";

  const defaultLabel = label || `Copiar todos os prompts (${prompts.length})`;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost timeline-copy-all-btn";
  btn.textContent = defaultLabel;
  btn.addEventListener("click", async () => {
    try {
      // A ferramenta de destino do usuário exige uma linha em branco entre
      // cada prompt (testado colando no painel dele) — "\n" simples juntava
      // tudo grudado.
      await navigator.clipboard.writeText(prompts.join("\n\n"));
      btn.textContent = "Copiado!";
      setTimeout(() => {
        btn.textContent = defaultLabel;
      }, 1500);
    } catch {
      // clipboard pode falhar sem permissão/fora de https — não bloqueia o usuário
    }
  });

  wrap.appendChild(btn);
  return wrap;
}

// Mesma ideia do botão por bloco acima, mas juntando os prompts de TODOS os
// blocos do rascunho, na ordem — renderizado uma vez só (mesmo padrão de
// renderDraftFolderSync), não por bloco.
function renderDraftCopyAllPrompts() {
  const container = document.getElementById("draft-copy-all-prompts");
  if (!container) return;
  container.innerHTML = "";
  if (mediaMode !== "own_media") return;

  const prompts = [];
  for (const block of blocks) {
    const slots = blockSlots[block.id] || [];
    for (const slot of slots) {
      if (slot.image_prompt) prompts.push(slot.image_prompt);
    }
  }
  if (!prompts.length) return;

  container.appendChild(
    renderCopyAllPromptsButton(prompts, `Copiar todos os prompts do rascunho (${prompts.length})`)
  );
}

// --- Sincronização automática de pasta pro rascunho inteiro (ver
// webapp/folder_sync.py) --- A cada arquivo novo numa pasta local, o
// backend anexa ao próximo trecho de mídia única ainda vazio do PRIMEIRO
// bloco que tiver vaga — bloco 1 esgota antes do 2 começar a receber,
// sozinho, sem precisar desligar/religar entre um bloco e outro. Só pra
// efeito de mídia única (padrão/parallax pan); galeria continua manual.
let draftFolderSyncPanelOpen = false;

async function fetchFolderSyncStatus() {
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/watch-folder`);
    if (!resp.ok) return;
    const data = await resp.json();
    folderSyncState = data;
    // só o bloco "atual" pode ter mudado desde o último poll — os
    // anteriores já estão prontos e o sincronizador nunca volta neles.
    if (data.watching && data.current_block_id != null) {
      const manifestResp = await fetch(`/api/timeline/${draftSlug}/${data.current_block_id}`);
      if (manifestResp.ok) {
        const { slots } = await manifestResp.json();
        blockSlots[data.current_block_id] = slots;
      }
    }
    renderBlocksList();
  } catch {
    // status é só cortesia visual — falha aqui não desliga a sincronização real
  }
}

function startFolderSyncPolling() {
  stopFolderSyncPolling();
  folderSyncTimer = setInterval(fetchFolderSyncStatus, 2000);
}

function stopFolderSyncPolling() {
  if (folderSyncTimer) {
    clearInterval(folderSyncTimer);
    folderSyncTimer = null;
  }
}

async function startFolderSync(folderPath) {
  clearError();
  if (!draftSlug) return;
  if (!folderPath) {
    showError("Informe o caminho da pasta.");
    return;
  }
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/watch-folder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: folderPath }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao sincronizar pasta (${resp.status})`);
    }
    folderSyncState = await resp.json();
    startFolderSyncPolling();
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

async function stopFolderSync() {
  clearError();
  stopFolderSyncPolling();
  if (!draftSlug) {
    folderSyncState = { watching: false };
    renderBlocksList();
    return;
  }
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/watch-folder/stop`, { method: "POST" });
    folderSyncState = resp.ok ? await resp.json() : { watching: false };
  } catch {
    folderSyncState = { watching: false };
  }
  renderBlocksList();
}

// Renderizado UMA vez por rascunho (não por bloco) — dentro de
// #draft-folder-sync, em .own-media-picker, ao lado da biblioteca.
function renderDraftFolderSync() {
  const container = document.getElementById("draft-folder-sync");
  if (!container) return;
  container.innerHTML = "";
  if (mediaMode !== "own_media") return;

  const wrap = document.createElement("div");
  wrap.className = "timeline-folder-sync";

  const state = folderSyncState;

  if (state && state.watching) {
    const blockLabel =
      state.current_block_id != null
        ? ` — bloco ${blocks.findIndex((b) => b.id === state.current_block_id) + 1} em preenchimento`
        : "";
    const status = document.createElement("span");
    status.className = "timeline-folder-sync-status";
    status.textContent =
      `Observando "${state.folder}"${blockLabel} — ${state.consumed_count} arquivo(s) usado(s)` +
      (state.all_filled ? " · todos os trechos de mídia única já completos" : "");

    const stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.className = "ghost";
    stopBtn.textContent = "Parar sincronização";
    stopBtn.addEventListener("click", () => stopFolderSync());

    wrap.append(status, stopBtn);

    if (state.last_error) {
      const err = document.createElement("p");
      err.className = "hint timeline-folder-sync-error";
      err.textContent = `Erro no último arquivo: ${state.last_error}`;
      wrap.appendChild(err);
    }
    container.appendChild(wrap);
    return;
  }

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost";
  btn.textContent = "Sincronizar pasta (todos os blocos)";

  const panel = document.createElement("div");
  panel.className = "timeline-folder-sync-panel";
  panel.classList.toggle("hidden", !draftFolderSyncPanelOpen);

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent =
    'A cada arquivo novo nessa pasta, ele vai pro próximo trecho vazio do primeiro bloco que ainda tiver vaga — o bloco 1 esgota antes do 2 começar a receber, sozinho, mesmo pra blocos gerados depois de ligar isso aqui (só efeitos de mídia única — split screen e galeria continuam manuais). Arquivo usado é movido pra uma subpasta "usados".';

  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Caminho da pasta (ex.: C:\\Users\\você\\Downloads)";
  input.className = "timeline-folder-sync-input";

  const startBtn = document.createElement("button");
  startBtn.type = "button";
  startBtn.className = "ghost";
  startBtn.textContent = "Iniciar";
  startBtn.addEventListener("click", () => startFolderSync(input.value.trim()));

  btn.addEventListener("click", () => {
    draftFolderSyncPanelOpen = !draftFolderSyncPanelOpen;
    panel.classList.toggle("hidden", !draftFolderSyncPanelOpen);
  });

  panel.append(hint, input, startBtn);
  wrap.append(btn, panel);
  container.appendChild(wrap);
}

function renderSlotCard(blockId, slot) {
  const card = document.createElement("div");
  card.className = "timeline-slot-card";

  const head = document.createElement("div");
  head.className = "timeline-slot-head";
  const badge = document.createElement("span");
  badge.className = "timeline-slot-badge";
  badge.textContent = `${slot.start_seconds.toFixed(1)}s–${slot.end_seconds.toFixed(1)}s`;
  head.appendChild(badge);
  card.appendChild(head);

  const text = document.createElement("p");
  text.className = "timeline-slot-text";
  text.textContent = slot.text;
  card.appendChild(text);

  if (slot.translation_pt && slot.translation_pt !== slot.text) {
    const translation = document.createElement("p");
    translation.className = "timeline-slot-translation";
    translation.textContent = slot.translation_pt;
    card.appendChild(translation);
  }

  if (slot.hint) {
    const hint = document.createElement("p");
    hint.className = "timeline-slot-hint";
    hint.textContent = `Dica: ${slot.hint}`;
    card.appendChild(hint);
  }

  if (slot.image_prompt) {
    const promptWrap = document.createElement("div");
    promptWrap.className = "timeline-slot-image-prompt";

    const promptText = document.createElement("code");
    promptText.className = "timeline-slot-image-prompt-text";
    promptText.textContent = slot.image_prompt;

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "ghost icon-btn timeline-slot-copy-btn";
    copyBtn.textContent = "Copiar";
    copyBtn.title = "Copiar prompt de imagem pra colar num gerador de IA";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(slot.image_prompt);
        copyBtn.textContent = "Copiado!";
        setTimeout(() => {
          copyBtn.textContent = "Copiar";
        }, 1500);
      } catch {
        // clipboard pode falhar sem permissão/fora de https — não bloqueia o usuário
      }
    });

    promptWrap.append(promptText, copyBtn);
    card.appendChild(promptWrap);
  }

  if (slot.needs_media === false) {
    // A IA (ou o usuário, via override anterior) decidiu que este trecho
    // vira texto na tela — não pede efeito nem mídia nenhuma.
    const notice = document.createElement("div");
    notice.className = "timeline-slot-text-notice";
    const noticeText = document.createElement("p");
    noticeText.textContent = "Vira texto na tela, sem precisar de mídia.";
    const overrideBtn = document.createElement("button");
    overrideBtn.type = "button";
    overrideBtn.className = "ghost";
    overrideBtn.textContent = "Usar mídia aqui mesmo assim";
    overrideBtn.addEventListener("click", () => setSlotNeedsMedia(blockId, slot, true));
    notice.append(noticeText, overrideBtn);
    card.appendChild(notice);
    return card;
  }

  card.appendChild(renderSlotEffectPicker(blockId, slot));

  const attach = document.createElement("div");
  attach.className = "timeline-slot-attach";
  const spec = slotEffectSpec(slot);
  const mediaList = slot.media || [];
  for (let i = 0; i < spec.max; i++) {
    const item = mediaList[i];
    attach.appendChild(
      item ? renderAssignedMedia(blockId, slot, i, item) : renderAttachControl(blockId, slot, i)
    );
  }
  card.appendChild(attach);

  const noMediaBtn = document.createElement("button");
  noMediaBtn.type = "button";
  noMediaBtn.className = "ghost timeline-slot-no-media-btn";
  noMediaBtn.textContent = "Isso não precisa de mídia";
  noMediaBtn.title = "Marcar como texto na tela em vez de anexar mídia";
  noMediaBtn.addEventListener("click", () => setSlotNeedsMedia(blockId, slot, false));
  card.appendChild(noMediaBtn);

  return card;
}

// Override manual da classificação da IA (needs_media) — o usuário sempre
// pode discordar, nos dois sentidos (ver POST .../needs-media no server.py).
async function setSlotNeedsMedia(blockId, slot, needsMedia) {
  clearError();
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/${blockId}/${slot.index}/needs-media`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ needs_media: needsMedia }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao atualizar trecho (${resp.status})`);
    }
    const { slot: updated } = await resp.json();
    blockSlots[blockId][updated.index] = updated;
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

// Select de efeito por trecho — a escolha do usuário sobre COMO essa mídia
// (ou mídias, se for galeria) aparece na tela. Trocar de efeito não apaga
// mídia já anexada em posições que ainda existem no novo efeito (ex.: de
// "grade" pra "colagem", ambas 2-6 posições) — só quando encolhe o número
// de posições disponíveis (ver set_timeline_slot_effect no server.py).
function renderSlotEffectPicker(blockId, slot) {
  const row = document.createElement("div");
  row.className = "timeline-slot-effect";

  const label = document.createElement("label");
  label.textContent = "Efeito";

  const select = document.createElement("select");
  for (const [id, spec] of Object.entries(EFFECT_CATALOG)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = spec.label;
    if ((slot.effect || "padrao") === id) opt.selected = true;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => setSlotEffect(blockId, slot, select.value));

  row.append(label, select);
  return row;
}

async function setSlotEffect(blockId, slot, effect) {
  clearError();
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/${blockId}/${slot.index}/effect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ effect }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao trocar efeito (${resp.status})`);
    }
    const { slot: updated } = await resp.json();
    blockSlots[blockId][updated.index] = updated;
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

function renderAssignedMedia(blockId, slot, mediaIndex, media) {
  const wrap = document.createElement("div");
  wrap.className = "timeline-slot-assigned";

  const url = `/own_media_cache/${draftSlug}/${media.pool_filename}`;
  const el =
    media.media_type === "image"
      ? Object.assign(document.createElement("img"), { src: url })
      : Object.assign(document.createElement("video"), { src: url, muted: true });
  wrap.appendChild(el);

  if (media.media_type === "video" && media.clip_start_seconds != null) {
    const time = document.createElement("span");
    time.className = "timeline-slot-time";
    time.textContent = `a partir de ${media.clip_start_seconds.toFixed(1)}s`;
    wrap.appendChild(time);
  }

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "danger-ghost icon-btn timeline-slot-remove";
  removeBtn.appendChild(icon("icon-trash"));
  removeBtn.addEventListener("click", () => unassignSlot(blockId, slot, mediaIndex));
  wrap.appendChild(removeBtn);

  // Corrige o desalinhamento clássico da sincronização de pasta (um
  // download que falhou empurra tudo uma posição pra frente) — só faz
  // sentido em trecho de mídia única elegível pro sincronizador (mesmo
  // critério de modules/timeline.py::is_single_media_slot); galeria nunca
  // é preenchida automaticamente, não participa desse desalinhamento.
  if (slotEffectSpec(slot).max === 1 && slot.needs_media !== false) {
    const shiftBtn = document.createElement("button");
    shiftBtn.type = "button";
    shiftBtn.className = "ghost icon-btn timeline-slot-shift";
    shiftBtn.title = "Essa mídia é a do próximo trecho? Puxa a mídia de cada trecho seguinte uma posição pra trás.";
    shiftBtn.textContent = "Realinhar a partir daqui";
    shiftBtn.addEventListener("click", () => shiftMediaFrom(blockId, slot));
    wrap.appendChild(shiftBtn);
  }

  return wrap;
}

async function shiftMediaFrom(blockId, slot) {
  clearError();
  const ok = window.confirm(
    "Isso vai puxar a mídia de cada trecho seguinte (deste bloco em diante) uma posição pra trás, " +
    "fechando a vaga aqui. O último trecho da cadeia fica sem mídia. Confirma?"
  );
  if (!ok) return;
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/shift-media`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block_id: blockId, slot_index: slot.index }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao realinhar (${resp.status})`);
    }
    await resp.json();
    // Rebusca o manifesto de cada bloco que pode ter mudado, do bloco
    // atual em diante — a cadeia pode atravessar vários blocos.
    const affected = blocks.map((b) => b.id).filter((id) => id >= blockId);
    for (const id of affected) {
      const manifestResp = await fetch(`/api/timeline/${draftSlug}/${id}`);
      if (manifestResp.ok) {
        const { slots } = await manifestResp.json();
        blockSlots[id] = slots;
      }
    }
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

function renderAttachControl(blockId, slot, mediaIndex) {
  const wrap = document.createElement("div");
  wrap.className = "timeline-slot-attach-wrap";

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost";
  btn.textContent = "Anexar mídia";
  btn.addEventListener("click", () => openAttachPickerPopup(blockId, slot, mediaIndex));

  wrap.appendChild(btn);
  return wrap;
}

// Popup fixo no <body> (mesmo padrão de openClipStartPicker) em vez de um
// painel posicionado dentro do card — o card do trecho agora tem altura
// fixa com scroll (.timeline-slot-card), e um painel absoluto dentro dele
// seria cortado pelo overflow em vez de flutuar por cima.
function openAttachPickerPopup(blockId, slot, mediaIndex) {
  const backdrop = document.createElement("div");
  backdrop.className = "timeline-popup-backdrop";

  const popup = document.createElement("div");
  popup.className = "timeline-popup";
  popup.addEventListener("click", (e) => e.stopPropagation());

  const title = document.createElement("h3");
  title.className = "timeline-popup-title";
  title.textContent = "Escolha um arquivo da sua biblioteca";

  function close() {
    document.body.removeChild(backdrop);
  }

  const grid = buildAttachPicker(blockId, slot, mediaIndex, close);

  const actions = document.createElement("div");
  actions.className = "timeline-popup-actions";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "ghost";
  cancelBtn.textContent = "Cancelar";
  cancelBtn.addEventListener("click", close);
  actions.appendChild(cancelBtn);

  popup.append(title, grid, actions);
  backdrop.appendChild(popup);
  backdrop.addEventListener("click", close);
  document.body.appendChild(backdrop);
}

function buildAttachPicker(blockId, slot, mediaIndex, onClose) {
  const grid = document.createElement("div");
  grid.className = "timeline-attach-grid";

  const items = [
    ...mediaPool.photos.map((p) => ({ ...p, mediaType: "image" })),
    ...mediaPool.videos.map((v) => ({ ...v, mediaType: "video" })),
  ];
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "Envie fotos/vídeos na sua biblioteca (acima) antes de anexar.";
    grid.appendChild(empty);
    return grid;
  }

  for (const item of items) {
    const thumb = document.createElement("div");
    thumb.className = "timeline-attach-thumb";
    thumb.title = item.filename;
    const media =
      item.mediaType === "image"
        ? Object.assign(document.createElement("img"), { src: item.url })
        : Object.assign(document.createElement("video"), { src: item.url, muted: true });
    thumb.appendChild(media);
    thumb.addEventListener("click", () => {
      if (item.mediaType === "image") {
        assignSlot(blockId, slot, mediaIndex, item.filename, null);
        onClose();
      } else {
        openClipStartPicker(item, slot, (startSeconds) => {
          assignSlot(blockId, slot, mediaIndex, item.filename, startSeconds);
          onClose();
        });
      }
    });
    grid.appendChild(thumb);
  }
  return grid;
}

async function assignSlot(blockId, slot, mediaIndex, poolFilename, clipStartSeconds) {
  clearError();
  try {
    const resp = await fetch(`/api/timeline/${draftSlug}/${blockId}/${slot.index}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pool_filename: poolFilename,
        clip_start_seconds: clipStartSeconds,
        media_index: mediaIndex,
      }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao anexar mídia (${resp.status})`);
    }
    const { slot: updated } = await resp.json();
    blockSlots[blockId][updated.index] = updated;
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

async function unassignSlot(blockId, slot, mediaIndex) {
  clearError();
  try {
    const resp = await fetch(
      `/api/timeline/${draftSlug}/${blockId}/${slot.index}?media_index=${mediaIndex}`,
      { method: "DELETE" }
    );
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao remover mídia (${resp.status})`);
    }
    const { slot: updated } = await resp.json();
    blockSlots[blockId][updated.index] = updated;
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

// Popup de recorte: escolhe visualmente qual trecho (do tamanho do slot,
// ~4s) de um vídeo da biblioteca vai pro trecho da narração. Não existe
// componente de scrubber no projeto (o slider de velocidade é de um valor
// só) — janela arrastável construída na mão sobre uma barra representando
// a duração inteira do vídeo.
function openClipStartPicker(item, slot, onConfirm) {
  const slotDuration = Math.max(0.5, slot.end_seconds - slot.start_seconds);
  const videoDuration = item.duration || slotDuration;
  const maxStart = Math.max(0, videoDuration - slotDuration);
  let start = 0;

  const backdrop = document.createElement("div");
  backdrop.className = "timeline-popup-backdrop";

  const popup = document.createElement("div");
  popup.className = "timeline-popup";
  popup.addEventListener("click", (e) => e.stopPropagation());

  const title = document.createElement("h3");
  title.className = "timeline-popup-title";
  title.textContent = `Escolha ${slotDuration.toFixed(1)}s deste vídeo`;

  const video = document.createElement("video");
  video.src = item.url;
  video.muted = true;
  video.className = "timeline-popup-video";

  const track = document.createElement("div");
  track.className = "clip-scrub-track";
  const windowEl = document.createElement("div");
  windowEl.className = "clip-scrub-window";
  track.appendChild(windowEl);

  function widthPct() {
    return videoDuration > 0 ? Math.max(4, (slotDuration / videoDuration) * 100) : 100;
  }
  function updateWindow() {
    const leftPct = videoDuration > 0 ? (start / videoDuration) * 100 : 0;
    windowEl.style.left = `${leftPct}%`;
    windowEl.style.width = `${widthPct()}%`;
    video.currentTime = start;
  }

  function setStartFromClientX(clientX) {
    const rect = track.getBoundingClientRect();
    const ratio = rect.width > 0 ? Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)) : 0;
    start = Math.min(maxStart, Math.max(0, ratio * videoDuration));
    updateWindow();
  }

  let dragging = false;
  windowEl.addEventListener("pointerdown", (e) => {
    dragging = true;
    windowEl.setPointerCapture(e.pointerId);
  });
  windowEl.addEventListener("pointermove", (e) => {
    if (dragging) setStartFromClientX(e.clientX);
  });
  windowEl.addEventListener("pointerup", () => {
    dragging = false;
  });
  track.addEventListener("click", (e) => setStartFromClientX(e.clientX));

  const actions = document.createElement("div");
  actions.className = "timeline-popup-actions";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "ghost";
  cancelBtn.textContent = "Cancelar";
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "primary";
  confirmBtn.textContent = "Usar este trecho";
  actions.append(cancelBtn, confirmBtn);

  function close() {
    document.body.removeChild(backdrop);
  }
  cancelBtn.addEventListener("click", close);
  backdrop.addEventListener("click", close);
  confirmBtn.addEventListener("click", () => {
    close();
    onConfirm(Math.round(start * 100) / 100);
  });

  popup.append(title, video, track, actions);
  backdrop.appendChild(popup);
  document.body.appendChild(backdrop);
  updateWindow();
}

async function regenerateBlock(block) {
  clearError();
  try {
    const resp = await fetch("/api/narration-blocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug: draftSlug,
        block_id: block.id,
        text: block.text,
        voice_id: selectedVoiceId,
        language: selectedVoiceLanguage,
        speed: Number(speedSlider.value),
        force: true,
      }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao regenerar (${resp.status})`);
    }
    const result = await resp.json();
    block.duration = result.duration_seconds;
    block.audioUrl = result.audio_url;
    blockSlots[block.id] = result.slots || [];
    renderBlocksList();
    if (mediaMode === "own_media") fetchSlotHints(block.id, selectedVoiceLanguage);
  } catch (err) {
    showError(err.message);
  }
}

function removeBlock(blockId) {
  // Apaga o manifesto em disco também — sem isso, o sincronizador de
  // pasta do rascunho inteiro (ainda ligado, mexendo em OUTROS blocos)
  // continuaria descobrindo o arquivo e oferecendo vaga pra um bloco que
  // o usuário já removeu daqui. Fire-and-forget: não precisa travar a UI
  // pra isso, e mesmo se falhar não é destrutivo (só deixa um manifesto
  // órfão que o sincronizador já sabe pular via needs_media... na
  // verdade não sabe, mas a falha aqui é rara o bastante pra não valer
  // tratamento especial).
  if (draftSlug) {
    fetch(`/api/timeline/${draftSlug}/${blockId}`, { method: "DELETE" }).catch(() => {});
  }
  blocks = blocks.filter((b) => b.id !== blockId);
  delete blockSlots[blockId];
  collapsedBlocks.delete(blockId);
  blockHintsLoading.delete(blockId);
  renderBlocksList();
  if (blocks.length === 0) unlockDraft();
}

function resetDraft() {
  if (folderSyncState && folderSyncState.watching) stopFolderSync();
  stopFolderSyncPolling();
  folderSyncState = null;
  draftSlug = null;
  draftLocked = false;
  selectedVoiceId = null;
  blocks = [];
  blockSlots = {};
  collapsedBlocks = new Set();
  blockHintsLoading = new Set();
  mediaPool = { photos: [], videos: [] };
  nextBlockId = 0;
  blockText.value = "";
  blocksList.innerHTML = "";
  currentJobId = null;
  stepReview.classList.add("hidden");
  reviewBeats.innerHTML = "";
  disarmPasteTarget();
  stepProgress.classList.add("hidden");
  stepResult.classList.add("hidden");
  renderProgressWrap.classList.add("hidden");
  renderStatusWrap.classList.add("hidden");
  clearError();
  unlockDraft();
  updateGenerateBlockButton();

  poolItemCount = 0;
  ownMediaList.innerHTML = "";
  ownMediaStatus.textContent = "";
  for (const radio of mediaModeRadios) radio.checked = radio.value === "ai_search";
  setMediaMode("ai_search");
}

// --- Geração do vídeo ---

function renderBeatsQueue(beats) {
  beatsList.innerHTML = "";
  for (const beat of beats) {
    const li = document.createElement("li");
    li.className = "beat-row";
    li.dataset.beatId = beat.id;

    const text = document.createElement("div");
    text.className = "beat-text";
    text.textContent = `${beat.id + 1}. ${beat.text}`;

    const stages = document.createElement("div");
    stages.className = "beat-stages";
    for (const stage of Object.keys(STAGE_LABELS)) {
      const badge = document.createElement("span");
      badge.className = "stage-badge";
      badge.dataset.stage = stage;
      badge.textContent = STAGE_LABELS[stage];
      stages.appendChild(badge);
    }

    li.append(text, stages);
    beatsList.appendChild(li);
  }
  stepProgress.classList.remove("hidden");
}

function updateBeatStage(beatId, stage, status) {
  const row = beatsList.querySelector(`.beat-row[data-beat-id="${beatId}"]`);
  if (!row) return;
  const badge = row.querySelector(`.stage-badge[data-stage="${stage}"]`);
  if (!badge) return;
  badge.classList.remove("running", "done");
  if (status === "running" || status === "done") badge.classList.add(status);
}

async function loadFootageReview(jobId) {
  const resp = await fetch(`/api/jobs/${jobId}/footage-candidates`);
  if (!resp.ok) return;
  const beatsData = await resp.json();
  renderReviewBeats(jobId, beatsData);
  stepReview.classList.remove("hidden");
}

function markChosen(gridEl, thumbEl) {
  gridEl.querySelectorAll(".candidate-card").forEach((el) => {
    el.classList.remove("chosen");
    const check = el.querySelector(".candidate-chosen-check");
    if (check) check.remove();
  });
  thumbEl.classList.add("chosen");
  const check = document.createElement("span");
  check.className = "candidate-chosen-check";
  check.innerHTML = CHOSEN_CHECK_SVG;
  check.title = "Escolhido pra esta cena";
  thumbEl.querySelector(".candidate-media").appendChild(check);
}

function flashNote(el, message) {
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(el._noteTimer);
  el._noteTimer = setTimeout(() => el.classList.remove("visible"), 2600);
}

// Ícone de checkmark do card escolhido — SVG em vez de emoji/texto, fica
// legível em qualquer tamanho e não compete por espaço com a nota.
const CHOSEN_CHECK_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
  'stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

function buildCandidateThumb(candidate, index, isChosen) {
  const card = document.createElement("button");
  card.className = "candidate-card" + (isChosen ? " chosen" : "");
  card.type = "button";
  card.dataset.index = index;
  card.title = candidate.ai_reasoning || "";

  const media = document.createElement("div");
  media.className = "candidate-media";

  const img = document.createElement("img");
  img.src = candidate.thumbnail_url;
  img.loading = "lazy";
  img.alt = `Candidato ${index + 1}`;
  media.appendChild(img);

  // nota de relevância com cor por faixa: verde recomendado, âmbar alternativa,
  // vermelho abaixo do mínimo (esses nem chegam a ser usados). Candidato
  // enviado manualmente não passa pelo ranking — mostra "própria" no lugar,
  // em vez de esconder o selo e deixar a mídia sem nenhum indicador.
  if (candidate.source === "manual") {
    const manualBadge = document.createElement("span");
    manualBadge.className = "candidate-manual-badge";
    manualBadge.textContent = "Própria";
    media.appendChild(manualBadge);
  } else if (typeof candidate.relevance_score === "number") {
    const faixa =
      candidate.relevance_score >= 80 ? "alta" : candidate.relevance_score >= 60 ? "media" : "baixa";
    const scoreBadge = document.createElement("span");
    scoreBadge.className = `candidate-score-badge ${faixa}`;
    scoreBadge.textContent = candidate.relevance_score;
    media.appendChild(scoreBadge);
  }

  if (isChosen) {
    const check = document.createElement("span");
    check.className = "candidate-chosen-check";
    check.innerHTML = CHOSEN_CHECK_SVG;
    check.title = "Escolhido pra esta cena";
    media.appendChild(check);
  }

  card.appendChild(media);

  // Rodapé em fluxo normal (não sobreposto à imagem): tipo, duração e fonte
  // truncam com "..." se não couberem, em vez de vazar por cima de outra
  // informação. Crédito de licença vira ícone com o texto completo só no
  // tooltip — o Google Imagens manda uma frase inteira em toda mídia
  // ("Direitos não verificados..."), que nunca caberia como badge solta.
  const info = document.createElement("div");
  info.className = "candidate-info";

  const infoText = document.createElement("span");
  infoText.className = "candidate-info-text";
  const tipo =
    candidate.media_type === "image" ? "Foto" : `Vídeo · ${Math.round(candidate.duration || 0)}s`;
  infoText.textContent = `${tipo} · ${candidate.source}`;
  infoText.title = infoText.textContent;
  info.appendChild(infoText);

  if (candidate.attribution) {
    const creditIcon = document.createElement("span");
    creditIcon.className = "candidate-credit-icon";
    creditIcon.textContent = "©";
    creditIcon.title = `${candidate.attribution.license} — ${candidate.attribution.author}`;
    info.appendChild(creditIcon);
  }

  card.appendChild(info);
  return card;
}

// Qual shot recebe uma imagem colada (Ctrl+V) — só um por vez. Armado ao
// clicar num botão/tile de envio (o mesmo clique que abre o seletor de
// arquivo): se o usuário cancelar o seletor e colar em vez de escolher um
// arquivo, o Ctrl+V vai pro shot certo mesmo assim.
let pasteTarget = null;

function armPasteTarget(target) {
  disarmPasteTarget();
  pasteTarget = target;
  target.el.classList.add("armed");
  flashNote(target.noteEl, "Pronto — escolha um arquivo ou cole (Ctrl+V) uma imagem copiada.");
}

function disarmPasteTarget() {
  if (pasteTarget) pasteTarget.el.classList.remove("armed");
  pasteTarget = null;
}

document.addEventListener("paste", (e) => {
  if (!pasteTarget) return;
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const item of items) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      e.preventDefault();
      const blob = item.getAsFile();
      const ext = item.type.split("/")[1] || "png";
      const file = new File([blob], `colado.${ext}`, { type: item.type });
      const target = pasteTarget;
      disarmPasteTarget();
      target.el.classList.add("loading");
      uploadManualMedia(target.jobId, target.beatId, target.slot, file, target.noteEl, target.onDone).finally(
        () => target.el.classList.remove("loading")
      );
      return;
    }
  }
});

async function uploadManualMedia(jobId, beatId, slot, file, noteEl, onDone) {
  clearError();
  flashNote(noteEl, "Enviando arquivo...");
  const body = new FormData();
  body.append("file", file);
  try {
    const resp = await fetch(`/api/jobs/${jobId}/footage-candidates/${beatId}/${slot}/upload`, {
      method: "POST",
      body,
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `Erro ao enviar arquivo (${resp.status})`);
    }
    onDone();
  } catch (err) {
    showError(err.message);
    flashNote(noteEl, "Falhou — veja o erro acima.");
  }
}

// Tile extra no fim da grade de candidatos, no mesmo tamanho dos outros —
// clicar abre o seletor de arquivo do sistema. <input> fica fora do <button>
// (só escondido) porque botão não pode conter elemento interativo aninhado.
function buildUploadTile(jobId, beatId, slot, noteEl, onDone) {
  const wrap = document.createElement("span");
  wrap.className = "upload-wrap";

  const tile = document.createElement("button");
  tile.className = "candidate-upload-tile";
  tile.type = "button";
  tile.title = "Enviar um vídeo ou foto próprio pra esta cena, ou colar (Ctrl+V) uma imagem copiada";

  const icon = document.createElement("span");
  icon.className = "candidate-upload-icon";
  icon.textContent = "+";
  const label = document.createElement("span");
  label.className = "candidate-upload-label";
  label.textContent = "Enviar mídia";
  tile.append(icon, label);

  const input = document.createElement("input");
  input.type = "file";
  input.accept = "video/*,image/*";
  input.className = "hidden";
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (!file) return;
    disarmPasteTarget();
    tile.classList.add("loading");
    uploadManualMedia(jobId, beatId, slot, file, noteEl, onDone).finally(() => {
      tile.classList.remove("loading");
      input.value = "";
    });
  });
  tile.addEventListener("click", () => {
    armPasteTarget({ jobId, beatId, slot, noteEl, onDone, el: tile });
    input.click();
  });
  wrap.append(tile, input);
  return wrap;
}

// Mesma ideia, só que como botão de texto solto — usado nos cards de texto,
// que não têm uma grade de candidatos pra encaixar um tile.
function buildUploadButton(jobId, beatId, slot, noteEl, onDone) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost review-concept-upload";
  btn.textContent = "Enviar mídia própria";
  btn.title = "Enviar um arquivo, ou colar (Ctrl+V) uma imagem copiada";

  const input = document.createElement("input");
  input.type = "file";
  input.accept = "video/*,image/*";
  input.className = "hidden";
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (!file) return;
    disarmPasteTarget();
    btn.disabled = true;
    uploadManualMedia(jobId, beatId, slot, file, noteEl, onDone).finally(() => {
      btn.disabled = false;
      input.value = "";
    });
  });
  btn.addEventListener("click", () => {
    armPasteTarget({ jobId, beatId, slot, noteEl, onDone, el: btn });
    input.click();
  });

  const wrap = document.createElement("span");
  wrap.className = "upload-wrap";
  wrap.append(btn, input);
  return wrap;
}

async function submitYoutubeClip(jobId, beatId, slot, url, start, end, noteEl, onDone) {
  clearError();
  if (!url.trim()) {
    flashNote(noteEl, "Cole um link do YouTube.");
    return false;
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    flashNote(noteEl, "Informe início e fim válidos (fim maior que início).");
    return false;
  }
  flashNote(noteEl, "Baixando o trecho do YouTube...");
  try {
    const resp = await fetch(`/api/jobs/${jobId}/footage-candidates/${beatId}/${slot}/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.trim(), start_seconds: start, end_seconds: end }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `Erro ao baixar o trecho (${resp.status})`);
    }
    onDone();
    return true;
  } catch (err) {
    showError(err.message);
    flashNote(noteEl, "Falhou — veja o erro acima.");
    return false;
  }
}

// Monta os 3 campos (link, início, fim) + ações — usado tanto dentro do
// tile do grid quanto no painel do card de texto.
function buildYoutubeForm(jobId, beatId, slot, noteEl, onDone, onCancel) {
  const form = document.createElement("div");
  form.className = "youtube-form";

  const urlInput = document.createElement("input");
  urlInput.type = "text";
  urlInput.placeholder = "Link do vídeo";
  urlInput.className = "youtube-form-input";
  urlInput.addEventListener("click", (e) => e.stopPropagation());

  const rangeRow = document.createElement("div");
  rangeRow.className = "youtube-form-range";
  const startInput = document.createElement("input");
  startInput.type = "number";
  startInput.min = "0";
  startInput.placeholder = "Início (s)";
  startInput.className = "youtube-form-input-sm";
  startInput.addEventListener("click", (e) => e.stopPropagation());
  const endInput = document.createElement("input");
  endInput.type = "number";
  endInput.min = "0";
  endInput.placeholder = "Fim (s)";
  endInput.className = "youtube-form-input-sm";
  endInput.addEventListener("click", (e) => e.stopPropagation());
  rangeRow.append(startInput, endInput);

  const actions = document.createElement("div");
  actions.className = "youtube-form-actions";
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "ghost";
  confirmBtn.textContent = "Usar trecho";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "ghost";
  cancelBtn.textContent = "Cancelar";
  actions.append(confirmBtn, cancelBtn);

  form.append(urlInput, rangeRow, actions);

  cancelBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    onCancel();
  });

  confirmBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    const ok = await submitYoutubeClip(
      jobId, beatId, slot, urlInput.value, Number(startInput.value), Number(endInput.value), noteEl, onDone
    );
    if (!ok) {
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  });

  requestAnimationFrame(() => urlInput.focus());
  return form;
}

// Tile extra no grid, ao lado do "Enviar mídia" — clique expande o mesmo
// tile num formulário de link + início/fim em vez de abrir seletor de arquivo.
function buildYoutubeTile(jobId, beatId, slot, noteEl, onDone) {
  const tile = document.createElement("div");
  tile.className = "candidate-upload-tile candidate-youtube-tile";
  tile.tabIndex = 0;
  tile.setAttribute("role", "button");
  tile.title = "Colar um link do YouTube e recortar um trecho pra esta cena";

  function collapse() {
    tile.innerHTML = "";
    tile.classList.remove("expanded");
    const icon = document.createElement("span");
    icon.className = "candidate-upload-icon";
    icon.textContent = "▶";
    const label = document.createElement("span");
    label.className = "candidate-upload-label";
    label.textContent = "Link do YouTube";
    tile.append(icon, label);
  }

  function expand() {
    tile.classList.add("expanded");
    tile.innerHTML = "";
    tile.appendChild(buildYoutubeForm(jobId, beatId, slot, noteEl, onDone, collapse));
  }

  tile.addEventListener("click", () => {
    if (!tile.classList.contains("expanded")) expand();
  });
  tile.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && !tile.classList.contains("expanded")) {
      e.preventDefault();
      expand();
    }
  });

  collapse();
  return tile;
}

// Mesma ideia em forma de botão + painel que abre abaixo da linha — usado
// nos cards de texto (mesmo lugar do "Enviar mídia própria").
function buildYoutubeButton(jobId, beatId, slot, noteEl, onDone) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost review-concept-upload";
  btn.textContent = "Link do YouTube";
  btn.title = "Colar um link do YouTube e recortar um trecho pra esta cena";

  const panel = document.createElement("div");
  panel.className = "youtube-form-panel hidden";

  btn.addEventListener("click", () => {
    const opening = panel.classList.contains("hidden");
    panel.innerHTML = "";
    if (opening) {
      panel.appendChild(
        buildYoutubeForm(jobId, beatId, slot, noteEl, onDone, () => panel.classList.add("hidden"))
      );
    }
    panel.classList.toggle("hidden", !opening);
  });

  const wrap = document.createElement("span");
  wrap.className = "upload-wrap";
  wrap.append(btn, panel);
  return wrap;
}

function renderReviewBeats(jobId, beatsData) {
  reviewBeats.innerHTML = "";
  for (const beat of beatsData) {
    const wrap = document.createElement("div");
    wrap.className = "review-beat";
    wrap.dataset.beatId = beat.beat_id;

    const text = document.createElement("div");
    text.className = "review-beat-text";
    text.textContent = `${beat.beat_id + 1}. ${beat.text}`;
    wrap.appendChild(text);

    if (beat.entities && beat.entities.length) {
      const ents = document.createElement("div");
      ents.className = "review-entities";
      ents.textContent = `Entidades: ${beat.entities.join(" · ")}`;
      wrap.appendChild(ents);
    }

    // cenas onde nada atingiu o mínimo de relevância viraram card de texto —
    // mostrar deixa explícito que a ferramenta preferiu não forçar footage.
    // Cada card ainda tem o slot do shot que o gerou (mesmo quando a busca
    // não achou candidato nenhum), então dá pra enviar mídia própria pra ele.
    (beat.concept_cards || []).forEach((card) => {
      const el = document.createElement("div");
      el.className = "review-concept";

      const text = document.createElement("span");
      text.textContent = `Card de texto (${card.seconds}s): "${card.text}"`;
      el.appendChild(text);

      if (card.slot !== null && card.slot !== undefined) {
        const note = document.createElement("span");
        note.className = "review-shot-note";
        const uploadBtn = buildUploadButton(jobId, beat.beat_id, card.slot, note, () =>
          loadFootageReview(jobId)
        );
        const youtubeBtn = buildYoutubeButton(jobId, beat.beat_id, card.slot, note, () =>
          loadFootageReview(jobId)
        );
        el.append(uploadBtn, youtubeBtn, note);
      }

      wrap.appendChild(el);
    });

    if (!beat.shots.length) {
      const empty = document.createElement("div");
      empty.className = "review-beat-empty";
      // shots vazio quer dizer coisas diferentes: modo de mídia própria
      // nunca salva candidato pra revisão (a mídia está lá, só não dá pra
      // trocar por aqui) — bem diferente de "a busca não achou nada bom".
      empty.textContent = beat.used_own_media
        ? "Este bloco usa mídia do seu lote próprio — sem revisão disponível nesse modo."
        : "Nenhum candidato encontrado — usando footage genérico (fallback).";
      wrap.appendChild(empty);
      reviewBeats.appendChild(wrap);
      continue;
    }

    // um bloco longo é preenchido por vários shots que se revezam na tela;
    // cada um tem sua própria lista de candidatos pra trocar
    beat.shots.forEach((shot) => {
      const shotWrap = document.createElement("div");
      shotWrap.className = "review-shot";

      const head = document.createElement("div");
      head.className = "review-shot-head";
      const label = document.createElement("span");
      label.className = "review-shot-label";
      const usage = shot.usage;
      const estrategia = shot.visual_strategy ? ` · ${shot.visual_strategy}` : "";
      label.textContent = usage
        ? `Cena ${shot.slot + 1}${estrategia} · ${usage.scene_count}× na tela · ${usage.screen_seconds}s no total`
        : `Cena ${shot.slot + 1}${estrategia}`;
      const note = document.createElement("span");
      note.className = "review-shot-note";
      head.append(label, note);
      shotWrap.appendChild(head);

      const preview = document.createElement("div");
      preview.className = "candidate-preview hidden";
      const previewVideo = document.createElement("video");
      previewVideo.controls = true;
      previewVideo.preload = "none";
      // Candidato do YouTube não tem arquivo de vídeo direto pra preview —
      // "url" é a página de watch, que o <video> não consegue tocar. Usa o
      // player embed do YouTube recortado no mesmo trecho que seria baixado.
      const previewFrame = document.createElement("iframe");
      previewFrame.className = "candidate-preview-frame hidden";
      previewFrame.allow = "autoplay; encrypted-media";
      previewFrame.allowFullscreen = true;
      preview.append(previewVideo, previewFrame);

      const grid = document.createElement("div");
      grid.className = "review-candidates";
      shot.candidates.forEach((candidate, index) => {
        const thumb = buildCandidateThumb(candidate, index, index === shot.chosen_index);
        thumb.addEventListener("click", () =>
          swapCandidate(jobId, beat.beat_id, shot.slot, index, thumb, grid, note)
        );

        if (candidate.media_type !== "image") {
          const playBtn = document.createElement("span");
          playBtn.className = "candidate-play";
          playBtn.textContent = "▶";
          playBtn.title = "Pré-visualizar o clipe";
          playBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            preview.classList.remove("hidden");
            if (candidate.source === "youtube" && candidate.youtube_video_id) {
              previewVideo.pause();
              previewVideo.removeAttribute("src");
              previewVideo.classList.add("hidden");
              const [start, end] = candidate.youtube_segment || [0, 0];
              previewFrame.classList.remove("hidden");
              previewFrame.src =
                `https://www.youtube.com/embed/${candidate.youtube_video_id}` +
                `?start=${Math.floor(start)}&end=${Math.ceil(end)}&autoplay=1`;
            } else {
              previewFrame.classList.add("hidden");
              previewFrame.src = "";
              previewVideo.classList.remove("hidden");
              previewVideo.src = candidate.url;
              previewVideo.play().catch(() => {});
            }
          });
          thumb.querySelector(".candidate-media").appendChild(playBtn);
        }
        grid.appendChild(thumb);
      });
      grid.appendChild(
        buildUploadTile(jobId, beat.beat_id, shot.slot, note, () => loadFootageReview(jobId))
      );
      grid.appendChild(
        buildYoutubeTile(jobId, beat.beat_id, shot.slot, note, () => loadFootageReview(jobId))
      );

      shotWrap.append(grid, preview);
      wrap.appendChild(shotWrap);
    });

    reviewBeats.appendChild(wrap);
  }
}

async function swapCandidate(jobId, beatId, slot, index, thumbEl, gridEl, noteEl) {
  if (thumbEl.classList.contains("loading")) return;
  // clicar no que já está escolhido antes não dava sinal nenhum e parecia
  // que a ferramenta tinha travado
  if (thumbEl.classList.contains("chosen")) {
    flashNote(noteEl, "Esse já é o clipe escolhido.");
    return;
  }
  clearError();
  thumbEl.classList.add("loading");
  flashNote(noteEl, "Baixando o clipe...");
  try {
    const resp = await fetch(`/api/jobs/${jobId}/footage-candidates/${beatId}/${slot}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_index: Number(index) }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao trocar footage (${resp.status})`);
    }
    const result = await resp.json();
    markChosen(gridEl, thumbEl);
    flashNote(noteEl, `Trocado em ${result.updated_scenes} cena(s).`);
  } catch (err) {
    showError(err.message);
    flashNote(noteEl, "Falhou — veja o erro acima.");
  } finally {
    thumbEl.classList.remove("loading");
  }
}

async function handleConfirmRender() {
  if (!currentJobId) return;
  clearError();
  confirmRenderBtn.disabled = true;
  try {
    const resp = await fetch(`/api/jobs/${currentJobId}/confirm-render`, { method: "POST" });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao confirmar (${resp.status})`);
    }
    stepReview.classList.add("hidden");
  } catch (err) {
    showError(err.message);
    confirmRenderBtn.disabled = false;
  }
}

function startJobEvents(jobId) {
  currentJobId = jobId;
  const source = new EventSource(`/api/jobs/${jobId}/events`);

  source.addEventListener("beat_progress", (e) => {
    const data = JSON.parse(e.data);
    updateBeatStage(data.beat_id, data.stage, data.status);
  });

  source.addEventListener("composition_ready", () => {
    confirmRenderBtn.disabled = false;
    loadFootageReview(jobId);
  });

  source.addEventListener("render_progress", (e) => {
    const data = JSON.parse(e.data);
    renderProgressWrap.classList.remove("hidden");
    const pct = data.total > 0 ? Math.round((data.frame / data.total) * 100) : 0;
    renderProgress.value = pct;
    renderProgressLabel.textContent = `${data.frame}/${data.total} frames`;
  });

  source.addEventListener("render_status", (e) => {
    const data = JSON.parse(e.data);
    renderStatusWrap.classList.remove("hidden");
    renderStatusLabel.textContent = data.message;
  });

  source.addEventListener("job_done", (e) => {
    const data = JSON.parse(e.data);
    resultVideo.src = data.video_url;
    downloadLink.href = data.video_url;
    stepResult.classList.remove("hidden");
    renderStatusWrap.classList.add("hidden");
    renderProgressWrap.classList.add("hidden");
    source.close();
  });

  source.addEventListener("job_error", (e) => {
    const data = JSON.parse(e.data);
    showError(`Falha ao gerar o vídeo: ${data.message}`);
    source.close();
  });

  source.onerror = () => {
    showError("Conexão com o servidor perdida. Verifique se o servidor ainda está rodando.");
    source.close();
  };
}

async function handleGenerateVideo() {
  clearError();
  if (mediaMode === "ai_search" && selectedSources.size === 0) {
    showError("Selecione ao menos uma fonte de mídia.");
    return;
  }
  if (mediaMode === "own_media" && poolItemCount === 0) {
    showError("Envie ao menos uma foto ou vídeo antes de gerar.");
    return;
  }
  generateVideoBtn.disabled = true;
  stepReview.classList.add("hidden");
  reviewBeats.innerHTML = "";
  disarmPasteTarget();
  stepResult.classList.add("hidden");
  renderProgressWrap.classList.add("hidden");
  renderStatusWrap.classList.add("hidden");
  renderProgress.value = 0;

  try {
    const resp = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug: draftSlug,
        blocks: blocks.map((b) => ({ id: b.id, text: b.text })),
        voice_id: selectedVoiceId,
        language: selectedVoiceLanguage,
        speed: Number(speedSlider.value),
        remote_render: remoteRenderToggle.checked,
        sources: Array.from(selectedSources),
        google_images_recency: recencySelect.value,
        media_mode: mediaMode,
        channel: currentChannel,
      }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao criar job (${resp.status})`);
    }
    const { job_id, beats } = await resp.json();
    renderBeatsQueue(beats);
    startJobEvents(job_id);
  } catch (err) {
    showError(err.message);
  } finally {
    updateGenerateVideoButton();
  }
}

// --- Eventos ---

for (const radio of mediaModeRadios) {
  radio.addEventListener("change", () => {
    if (radio.checked) setMediaMode(radio.value);
  });
}

ownMediaAddBtn.addEventListener("click", () => ownMediaInput.click());

ownMediaInput.addEventListener("change", async () => {
  await uploadPoolFiles(Array.from(ownMediaInput.files));
  ownMediaInput.value = ""; // permite reenviar o mesmo arquivo depois, se precisar
});

channelSelect.addEventListener("change", async () => {
  currentChannel = channelSelect.value;
  localStorage.setItem("lastChannel", currentChannel);
  await loadFavorites();
  await loadIdentity();
  await loadVoices(languageSelect.value);
});

newChannelBtn.addEventListener("click", async () => {
  const name = newChannelInput.value.trim();
  if (!name) return;
  await fetch("/api/channels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  newChannelInput.value = "";
  currentChannel = name;
  await loadChannels();
});


favoritesTabs.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-filter]");
  if (!btn) return;
  filterMode = btn.dataset.filter;
  updateFavoritesTabs();
  renderVoicesGrid();
});

languageSelect.addEventListener("change", () => loadVoices(languageSelect.value));
speedSlider.addEventListener("input", () => {
  speedValue.textContent = `${Number(speedSlider.value).toFixed(2)}x`;
});
blockText.addEventListener("input", updateGenerateBlockButton);
generateBlockBtn.addEventListener("click", generateBlock);
generateVideoBtn.addEventListener("click", handleGenerateVideo);
confirmRenderBtn.addEventListener("click", handleConfirmRender);
newDraftBtn.addEventListener("click", resetDraft);

loadSerperStatus();
loadFootageSources();

loadChannels();
