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
      state.novel = null;
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

function banner() {
  if (!state.config) return "";
  if (state.config.app_mode === "mock" && state.config.tts_provider === "mock") {
    return `<div class="banner">当前是离线演示。剧本、配音和画面都在本机生成，不需要 API 密钥。</div>`;
  }
  const bits = [
    `文本 ${esc(state.config.llm_provider)}`,
    `语音 ${esc(state.config.tts_provider)}`,
    `图像 ${esc(state.config.image_provider)}`,
  ];
  const missing = [];
  if (state.config.llm_provider === "openai" && !state.config.llm_configured) missing.push("OPENAI_API_KEY");
  if (state.config.tts_provider === "mosi" && !state.config.moss_configured) missing.push("MOSS_API_KEY");
  if (state.config.image_provider === "openai" && !state.config.image_configured) missing.push("图像密钥");
  const warn = missing.length ? `还缺：${missing.join("、")}` : "密钥已配置";
  return `<div class="banner">真实接口模式。${bits.join(" · ")}。${esc(warn)}</div>`;
}

function jobbar() {
  const job = state.job;
  if (!job) return "";
  const label = JOBS[job.kind] || job.kind;
  return `<div class="jobbar ${esc(job.status)}">
    <strong>${esc(label)}</strong> · ${esc(job.message || job.status)}
    ${job.error ? `<div>${esc(job.error)}</div>` : ""}
    <div class="progress"><span style="width:${job.progress || 0}%"></span></div>
  </div>`;
}

function shell(inner) {
  app.innerHTML = `
    <div class="shell">
      <header class="top">
        <button class="brand" id="home">
          <span class="seal">章</span>
          <span><h1>章映</h1><p>小说影像工作室</p></span>
        </button>
        <div class="top-actions">
          <button class="btn" id="load-sample">载入示例</button>
          <button class="btn primary" id="jump-import">导入作品</button>
        </div>
      </header>
      ${banner()}
      ${state.error ? `<div class="toast">${esc(state.error)}</div>` : ""}
      ${jobbar()}
      ${inner}
    </div>`;
  document.querySelector("#home").onclick = () => go("#/");
  document.querySelector("#load-sample").onclick = () => importSample();
  document.querySelector("#jump-import").onclick = () => {
    go("#/");
    setTimeout(() => document.querySelector("#import-text")?.focus(), 0);
  };
}

function renderLibrary() {
  const cards = state.novels.length
    ? `<div class="grid">${state.novels.map((novel) => `
        <button class="card" data-novel="${esc(novel.id)}">
          <strong>${esc(novel.title)}</strong>
          <span>${novel.episode_count} 集 · ${novel.character_count} 个角色</span>
        </button>`).join("")}</div>`
    : `<div class="empty">还没有作品。贴一段小说，或先载入《雨夜来信》。</div>`;
  shell(`
    <section class="panel" id="import">
      <p class="kicker">导入</p>
      <h2>把一章书变成一集影像</h2>
      <p class="lede">粘贴正文，或上传 .txt / .md。单独成行的「第X章」「第X集」会自动分成一集。</p>
      <form id="import-form">
        <div class="row">
          <div class="grow"><label for="import-title">作品标题（可空）</label><input id="import-title" name="title" type="text" maxlength="80"></div>
        </div>
        <label for="import-text">正文</label>
        <textarea id="import-text" name="text" placeholder="第1集 雨夜&#10;&#10;雨下了整整一夜。"></textarea>
        <div class="actions">
          <button class="btn primary" type="submit">导入正文</button>
          <label class="btn">上传文件<input id="import-file" type="file" accept=".txt,.md,text/plain,text/markdown" hidden></label>
        </div>
      </form>
    </section>
    <section>
      <h3 style="margin:18px 0 10px">作品库</h3>
      ${cards}
    </section>`);
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
    <button class="back" id="back">← 作品库</button>
    <section class="panel">
      <p class="kicker">作品</p>
      <h2>${esc(novel.title)}</h2>
      <p class="meta">${novel.episode_count} 集 · ${novel.character_count} 个角色</p>
      <div class="actions"><button class="btn danger" id="delete-novel">删除作品</button></div>
    </section>
    <section class="panel">
      <h3>角色名册</h3>
      <p class="lede">每个角色可以改外观描述，并指定一把音色。旁白也会占一个音色。</p>
      <div class="roster" id="roster">
        ${novel.characters.map((character) => characterCard(character)).join("") || `<div class="empty">拆解剧本后会在这里出现角色。</div>`}
      </div>
      <form id="add-character" class="row" style="margin-top:14px">
        <div class="grow"><input name="name" type="text" placeholder="新角色名字" required maxlength="80"></div>
        <button class="btn" type="submit">添加角色</button>
      </form>
    </section>
    <section class="panel">
      <h3>分集</h3>
      ${(novel.episodes || []).map((episode) => `
        <button class="card" style="width:100%; margin-top:10px" data-episode="${esc(episode.id)}">
          <strong>${esc(episode.title)}</strong>
          <span>${esc(STATUS[episode.status] || episode.status)} · 台词 ${episode.line_count} · ${episode.has_video ? "已有成片" : "尚未成片"}</span>
        </button>`).join("")}
    </section>`);
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

function characterCard(character) {
  return `<div class="character" data-character="${esc(character.id)}">
    <div class="speaker-row">
      <strong>${esc(character.name)}</strong>
      ${character.is_narrator ? `<span class="chip on">旁白</span>` : ""}
    </div>
    <label>音色</label>
    ${voiceSelect(character)}
    <label style="margin-top:8px">外观描述</label>
    <textarea data-field="description" rows="2">${esc(character.description || "")}</textarea>
    ${character.is_narrator ? "" : `<div class="actions"><button class="btn small danger" data-delete-character="${esc(character.id)}" type="button">删除角色</button></div>`}
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

function voiceSelect(character) {
  return `<select data-field="voice">${voiceOptions(character.voice_id)}</select>
    <input data-field="voice-custom" type="text" placeholder="或粘贴 voice_id" value="${esc(character.voice_id || "")}" style="margin-top:6px">`;
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
      run(save);
    };
    root.querySelector('[data-field="voice-custom"]').onchange = save;
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

function renderEpisode() {
  const episode = state.episode;
  const stamp = encodeURIComponent(episode.updated_at || "");
  shell(`
    <button class="back" id="back">← ${esc(episode.novel_title)}</button>
    <section class="panel">
      <p class="kicker">第 ${episode.position + 1} 集</p>
      <h2>${esc(episode.title)}</h2>
      <div class="chips">
        <span class="chip ${episode.line_count ? "on" : ""}">台词 ${episode.line_count}</span>
        <span class="chip ${episode.voiced_count === episode.line_count && episode.line_count ? "on" : ""}">配音 ${episode.voiced_count}/${episode.line_count}</span>
        <span class="chip ${episode.illustrated_count ? "on" : ""}">画面 ${episode.illustrated_count}</span>
        <span class="chip ${episode.has_video ? "on" : ""}">${episode.has_video ? "已成片" : "未成片"}</span>
      </div>
      <div class="actions">
        <button class="btn" id="act-breakdown" ${state.busy ? "disabled" : ""}>拆解剧本</button>
        <button class="btn" id="act-tts" ${state.busy ? "disabled" : ""}>生成配音</button>
        <button class="btn" id="act-image" ${state.busy ? "disabled" : ""}>生成画面</button>
        <button class="btn" id="act-video" ${state.busy ? "disabled" : ""}>合成视频</button>
        <button class="btn primary" id="act-pipeline" ${state.busy ? "disabled" : ""}>一键成片</button>
      </div>
      <p class="meta">一键成片会保留现有台词；如果还没有台词，会先自动拆解。重新拆解会覆盖台词。</p>
      <details><summary>查看原文</summary><div class="source">${esc(episode.source_text)}</div></details>
    </section>
    <div class="layout">
      <section class="panel">
        <h3>台词</h3>
        <div id="lines">
          ${episode.lines.map((line, index) => lineCard(line, index, stamp)).join("") || `<div class="empty">这一集还没有台词。</div>`}
        </div>
        <form id="add-line" class="stack" style="margin-top:12px">
          <div class="row">
            <input name="speaker" type="text" list="speaker-list" value="旁白" style="max-width:160px">
            <button class="btn" type="submit">加一句</button>
          </div>
          <textarea name="text" placeholder="补一句旁白或对白" required></textarea>
        </form>
        <datalist id="speaker-list">
          ${episode.characters.map((character) => `<option value="${esc(character.name)}"></option>`).join("")}
        </datalist>
      </section>
      <aside class="stack">
        <section class="panel">
          <h3>成片</h3>
          <div id="cut">
            ${episode.video_url ? `<video controls src="${esc(episode.video_url)}?t=${stamp}"></video>` : `<div class="empty">合成之后可以在这里播放。</div>`}
            ${episode.audio_url ? `<audio controls src="${esc(episode.audio_url)}?t=${stamp}" style="margin-top:8px"></audio>` : ""}
          </div>
        </section>
        <section class="panel">
          <h3>场景</h3>
          ${episode.scenes.map((scene) => `
            <div class="scene">
              ${scene.image_url ? `<img src="${esc(scene.image_url)}?t=${stamp}" alt="">` : `<div class="empty">无图</div>`}
              <div>
                <p>${esc(scene.summary)}</p>
                <p class="prompt">台词 ${scene.start_line + 1}–${scene.end_line + 1}</p>
              </div>
            </div>`).join("") || `<div class="empty">生成画面后，场景会列在这里。</div>`}
        </section>
        <section class="panel">
          <h3>这一集的音色</h3>
          <div class="roster">${episode.characters.map((character) => characterCard(character)).join("")}</div>
        </section>
      </aside>
    </div>`);
  document.querySelector("#back").onclick = () => go(`#/novels/${episode.novel_id}`);
  document.querySelector("#act-breakdown").onclick = () => startJob("breakdown", episode.line_count > 0);
  document.querySelector("#act-tts").onclick = () => startJob("tts", false);
  document.querySelector("#act-image").onclick = () => startJob("illustrate", false);
  document.querySelector("#act-video").onclick = () => startJob("video", false);
  document.querySelector("#act-pipeline").onclick = () => startJob("pipeline", false);
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
  return `<article class="script-line" data-line="${esc(line.id)}">
    <div class="index">${String(index + 1).padStart(2, "0")}</div>
    <div>
      <div class="speaker-row">
        <input data-field="speaker" type="text" list="speaker-list" value="${esc(line.speaker)}">
        <span class="chip">${line.kind === "narrator" ? "旁白" : "对白"}</span>
      </div>
      <textarea class="line-text" data-field="text">${esc(line.text)}</textarea>
      <div class="line-actions">
        <button class="btn small" type="button" data-move="up">上移</button>
        <button class="btn small" type="button" data-move="down">下移</button>
        <button class="btn small danger" type="button" data-remove="1">删除</button>
        ${line.audio_url ? `<audio controls src="${esc(line.audio_url)}?t=${stamp}"></audio>` : ""}
      </div>
    </div>
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
