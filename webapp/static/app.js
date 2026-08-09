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
const favoritesTabs = document.getElementById("favorites-tabs");
const voicesStatus = document.getElementById("voices-status");
const voicesGrid = document.getElementById("voices-grid");
const voiceLockedNote = document.getElementById("voice-locked-note");
const blockText = document.getElementById("block-text");
const generateBlockBtn = document.getElementById("generate-block-btn");
const blocksList = document.getElementById("blocks-list");
const generateVideoBtn = document.getElementById("generate-video-btn");
const remoteRenderToggle = document.getElementById("remote-render-toggle");
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
  await loadVoices(languageSelect.value);
}

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
      }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao gerar narração (${resp.status})`);
    }
    const result = await resp.json();
    blocks.push({ id: blockId, text, audioUrl: result.audio_url, duration: result.duration_seconds });
    nextBlockId += 1;
    blockText.value = "";
    lockDraft();
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  } finally {
    generateBlockBtn.textContent = "Gerar narração deste bloco";
    updateGenerateBlockButton();
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
    head.append(number, actions);

    const text = document.createElement("div");
    text.className = "block-text";
    text.textContent = block.text;

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = block.audioUrl + `?t=${Date.now()}`;

    li.append(head, text, audio);
    blocksList.appendChild(li);
  });
  generateVideoBtn.disabled = blocks.length === 0;
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
    renderBlocksList();
  } catch (err) {
    showError(err.message);
  }
}

function removeBlock(blockId) {
  blocks = blocks.filter((b) => b.id !== blockId);
  renderBlocksList();
  if (blocks.length === 0) unlockDraft();
}

function resetDraft() {
  draftSlug = null;
  draftLocked = false;
  selectedVoiceId = null;
  blocks = [];
  nextBlockId = 0;
  blockText.value = "";
  blocksList.innerHTML = "";
  currentJobId = null;
  stepReview.classList.add("hidden");
  reviewBeats.innerHTML = "";
  stepProgress.classList.add("hidden");
  stepResult.classList.add("hidden");
  renderProgressWrap.classList.add("hidden");
  renderStatusWrap.classList.add("hidden");
  clearError();
  unlockDraft();
  updateGenerateBlockButton();
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

    if (!beat.candidates.length) {
      const empty = document.createElement("div");
      empty.className = "review-beat-empty";
      empty.textContent = "Nenhum candidato encontrado — usando footage genérico (fallback).";
      wrap.appendChild(empty);
      reviewBeats.appendChild(wrap);
      continue;
    }

    const grid = document.createElement("div");
    grid.className = "review-candidates";
    beat.candidates.forEach((candidate, index) => {
      const thumb = document.createElement("button");
      thumb.className = "candidate-thumb" + (index === beat.chosen_index ? " chosen" : "");
      thumb.dataset.index = index;

      const img = document.createElement("img");
      img.src = candidate.thumbnail_url;
      img.loading = "lazy";
      img.alt = `Candidato ${index + 1}`;
      thumb.appendChild(img);

      const typeBadge = document.createElement("span");
      typeBadge.className = "candidate-type-badge";
      typeBadge.textContent = candidate.media_type === "image" ? "Foto" : "Vídeo";
      thumb.appendChild(typeBadge);

      if (index === beat.chosen_index) {
        const badge = document.createElement("span");
        badge.className = "candidate-badge";
        badge.textContent = "Escolhido";
        thumb.appendChild(badge);
      }

      thumb.title = candidate.ai_reasoning || "";
      thumb.addEventListener("click", () => swapCandidate(jobId, beat.beat_id, index, thumb, grid));
      grid.appendChild(thumb);
    });
    wrap.appendChild(grid);
    reviewBeats.appendChild(wrap);
  }
}

async function swapCandidate(jobId, beatId, index, thumbEl, gridEl) {
  if (thumbEl.classList.contains("chosen") || thumbEl.classList.contains("loading")) return;
  clearError();
  thumbEl.classList.add("loading");
  try {
    const resp = await fetch(`/api/jobs/${jobId}/footage-candidates/${beatId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_index: Number(index) }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao trocar footage (${resp.status})`);
    }
    gridEl.querySelectorAll(".candidate-thumb").forEach((el) => {
      el.classList.remove("chosen");
      const badge = el.querySelector(".candidate-badge");
      if (badge) badge.remove();
    });
    thumbEl.classList.add("chosen");
    const badge = document.createElement("span");
    badge.className = "candidate-badge";
    badge.textContent = "Escolhido";
    thumbEl.appendChild(badge);
  } catch (err) {
    showError(err.message);
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
  generateVideoBtn.disabled = true;
  stepReview.classList.add("hidden");
  reviewBeats.innerHTML = "";
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
        remote_render: remoteRenderToggle.checked,
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
    generateVideoBtn.disabled = blocks.length === 0;
  }
}

// --- Eventos ---

channelSelect.addEventListener("change", async () => {
  currentChannel = channelSelect.value;
  localStorage.setItem("lastChannel", currentChannel);
  await loadFavorites();
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
blockText.addEventListener("input", updateGenerateBlockButton);
generateBlockBtn.addEventListener("click", generateBlock);
generateVideoBtn.addEventListener("click", handleGenerateVideo);
confirmRenderBtn.addEventListener("click", handleConfirmRender);
newDraftBtn.addEventListener("click", resetDraft);

loadChannels();
