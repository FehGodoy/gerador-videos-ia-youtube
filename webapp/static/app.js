const STAGE_LABELS = {
  narration: "Narração",
  keywords: "Palavras-chave",
  footage: "Footage",
  captions: "Legendas",
};

const scriptText = document.getElementById("script-text");
const languageSelect = document.getElementById("language-select");
const voicesStatus = document.getElementById("voices-status");
const voicesGrid = document.getElementById("voices-grid");
const generateBtn = document.getElementById("generate-btn");
const stepProgress = document.getElementById("step-progress");
const beatsList = document.getElementById("beats-list");
const renderProgressWrap = document.getElementById("render-progress-wrap");
const renderProgress = document.getElementById("render-progress");
const renderProgressLabel = document.getElementById("render-progress-label");
const stepResult = document.getElementById("step-result");
const resultVideo = document.getElementById("result-video");
const downloadLink = document.getElementById("download-link");
const errorMessage = document.getElementById("error-message");

let selectedVoiceId = null;

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.classList.remove("hidden");
}

function clearError() {
  errorMessage.classList.add("hidden");
}

function updateGenerateButton() {
  generateBtn.disabled = !selectedVoiceId || !scriptText.value.trim();
}

async function loadVoices(language) {
  selectedVoiceId = null;
  updateGenerateButton();
  voicesGrid.innerHTML = "";
  voicesStatus.textContent = "Carregando vozes (a primeira vez neste idioma pode demorar um pouco)...";
  voicesStatus.classList.remove("hidden");

  try {
    const resp = await fetch(`/api/voices?language=${encodeURIComponent(language)}`);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Erro ao carregar vozes (${resp.status})`);
    }
    const voices = await resp.json();
    renderVoices(voices);
    voicesStatus.classList.add("hidden");
  } catch (err) {
    voicesStatus.textContent = `Não foi possível carregar as vozes: ${err.message}`;
  }
}

function renderVoices(voices) {
  voicesGrid.innerHTML = "";
  for (const voice of voices) {
    const card = document.createElement("div");
    card.className = "voice-card";
    card.dataset.voiceId = voice.id;

    const name = document.createElement("div");
    name.className = "voice-name";
    name.textContent = voice.name;

    const desc = document.createElement("div");
    desc.className = "voice-desc";
    desc.textContent = voice.description || "";

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = voice.preview_url;

    const selectBtn = document.createElement("button");
    selectBtn.textContent = "Selecionar";
    selectBtn.addEventListener("click", () => {
      selectedVoiceId = voice.id;
      document.querySelectorAll(".voice-card").forEach((el) => el.classList.remove("selected"));
      card.classList.add("selected");
      updateGenerateButton();
    });

    card.append(name, desc, audio, selectBtn);
    voicesGrid.appendChild(card);
  }
}

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
  if (status === "running" || status === "done") {
    badge.classList.add(status);
  }
}

function startJobEvents(jobId) {
  const source = new EventSource(`/api/jobs/${jobId}/events`);

  source.addEventListener("beat_progress", (e) => {
    const data = JSON.parse(e.data);
    updateBeatStage(data.beat_id, data.stage, data.status);
  });

  source.addEventListener("render_progress", (e) => {
    const data = JSON.parse(e.data);
    renderProgressWrap.classList.remove("hidden");
    const pct = data.total > 0 ? Math.round((data.frame / data.total) * 100) : 0;
    renderProgress.value = pct;
    renderProgressLabel.textContent = `${data.frame}/${data.total} frames`;
  });

  source.addEventListener("job_done", (e) => {
    const data = JSON.parse(e.data);
    resultVideo.src = data.video_url;
    downloadLink.href = data.video_url;
    stepResult.classList.remove("hidden");
    source.close();
  });

  source.addEventListener("job_error", (e) => {
    const data = JSON.parse(e.data);
    showError(`Falha ao gerar o vídeo: ${data.message}`);
    source.close();
  });

  source.onerror = () => {
    // conexão SSE caiu sem um job_done/job_error explícito (ex: servidor reiniciou)
    showError("Conexão com o servidor perdida. Verifique se o servidor ainda está rodando.");
    source.close();
  };
}

async function handleGenerate() {
  clearError();
  generateBtn.disabled = true;
  stepResult.classList.add("hidden");
  renderProgressWrap.classList.add("hidden");
  renderProgress.value = 0;

  try {
    const resp = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_text: scriptText.value,
        voice_id: selectedVoiceId,
        language: languageSelect.value,
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
    updateGenerateButton();
  }
}

languageSelect.addEventListener("change", () => loadVoices(languageSelect.value));
scriptText.addEventListener("input", updateGenerateButton);
generateBtn.addEventListener("click", handleGenerate);

loadVoices(languageSelect.value);
