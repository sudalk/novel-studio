const app = document.querySelector("#app");
const state = {
  config: null,
  voices: [],
  examples: [],
  novels: [],
  novel: null,
  episode: null,
  job: null,
  busy: false,
  error: "",
  inspector: "voice",
  inspectorEpisode: "",
};
let pollToken = 0;
const pending = new Map();

const STATUS = {
  imported: "已导入",
  scripted: "已拆解",
  voiced: "已配音",
  illustrated: "已绘图",
  rendered: "已成片",
};
const JOBS = {
  breakdown: "拆解剧本",
  tts: "合成配音",
  "line-tts": "重配这一句",
  illustrate: "生成画面",
  video: "合成视频",
  pipeline: "一键成片",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (_error) {
      /* 保留状态文本 */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response;
}

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function route() {
  pollToken += 1;
  state.error = "";
  const parts = location.hash.replace(/^#/, "").split("/").filter(Boolean);
  try {
    if (parts[0] === "novels" && parts[1]) {
      state.novel = await api(`/api/novels/${parts[1]}`);
      state.episode = null;
      renderNovel();
      return;
    }
    if (parts[0] === "episodes" && parts[1]) {
      state.episode = await api(`/api/episodes/${parts[1]}`);
      if (!state.novel || state.novel.id !== state.episode.novel_id) {
        state.novel = await api(`/api/novels/${state.episode.novel_id}`);
      }
      state.job = state.episode.latest_job;
      renderEpisode();
      if (state.job && (state.job.status === "queued" || state.job.status === "running")) {
        watchJob(state.job);
      }
      return;
    }
    state.novels = await api("/api/novels");
    state.novel = null;
    state.episode = null;
    renderLibrary();
  } catch (error) {
    state.error = error.message;
    renderLibrary();
  }
}

function modeLabel() {
  if (!state.config) return "工作室";
  if (state.config.app_mode === "mock" && state.config.tts_provider === "mock") return "离线演示";
  return "真实接口";
}

function banner() {
  if (!state.config) return "";
  if (state.config.app_mode === "mock" && state.config.tts_provider === "mock") {
    return `<div class="banner">离线演示。剧本、配音和画面都在本机生成，不需要 API 密钥。</div>`;
  }
  const bits = [
    `文本 ${esc(state.config.llm_provider)}`,
    `语音 ${esc(state.config.tts_provider)}`,
    `图像 ${esc(state.config.image_provider)}`,
  ];
  const missing = [];
  if (state.config.llm_provider === "openai" && !state.config.llm_configured) missing.push("OPENAI_API_KEY");
  if (state.config.tts_provider === "mosi" && !state.config.moss_configured) missing.push("MOSI_API_KEY");
  if (state.config.image_provider === "openai" && !state.config.image_configured) missing.push("图像密钥");
  const warn = missing.length ? `还缺：${missing.join("、")}` : "密钥已配置";
  return `<div class="banner">真实接口。${bits.join(" · ")}。${esc(warn)}</div>`;
}

function jobbar() {
  const job = state.job;
  if (!job || job.status === "succeeded") return "";
  const label = JOBS[job.kind] || job.kind;
  return `<div class="jobbar ${esc(job.status)}">
    <strong>${esc(label)}</strong> · ${esc(job.message || job.status)}
    ${job.error ? `<div class="note">${esc(job.error)}</div>` : ""}
    <div class="progress"><span style="width:${job.progress || 0}%"></span></div>
  </div>`;
}

function sidebar() {
  const novel = state.novel;
  const current = state.episode?.id || "";
  const episodes = (novel?.episodes || []).map((episode, index) => `
    <button class="nav-ep ${episode.id === current ? "on" : ""}" data-nav-episode="${esc(episode.id)}">
      <span>${String(index + 1).padStart(2, "0")}</span>
      ${esc(episode.title)}
    </button>`).join("");
  return `<aside class="sidebar">
    <button class="brand" id="home">
      <span class="mark">章</span>
      <span class="brand-copy"><strong>章映</strong><em>小说影像工作室</em></span>
    </button>
    <nav class="nav">
      <button class="nav-item ${novel || state.episode ? "" : "on"}" id="nav-library">作品库</button>
      ${novel ? `<div class="nav-label">${esc(novel.title)}</div><div class="nav-eps">${episodes}</div>` : ""}
    </nav>
    <div class="sidebar-foot"><span class="mode-pill">${esc(modeLabel())}</span></div>
  </aside>`;
}

function shell(inner, view) {
  document.body.dataset.view = view;
  app.innerHTML = `<div class="app">${sidebar()}<div class="main">
    ${banner()}
    ${state.error ? `<div class="toast">${esc(state.error)}</div>` : ""}
    ${jobbar()}
    ${inner}
  </div></div>`;
  document.querySelector("#home").onclick = () => go("#/");
  document.querySelector("#nav-library").onclick = () => go("#/");
  document.querySelectorAll("[data-nav-episode]").forEach((node) => {
    node.onclick = () => go(`#/episodes/${node.dataset.navEpisode}`);
  });
}

function renderLibrary() {
  const cards = state.novels.length
    ? state.novels.map((novel) => `
        <button class="work" data-novel="${esc(novel.id)}">
          <strong>${esc(novel.title)}</strong>
          <span>${novel.episode_count} 集 · ${novel.character_count} 个角色</span>
        </button>`).join("")
    : `<div class="empty">还没有作品。贴一段小说，或先载入《雨夜来信》。</div>`;
  shell(`
    <div class="page">
      <header class="page-head">
        <div>
          <h1>作品库</h1>
          <p class="lede">粘贴连载正文，或上传 .txt / .md。单独成行的「第X章」「第X集」会自动分成一集。</p>
        </div>
        <div class="head-actions"><button class="btn" id="load-sample" type="button">载入示例</button></div>
      </header>
      <div class="library">
        <section class="card import-card" id="import">
          <form id="import-form">
            <div class="field"><label for="import-title">作品标题，可留空</label><input id="import-title" name="title" type="text" maxlength="80" placeholder="例如：雨夜来信"></div>
            <div class="editor-shell">
              <textarea id="import-text" name="text" placeholder="第1集 雨夜&#10;&#10;雨下了整整一夜。"></textarea>
              <div class="composer-bar">
                <label class="btn file">上传文本<input id="import-file" type="file" accept=".txt,.md,text/plain,text/markdown" hidden></label>
                <button class="btn primary" type="submit">导入正文</button>
              </div>
            </div>
          </form>
        </section>
        <section class="library-list">${cards}</section>
      </div>
    </div>`, "library");
  document.querySelector("#load-sample").onclick = () => importSample();
  document.querySelectorAll("[data-novel]").forEach((node) => {
    node.onclick = () => go(`#/novels/${node.dataset.novel}`);
  });
  document.querySelector("#import-form").onsubmit = async (event) => {
    event.preventDefault();
    const title = document.querySelector("#import-title").value.trim();
    const text = document.querySelector("#import-text").value;
    if (!text.trim()) {
      state.error = "请先贴上正文";
      renderLibrary();
      return;
    }
    await importText(title, text);
  };
  document.querySelector("#import-file").onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    const title = document.querySelector("#import-title").value.trim();
    if (title) data.append("title", title);
    await run(async () => {
      const novel = await api("/api/novels/upload", { method: "POST", body: data });
      go(`#/novels/${novel.id}`);
    });
  };
}

function renderNovel() {
  const novel = state.novel;
  shell(`
    <div class="page">
      <header class="page-head">
        <div>
          <button class="text-back" id="back" type="button">作品库</button>
          <h1>${esc(novel.title)}</h1>
          <p class="meta">${novel.episode_count} 集 · ${novel.character_count} 个角色</p>
        </div>
        <button class="btn danger" id="delete-novel" type="button">删除作品</button>
      </header>
      <section class="card block style-card">
        <h2>画面风格</h2>
        <p class="lede">整部作品共用。每一镜的提示词会接上这段 STYLE、统一的硬性约束，以及角色外观。</p>
        <textarea id="novel-style" rows="4" placeholder="${esc(novel.style_default || "")}">${esc(novel.style || "")}</textarea>
        <p class="meta" id="style-status">留空则使用上面的默认画风。改完后，重新生成画面才会用到新风格。</p>
      </section>
      <div class="split">
        <section class="card block">
          <h2>分集</h2>
          ${(novel.episodes || []).map((episode, index) => `
            <button class="ep-row" data-episode="${esc(episode.id)}" type="button">
              <span class="ep-index">${String(index + 1).padStart(2, "0")}</span>
              <span class="ep-copy">
                <strong>${esc(episode.title)}</strong>
                <em>${esc(STATUS[episode.status] || episode.status)} · 台词 ${episode.line_count}</em>
              </span>
              <span class="flag ${episode.has_video ? "on" : ""}">${episode.has_video ? "已成片" : "未成片"}</span>
            </button>`).join("") || `<div class="empty">还没有分集。</div>`}
        </section>
        <section class="card block">
          <h2>音色设置</h2>
          <p class="lede">每个角色指定一把音色，并写下外观，方便后面的画面保持一致。</p>
          <div class="roster" id="roster">
            ${novel.characters.map((character) => characterCard(character)).join("") || `<div class="empty">拆解剧本后会在这里出现角色。</div>`}
          </div>
          <form id="add-character" class="add-row">
            <input name="name" type="text" placeholder="新角色名字" required maxlength="80">
            <button class="btn" type="submit">添加</button>
          </form>
        </section>
      </div>
    </div>`, "novel");
  document.querySelector("#back").onclick = () => go("#/");
  document.querySelector("#delete-novel").onclick = async () => {
    if (!confirm(`删除《${novel.title}》？`)) return;
    await run(async () => {
      await api(`/api/novels/${novel.id}`, { method: "DELETE" });
      go("#/");
    });
  };
  document.querySelectorAll("[data-episode]").forEach((node) => {
    node.onclick = () => go(`#/episodes/${node.dataset.episode}`);
  });
  bindCharacters(novel.characters);
  const styleBox = document.querySelector("#novel-style");
  let styleTimer = 0;
  styleBox.oninput = () => {
    clearTimeout(styleTimer);
    styleTimer = setTimeout(async () => {
      try {
        const saved = await api(`/api/novels/${novel.id}`, { method: "PATCH", body: { style: styleBox.value } });
        state.novel = saved;
        const status = document.querySelector("#style-status");
        if (status) status.textContent = "画面风格已保存。重新生成画面后才会用到。";
      } catch (error) {
        state.error = error.message;
        const status = document.querySelector("#style-status");
        if (status) status.textContent = error.message;
      }
    }, 400);
  };
  document.querySelector("#add-character").onsubmit = async (event) => {
    event.preventDefault();
    const name = new FormData(event.target).get("name");
    await run(async () => {
      await api(`/api/novels/${novel.id}/characters`, { method: "POST", body: { name } });
      state.novel = await api(`/api/novels/${novel.id}`);
      renderNovel();
    });
  };
}

function avatarStyle(character) {
  if (character.is_narrator) return "background:linear-gradient(135deg,#e8582b,#ff6932)";
  let hash = 0;
  for (const char of character.name || "") hash = (hash * 33 + char.charCodeAt(0)) % 360;
  return `background:hsl(${hash} 52% 46%)`;
}

function voiceCatalog() {
  const seen = new Set();
  const voices = [];
  for (const voice of [...state.voices, ...state.examples]) {
    if (!voice || !voice.id || seen.has(voice.id)) continue;
    seen.add(voice.id);
    voices.push(voice);
  }
  return voices;
}

function voiceById(id) {
  return voiceCatalog().find((voice) => voice.id === id);
}

function characterCard(character) {
  const initial = esc((character.name || "?").slice(0, 1));
  const current = voiceById(character.voice_id);
  const label = current ? current.name : (character.voice_name || "未指定音色");
  const detail = current
    ? [current.language, current.description].filter(Boolean).join(" · ")
    : "从下面选择，或粘贴 voice_id";
  const rows = voiceCatalog().map((voice) => `
    <div class="voice-row ${voice.id === character.voice_id ? "on" : ""}">
      <button type="button" class="voice-pick" data-pick-voice="${esc(voice.id)}">
        <strong>${esc(voice.name)}</strong>
        <em>${esc([voice.language, voice.description].filter(Boolean).join(" · "))}</em>
      </button>
      <button type="button" class="btn small" data-preview-voice="${esc(voice.id)}">试听</button>
    </div>`).join("");
  return `<div class="speaker" data-character="${esc(character.id)}">
    <div class="speaker-top">
      <span class="avatar" style="${avatarStyle(character)}">${initial}</span>
      <div>
        <strong>${esc(character.name)}</strong>
        ${character.is_narrator ? `<span class="tag">旁白</span>` : `<span class="tag">角色</span>`}
      </div>
    </div>
    <div class="voice-current">
      <div>
        <strong data-voice-label>${esc(label)}</strong>
        <p class="meta">${esc(detail)}</p>
      </div>
      <button type="button" class="btn small" data-preview-current>试听</button>
    </div>
    <audio data-preview-audio controls hidden></audio>
    <p class="meta" data-preview-note></p>
    <div class="voice-scroll">${rows}</div>
    <select class="voice-native" data-field="voice" aria-label="音色">${voiceOptions(character.voice_id)}</select>
    <input data-field="voice-custom" type="text" placeholder="或粘贴 voice_id" value="${esc(character.voice_id || "")}">
    <label style="margin-top:10px;display:block">外观描述</label>
    <textarea data-field="description" rows="2">${esc(character.description || "")}</textarea>
    ${character.is_narrator ? "" : `<div class="actions" style="margin-top:8px"><button class="btn small danger" data-delete-character="${esc(character.id)}" type="button">删除角色</button></div>`}
  </div>`;
}

function voiceOptions(selected) {
  const voices = [...state.voices];
  if (selected && !voices.some((voice) => voice.id === selected)) {
    voices.unshift({ id: selected, name: selected, description: "当前音色" });
  }
  const main = voices.map((voice) => `<option value="${esc(voice.id)}" ${voice.id === selected ? "selected" : ""}>${esc(voice.name)}</option>`).join("");
  const examples = state.examples
    .map((voice) => `<option value="${esc(voice.id)}" ${voice.id === selected ? "selected" : ""}>${esc(voice.name)}（示例）</option>`)
    .join("");
  return `<option value="">未指定</option>${main}${examples}`;
}

function bindCharacters(characters) {
  characters.forEach((character) => {
    const root = document.querySelector(`[data-character="${character.id}"]`);
    if (!root) return;
    const save = async () => {
      const select = root.querySelector('[data-field="voice"]');
      const custom = root.querySelector('[data-field="voice-custom"]').value.trim();
      const voiceId = custom || select.value;
      const voice = [...state.voices, ...state.examples].find((item) => item.id === voiceId);
      await api(`/api/characters/${character.id}`, {
        method: "PATCH",
        body: {
          description: root.querySelector('[data-field="description"]').value,
          voice_id: voiceId,
          voice_name: voice ? voice.name : voiceId,
        },
      });
    };
    root.querySelector('[data-field="voice"]').onchange = (event) => {
      root.querySelector('[data-field="voice-custom"]').value = event.target.value;
      markVoice(root, event.target.value);
      run(save);
    };
    root.querySelector('[data-field="voice-custom"]').onchange = (event) => {
      markVoice(root, event.target.value.trim());
      save();
    };
    root.querySelectorAll("[data-pick-voice]").forEach((button) => {
      button.onclick = () => {
        const id = button.dataset.pickVoice;
        const select = root.querySelector('[data-field="voice"]');
        select.value = [...select.options].some((option) => option.value === id) ? id : "";
        root.querySelector('[data-field="voice-custom"]').value = id;
        markVoice(root, id);
        run(save);
      };
    });
    root.querySelector("[data-preview-current]").onclick = () => previewVoice(root);
    root.querySelectorAll("[data-preview-voice]").forEach((button) => {
      button.onclick = () => previewVoice(root, button.dataset.previewVoice);
    });
    root.querySelector('[data-field="description"]').onchange = () => run(save);
    const remove = root.querySelector("[data-delete-character]");
    if (remove) {
      remove.onclick = async () => {
        if (!confirm("删除这个角色？")) return;
        await run(async () => {
          await api(`/api/characters/${character.id}`, { method: "DELETE" });
          if (state.episode) {
            state.episode = await api(`/api/episodes/${state.episode.id}`);
            renderEpisode();
          } else {
            state.novel = await api(`/api/novels/${state.novel.id}`);
            renderNovel();
          }
        });
      };
    }
  });
}

function markVoice(root, voiceId) {
  const voice = voiceById(voiceId);
  const label = root.querySelector("[data-voice-label]");
  if (label) label.textContent = voice ? voice.name : (voiceId || "未指定音色");
  root.querySelectorAll(".voice-row").forEach((row) => {
    const pick = row.querySelector("[data-pick-voice]");
    row.classList.toggle("on", pick && pick.dataset.pickVoice === voiceId);
  });
}

async function previewVoice(root, voiceId) {
  const id = (voiceId || root.querySelector('[data-field="voice-custom"]').value || root.querySelector('[data-field="voice"]').value).trim();
  const note = root.querySelector("[data-preview-note]");
  if (!id) {
    if (note) note.textContent = "请先选择音色";
    return;
  }
  if (note) note.textContent = "正在生成试听…";
  try {
    const response = await fetch("/api/voices/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_id: id }),
    });
    if (!response.ok) {
      let detail = "试听失败";
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch (_error) {
        /* 保留默认文案 */
      }
      if (note) note.textContent = typeof detail === "string" ? detail : "试听失败";
      return;
    }
    const blob = await response.blob();
    const audio = root.querySelector("[data-preview-audio]");
    if (audio._url) URL.revokeObjectURL(audio._url);
    audio._url = URL.createObjectURL(blob);
    audio.src = audio._url;
    audio.hidden = false;
    await audio.play();
    if (note) note.textContent = "";
  } catch (error) {
    if (note) note.textContent = error.message || "试听失败";
  }
}

function renderEpisode() {
  const episode = state.episode;
  const stamp = encodeURIComponent(episode.updated_at || "");
  if (state.inspectorEpisode !== episode.id) {
    state.inspector = "voice";
    state.inspectorEpisode = episode.id;
  }
  const tab = state.inspector;
  shell(`
    <header class="studio-head">
      <div class="studio-title">
        <button class="text-back" id="back" type="button">${esc(episode.novel_title)}</button>
        <h1>${esc(episode.title)}</h1>
        <div class="chips">
          <span class="chip ${episode.line_count ? "on" : ""}">台词 ${episode.line_count}</span>
          <span class="chip ${episode.voiced_count === episode.line_count && episode.line_count ? "on" : ""}">配音 ${episode.voiced_count}/${episode.line_count}</span>
          <span class="chip ${episode.illustrated_count ? "on" : ""}">画面 ${episode.illustrated_count}</span>
          <span class="chip ${episode.has_video ? "on" : ""}">${episode.has_video ? "已成片" : "未成片"}</span>
        </div>
      </div>
      <div class="pipeline">
        <button class="btn" id="act-breakdown" type="button" ${state.busy ? "disabled" : ""}>拆解剧本</button>
        <button class="btn" id="act-tts" type="button" ${state.busy ? "disabled" : ""}>生成配音</button>
        <button class="btn" id="act-image" type="button" ${state.busy ? "disabled" : ""}>生成画面</button>
        <button class="btn" id="act-video" type="button" ${state.busy ? "disabled" : ""}>合成视频</button>
        <button class="btn primary" id="act-pipeline" type="button" ${state.busy ? "disabled" : ""}>一键成片</button>
      </div>
    </header>
    <div class="studio">
      <section class="editor">
        <div class="editor-top">
          <strong>语音合成</strong>
          <span class="count">${episode.line_count} 句 · 重新拆解会覆盖现有台词</span>
        </div>
        <details class="source-fold"><summary>查看原文</summary><div class="source">${esc(episode.source_text)}</div></details>
        <div class="lines" id="lines">
          ${episode.lines.map((line, index) => lineCard(line, index, stamp)).join("") || `<div class="empty">这一集还没有台词。先拆解剧本，或在下面补一句。</div>`}
        </div>
        <form id="add-line" class="composer">
          <input name="speaker" type="text" list="speaker-list" value="旁白" aria-label="说话人">
          <textarea name="text" placeholder="补一句旁白或对白" required></textarea>
          <button class="btn primary" type="submit">添加</button>
        </form>
        <datalist id="speaker-list">
          ${episode.characters.map((character) => `<option value="${esc(character.name)}"></option>`).join("")}
        </datalist>
      </section>
      <aside class="inspector">
        <div class="tabs">
          <button type="button" data-tab="voice" class="${tab === "voice" ? "on" : ""}">音色设置</button>
          <button type="button" data-tab="cut" class="${tab === "cut" ? "on" : ""}">成片</button>
          <button type="button" data-tab="scenes" class="${tab === "scenes" ? "on" : ""}">场景</button>
        </div>
        <div class="tab-panel" data-panel="voice" ${tab === "voice" ? "" : "hidden"}>
          <p class="model-line">${state.config && state.config.tts_provider === "mosi" ? `模型 ${esc(state.config.moss_model || "moss-ttsd-1.0")}` : "离线试听。配置 MOSI_API_KEY 后改为真实音色。"}</p>
          <div class="roster">${episode.characters.map((character) => characterCard(character)).join("") || `<div class="empty">拆解后会列出说话人。</div>`}</div>
        </div>
        <div class="tab-panel" data-panel="cut" ${tab === "cut" ? "" : "hidden"}>
          <div id="cut" class="cut">
            ${episode.video_url ? `<video controls src="${esc(episode.video_url)}?t=${stamp}"></video>` : `<div class="empty">合成之后可以在这里播放。</div>`}
            ${episode.audio_url ? `<audio controls src="${esc(episode.audio_url)}?t=${stamp}"></audio>` : ""}
          </div>
        </div>
        <div class="tab-panel" data-panel="scenes" ${tab === "scenes" ? "" : "hidden"}>
          ${episode.scenes.map((scene) => `
            <div class="scene">
              ${scene.image_url ? `<img src="${esc(scene.image_url)}?t=${stamp}" alt="">` : `<div class="empty">无图</div>`}
              <div>
                <p>${esc(scene.summary)}</p>
                <p class="prompt">台词 ${scene.start_line + 1}–${scene.end_line + 1}</p>
              </div>
            </div>`).join("") || `<div class="empty">生成画面后，场景会列在这里。</div>`}
        </div>
      </aside>
    </div>`, "episode");
  document.querySelector("#back").onclick = () => go(`#/novels/${episode.novel_id}`);
  document.querySelector("#act-breakdown").onclick = () => startJob("breakdown", episode.line_count > 0);
  document.querySelector("#act-tts").onclick = () => startJob("tts", false);
  document.querySelector("#act-image").onclick = () => startJob("illustrate", false);
  document.querySelector("#act-video").onclick = () => startJob("video", false);
  document.querySelector("#act-pipeline").onclick = () => startJob("pipeline", false);
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.onclick = () => {
      state.inspector = button.dataset.tab;
      document.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("on", item.dataset.tab === state.inspector));
      document.querySelectorAll("[data-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.panel !== state.inspector;
      });
    };
  });
  bindLines(episode);
  bindCharacters(episode.characters);
  document.querySelector("#add-line").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    await flushPending();
    await run(async () => {
      state.episode = await api(`/api/episodes/${episode.id}/lines`, {
        method: "POST",
        body: { speaker: form.get("speaker"), text: form.get("text") },
      });
      renderEpisode();
    });
  };
}

function lineCard(line, index, stamp) {
  return `<article class="line" data-line="${esc(line.id)}">
    <div class="line-head">
      <span class="badge">${String(index + 1).padStart(2, "0")}</span>
      <input data-field="speaker" type="text" list="speaker-list" value="${esc(line.speaker)}" aria-label="说话人">
      <span class="kind">${line.kind === "narrator" ? "旁白" : "对白"}</span>
      <div class="line-tools">
        <button class="btn small" type="button" data-move="up">上移</button>
        <button class="btn small" type="button" data-move="down">下移</button>
        <button class="btn small" type="button" data-resynth="1">重配</button>
        <button class="btn small danger" type="button" data-remove="1">删除</button>
      </div>
    </div>
    <textarea data-field="text">${esc(line.text)}</textarea>
    ${line.audio_url ? `<audio controls src="${esc(line.audio_url)}?t=${stamp}"></audio>` : ""}
  </article>`;
}

function bindLines(episode) {
  episode.lines.forEach((line) => {
    const root = document.querySelector(`[data-line="${line.id}"]`);
    if (!root) return;
    const queue = () => {
      clearTimeout(pending.get(line.id));
      pending.set(line.id, setTimeout(() => saveLine(line.id, root), 350));
    };
    root.querySelector('[data-field="text"]').oninput = queue;
    root.querySelector('[data-field="speaker"]').onchange = queue;
    root.querySelector('[data-move="up"]').onclick = () => moveLine(line.id, "up");
    root.querySelector('[data-move="down"]').onclick = () => moveLine(line.id, "down");
    root.querySelector("[data-resynth]").onclick = () => resynthLine(line.id);
    root.querySelector("[data-remove]").onclick = async () => {
      await flushPending();
      await run(async () => {
        state.episode = await api(`/api/lines/${line.id}`, { method: "DELETE" });
        renderEpisode();
      });
    };
  });
}

async function saveLine(id, root) {
  pending.delete(id);
  const speaker = root.querySelector('[data-field="speaker"]').value.trim();
  const text = root.querySelector('[data-field="text"]').value.trim();
  if (!text) return;
  try {
    const episode = await api(`/api/lines/${id}`, { method: "PATCH", body: { speaker, text } });
    state.episode = episode;
    markCutStale(episode, id);
  } catch (error) {
    state.error = error.message;
    renderEpisode();
  }
}

function markCutStale(episode, lineId) {
  const cut = document.querySelector("#cut");
  if (cut && !episode.video_url) {
    cut.innerHTML = `<div class="empty">台词已改，原来的成片作废了。重新生成配音后再合成视频。</div>`;
  }
  const line = (episode.lines || []).find((item) => item.id === lineId);
  const card = document.querySelector(`[data-line="${lineId}"] audio`);
  if (card && (!line || !line.audio_url)) card.remove();
  const chips = document.querySelectorAll(".chips .chip");
  if (chips.length >= 4) {
    chips[1].textContent = `配音 ${episode.voiced_count}/${episode.line_count}`;
    chips[1].classList.toggle("on", episode.voiced_count === episode.line_count && episode.line_count > 0);
    chips[3].textContent = episode.has_video ? "已成片" : "未成片";
    chips[3].classList.toggle("on", episode.has_video);
  }
}

async function moveLine(id, direction) {
  await flushPending();
  await run(async () => {
    state.episode = await api(`/api/lines/${id}/move`, { method: "POST", body: { direction } });
    renderEpisode();
  });
}

async function flushPending() {
  const jobs = [];
  pending.forEach((timer, id) => {
    clearTimeout(timer);
    const root = document.querySelector(`[data-line="${id}"]`);
    if (root) jobs.push(saveLine(id, root));
  });
  pending.clear();
  await Promise.all(jobs);
}

async function resynthLine(id) {
  await run(async () => {
    await flushPending();
    const job = await api(`/api/lines/${id}/tts`, { method: "POST" });
    state.job = job;
    renderEpisode();
    await watchJob(job);
  });
}

async function startJob(kind, confirmOverwrite) {
  if (confirmOverwrite && !confirm("重新拆解会覆盖当前台词，并清掉这一集已生成的配音和画面。继续吗？")) return;
  await run(async () => {
    await flushPending();
    const job = await api(`/api/episodes/${state.episode.id}/${kind}`, { method: "POST" });
    state.job = job;
    renderEpisode();
    await watchJob(job);
  });
}

async function watchJob(job) {
  const token = ++pollToken;
  let current = job;
  state.busy = current.status === "queued" || current.status === "running";
  while (current && (current.status === "queued" || current.status === "running")) {
    state.job = current;
    const bar = document.querySelector(".jobbar");
    if (!bar) renderEpisode();
    else updateJobBar();
    await sleep(700);
    if (token !== pollToken) return;
    current = await api(`/api/jobs/${job.id}`);
  }
  if (token !== pollToken) return;
  state.job = current;
  state.busy = false;
  if (state.episode) {
    state.episode = await api(`/api/episodes/${state.episode.id}`);
    renderEpisode();
  }
}

function updateJobBar() {
  const existing = document.querySelector(".jobbar");
  if (!existing || !state.job) return;
  const holder = document.createElement("div");
  holder.innerHTML = jobbar();
  existing.replaceWith(holder.firstElementChild);
}

async function importSample() {
  await run(async () => {
    const sample = await api("/api/sample");
    await importText(sample.title, sample.text);
  });
}

async function importText(title, text) {
  await run(async () => {
    const novel = await api("/api/novels", { method: "POST", body: { title: title || null, text } });
    go(`#/novels/${novel.id}`);
  });
}

async function run(work) {
  state.error = "";
  try {
    await work();
  } catch (error) {
    state.error = error.message;
    state.busy = false;
    if (state.episode) renderEpisode();
    else if (state.novel) renderNovel();
    else renderLibrary();
  }
}

async function boot() {
  try {
    state.config = await api("/api/config");
    const voicebook = await api("/api/voices");
    state.voices = voicebook.voices || [];
    state.examples = voicebook.examples || [];
  } catch (error) {
    state.error = error.message;
  }
  window.addEventListener("hashchange", route);
  if (!location.hash) location.hash = "#/";
  else route();
}

boot();
