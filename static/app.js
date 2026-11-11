const state = {
  sessions: [],
  activeId: null,
  path: "",
  parent: "/",
  entries: [],
  selected: new Set(),
  anchor: "",
  sort: { key: "name", dir: 1 },
  filter: "",
  busy: false,
  busyMessage: "",
  dropping: false,
  dialogCancel: null,
  uploading: false,
  downloading: false,
  uploadMessage: "",
  uploadPercent: 0,
};

const $ = (id) => document.getElementById(id);

function jsonHeaders() {
  return { "Content-Type": "application/json", "X-RF-Request": "1" };
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "X-RF-Request": "1", ...(options.headers || {}) },
  });
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error((data && data.error) || "请求失败");
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

function toast(message) {
  const item = document.createElement("div");
  item.className = "toast";
  item.textContent = message;
  $("toasts").append(item);
  setTimeout(() => item.remove(), 4600);
}

function showError(error) {
  toast(error && error.message ? error.message : String(error));
}

function currentSession() {
  return state.sessions.find((session) => session.id === state.activeId) || null;
}

const views = new Map();

function ensureView(id) {
  let saved = views.get(id);
  if (!saved) {
    saved = {
      path: "",
      parent: "/",
      entries: [],
      selected: new Set(),
      anchor: "",
      sort: { key: "name", dir: 1 },
      filter: "",
      uploading: false,
      downloading: false,
      uploadMessage: "",
      uploadPercent: 0,
      dirty: false,
    };
    views.set(id, saved);
  }
  return saved;
}

function rememberView() {
  if (!state.activeId) return;
  const saved = ensureView(state.activeId);
  saved.path = state.path;
  saved.parent = state.parent;
  saved.entries = state.entries;
  saved.selected = state.selected;
  saved.anchor = state.anchor;
  saved.sort = state.sort;
  saved.filter = state.filter;
  saved.uploading = state.uploading;
  saved.downloading = state.downloading;
  saved.uploadMessage = state.uploadMessage;
  saved.uploadPercent = state.uploadPercent;
}

function showView(id) {
  const saved = ensureView(id);
  state.path = saved.path;
  state.parent = saved.parent;
  state.entries = saved.entries;
  state.selected = saved.selected;
  state.anchor = saved.anchor;
  state.sort = saved.sort;
  state.filter = saved.filter;
  state.uploading = saved.uploading;
  state.downloading = saved.downloading;
  state.uploadMessage = saved.uploadMessage;
  state.uploadPercent = saved.uploadPercent;
  const filter = $("filter");
  if (filter && filter.value !== saved.filter) filter.value = saved.filter;
}

function transferring(id) {
  const saved = views.get(id);
  return Boolean(saved && (saved.uploading || saved.downloading));
}

const LARGE_OPEN_BYTES = 32 * 1024 * 1024;

function isLargeFile(entry) {
  return Boolean(entry) && !entry.isDir && Number(entry.size) >= LARGE_OPEN_BYTES;
}

function formatSize(size, isDir, partial) {
  if (isDir && (size === null || size === undefined)) return "…";
  if (size === null || size === undefined) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = size;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = index === 0 || value >= 10 ? 0 : 1;
  const text = `${value.toFixed(digits)} ${units[index]}`;
  return partial ? `≥ ${text}` : text;
}

function formatTime(epoch) {
  if (!epoch) return "";
  const date = new Date(epoch * 1000);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function visibleEntries() {
  const query = state.filter.trim().toLowerCase();
  const rows = state.entries.filter((entry) => !query || entry.name.toLowerCase().includes(query));
  const direction = state.sort.dir;
  const key = state.sort.key;
  rows.sort((left, right) => {
    if (left.isDir !== right.isDir) return left.isDir ? -1 : 1;
    const a = key === "name" ? left.name.toLowerCase() : left[key];
    const b = key === "name" ? right.name.toLowerCase() : right[key];
    if (a < b) return -direction;
    if (a > b) return direction;
    return 0;
  });
  return rows;
}

function selectedEntries() {
  return state.entries.filter((entry) => state.selected.has(entry.path));
}

async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderSessions();
}

function renderSessions() {
  const list = $("session-list");
  list.replaceChildren();
  if (!state.sessions.length) {
    const empty = document.createElement("p");
    empty.className = "badge";
    empty.textContent = "还没有保存的连接";
    list.append(empty);
    return;
  }
  for (const session of state.sessions) {
    const row = document.createElement("div");
    row.className = "session" + (session.id === state.activeId ? " active" : "");
    row.dataset.session = session.id;
    const dot = document.createElement("i");
    dot.className = "dot" + (session.connected ? " on" : "");
    const meta = document.createElement("div");
    meta.className = "meta";
    const title = document.createElement("strong");
    title.textContent = session.name;
    const sub = document.createElement("span");
    const where = `${session.username}@${session.host}:${session.port}`;
    sub.textContent = transferring(session.id) ? `${where} · 传输中` : where;
    meta.append(title, sub);
    const more = document.createElement("button");
    more.type = "button";
    more.className = "more";
    more.textContent = "···";
    more.title = "更多";
    more.addEventListener("click", (event) => {
      event.stopPropagation();
      showMenu(event.clientX, event.clientY, sessionMenu(session));
    });
    row.append(dot, meta, more);
    row.addEventListener("click", () => {
      openSession(session.id).catch(showError);
    });
    list.append(row);
  }
}

function renderRows() {
  const connected = Boolean(currentSession()?.connected);
  $("welcome").hidden = connected;
  $("files").hidden = !connected;
  const body = $("rows");
  const wrap = $("list-wrap");
  const scroll = wrap.scrollTop;
  body.replaceChildren();
  if (!connected) {
    wrap.scrollTop = scroll;
    return;
  }
  const rows = visibleEntries();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "empty";
    td.textContent = state.entries.length ? "没有匹配的项目" : "这个文件夹是空的";
    tr.append(td);
    body.append(tr);
  }
  for (const entry of rows) {
    const index = state.entries.indexOf(entry);
    const tr = document.createElement("tr");
    tr.dataset.index = String(index);
    if (state.selected.has(entry.path)) tr.classList.add("selected");
    const name = document.createElement("td");
    const label = document.createElement("div");
    label.className = "name";
    const icon = FileIcons.create(entry);
    const text = document.createElement("span");
    text.textContent = entry.name;
    label.append(icon, text);
    if (entry.isLink) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "链接";
      label.append(badge);
    }
    name.append(label);
    const size = document.createElement("td");
    size.textContent = formatSize(entry.size, entry.isDir, entry.partial);
    const time = document.createElement("td");
    time.textContent = formatTime(entry.mtime);
    const owner = document.createElement("td");
    owner.textContent = entry.owner || "";
    const group = document.createElement("td");
    group.textContent = entry.group || "";
    const mode = document.createElement("td");
    mode.textContent = entry.mode || "";
    tr.append(name, size, time, owner, group, mode);
    body.append(tr);
  }
  wrap.scrollTop = scroll;
}

function renderCrumbs() {
  const box = $("crumbs");
  box.replaceChildren();
  const path = state.path || "";
  if (!path) return;
  const parts = path.split("/").filter(Boolean);
  const session = currentSession();
  const items = [{ name: session && session.host ? session.host : "/", path: "/" }];
  let current = "";
  for (const part of parts) {
    current += "/" + part;
    items.push({ name: part, path: current });
  }
  items.forEach((item, index) => {
    if (index) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "›";
      box.append(sep);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.name;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      browse(item.path).catch(showError);
    });
    box.append(button);
  });
}

function syncAddress() {
  const input = $("address");
  if (document.activeElement !== input) input.value = state.path || "";
}

function beginAddressEdit() {
  if (!currentSession()?.connected || state.busy) return;
  const input = $("address");
  input.value = state.path || "/";
  input.hidden = false;
  $("address-bar").classList.add("editing");
  input.focus();
  setTimeout(() => input.select(), 0);
}

function endAddressEdit(commit) {
  const bar = $("address-bar");
  if (!bar.classList.contains("editing")) return;
  const input = $("address");
  const next = input.value.trim();
  bar.classList.remove("editing");
  input.hidden = true;
  input.value = state.path || "";
  if (commit && next && next !== (state.path || "")) browse(next).catch(showError);
}

function renderChrome() {
  const connected = Boolean(currentSession()?.connected);
  const selected = state.selected.size;
  $("refresh").disabled = !connected || state.busy;
  $("upload").disabled = !connected || state.busy || state.uploading;
  $("upload-dir").disabled = !connected || state.busy || state.uploading;
  $("download").disabled = !connected || !selected || state.busy || state.downloading;
  $("sudo-btn").disabled = !connected || state.busy;
  $("sudo-btn").classList.toggle("on", Boolean(currentSession()?.sudo));
  $("sudo-btn").textContent = currentSession()?.sudo ? "关闭 sudo" : "sudo 查看";
  $("open-bash").disabled = !connected || state.busy || !state.path;
  $("up").disabled = !connected || !state.path || state.path === "/" || state.busy;
  $("overlay").hidden = !state.busy;
  $("overlay").textContent = state.busyMessage || "请稍候…";
  $("upload-bar").hidden = !state.uploading && !state.downloading;
  $("upload-bar").classList.toggle("waiting", (state.uploading || state.downloading) && state.uploadPercent <= 0);
  $("upload-text").textContent = state.uploadMessage;
  $("upload-text").title = state.uploadMessage;
  const percent = Math.max(0, Math.min(100, state.uploadPercent));
  $("upload-fill").style.width = percent > 0 ? `${percent}%` : "";
  $("list-wrap").classList.toggle("dropping", state.dropping);
  const status = $("status");
  if (!connected) status.textContent = "选择左侧连接，或新建一台主机。";
  else if (state.busy) status.textContent = state.busyMessage || "请稍候…";
  else {
    const prefix = currentSession()?.sudo ? "sudo 查看 · " : "";
    status.textContent = prefix + (selected ? `${visibleEntries().length} 项，已选 ${selected} 项` : `${visibleEntries().length} 项`);
  }
  document.querySelectorAll("#files th").forEach((th) => {
    const mark = th.dataset.sort === state.sort.key ? (state.sort.dir > 0 ? " ↑" : " ↓") : "";
    const base = th.dataset.label || th.textContent.replace(/ [↑↓]$/, "");
    th.dataset.label = base;
    th.textContent = base + mark;
  });
}

function render() {
  renderSessions();
  renderRows();
  renderCrumbs();
  syncAddress();
  renderChrome();
}

async function busy(message, action) {
  state.busy = true;
  state.busyMessage = message;
  renderChrome();
  try {
    return await action();
  } finally {
    state.busy = false;
    state.busyMessage = "";
    render();
  }
}

let sizeToken = 0;
let sizeAbort = null;

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      if (signal) signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    };
    if (signal) {
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
  });
}

async function loadDirectory(path) {
  const sessionId = state.activeId;
  if (!sessionId) return;
  const token = ++sizeToken;
  if (sizeAbort) sizeAbort.abort();
  const data = await api(`/api/sessions/${sessionId}/list?path=${encodeURIComponent(path || "")}`);
  if (token !== sizeToken) return;
  const saved = ensureView(sessionId);
  saved.path = data.path;
  saved.parent = data.parent;
  saved.entries = data.entries;
  saved.selected = new Set();
  saved.dirty = false;
  const session = state.sessions.find((item) => item.id === sessionId);
  if (session) session.lastPath = data.path;
  if (sessionId !== state.activeId) return;
  showView(sessionId);
  renderRows();
  renderCrumbs();
  syncAddress();
  renderChrome();
  const controller = new AbortController();
  sizeAbort = controller;
  measureFolders(token, sessionId, controller.signal);
}

async function measureFolders(token, sessionId, signal) {
  try {
    await sleep(450, signal);
  } catch {
    return;
  }
  if (token !== sizeToken || sessionId !== state.activeId || signal.aborted) return;
  const folders = state.entries.filter((entry) => entry.isDir);
  for (const entry of folders) {
    let resume = true;
    while (resume) {
      if (token !== sizeToken || sessionId !== state.activeId || signal.aborted) return;
      try {
        const data = await api(`/api/sessions/${sessionId}/size?path=${encodeURIComponent(entry.path)}`, { signal });
        if (token !== sizeToken || sessionId !== state.activeId || data.stale) return;
        if (data.counted) {
          entry.size = data.size;
          entry.partial = Boolean(data.partial);
          entry.sizeFailed = false;
          if (state.sort.key === "size") renderRows();
          else paintFolderSize(entry);
        }
        resume = Boolean(data.partial) && !data.limited && !data.stale;
        if (resume) await sleep(data.counted ? 40 : 180, signal);
      } catch (error) {
        if (error && error.name === "AbortError") return;
        if (token !== sizeToken || sessionId !== state.activeId) return;
        entry.sizeFailed = true;
        entry.partial = false;
        paintFolderSize(entry);
        resume = false;
      }
    }
  }
}

function paintFolderSize(entry) {
  const index = state.entries.indexOf(entry);
  const row = [...$("rows").children].find((tr) => Number(tr.dataset.index) === index);
  if (!row) return;
  row.children[1].textContent = entry.sizeFailed ? "—" : formatSize(entry.size, true, entry.partial);
}

async function browse(path) {
  try {
    await loadDirectory(path);
  } catch (error) {
    if (!(error.payload && error.payload.permissionDenied)) throw error;
    const elevated = await offerSudo(true);
    if (!elevated) throw error;
    await loadDirectory(path);
  }
}

async function requestSudo(password, remember) {
  const data = await api(`/api/sessions/${state.activeId}/sudo`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ password: password || "", remember: Boolean(remember), enable: true }),
  });
  const session = currentSession();
  if (session) session.sudo = true;
  renderChrome();
  toast(data.message || "已使用 sudo 查看");
}

async function offerSudo(becauseDenied) {
  if (currentSession()?.sudo) return true;
  try {
    await requestSudo("", false);
    return true;
  } catch (error) {
    if (!(error.payload && error.payload.sudoPassword)) throw error;
  }
  while (true) {
    const form = await openDialog({
      title: "sudo 密码",
      message: becauseDenied
        ? "当前账号打不开这个目录。输入 sudo 密码后，会用管理员身份查看和操作，直到关闭 sudo 查看。"
        : "输入 sudo 密码后，会用管理员身份查看和操作，直到关闭 sudo 查看。",
      fields: [
        { name: "password", label: "sudo 密码", type: "password", required: true },
        { name: "remember", label: "记住，下次不用再输入", type: "checkbox", checked: true },
      ],
      okText: "sudo 查看",
    });
    if (!form) return false;
    try {
      await requestSudo(form.password, form.remember);
      return true;
    } catch (error) {
      if (!(error.payload && error.payload.sudoPassword)) throw error;
      toast(error.message || "sudo 密码不正确");
    }
  }
}

async function disableSudo() {
  await api(`/api/sessions/${state.activeId}/sudo`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ enable: false }),
  });
  const session = currentSession();
  if (session) session.sudo = false;
  try {
    await loadDirectory(state.path || "");
  } catch (error) {
    if (error.payload && error.payload.permissionDenied) await loadDirectory("");
    else throw error;
  }
  toast("已关闭 sudo 查看");
}

async function connectSession(id, extra = {}) {
  const payload = {
    password: extra.password || undefined,
    passphrase: extra.passphrase || undefined,
    remember: Boolean(extra.remember),
    trustHost: Boolean(extra.trustHost),
  };
  try {
    return await api(`/api/sessions/${id}/connect`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  } catch (error) {
    if (error.payload && error.payload.authRequired && !extra.prompted) {
      const kind = error.payload.secretKind === "passphrase" ? "passphrase" : "password";
      const form = await openDialog({
        title: kind === "passphrase" ? "私钥口令" : "登录密码",
        fields: [
          { name: kind, label: kind === "passphrase" ? "口令" : "密码", type: "password", required: true },
          { name: "remember", label: "记住，下次不用再输入", type: "checkbox", checked: true },
        ],
        okText: "连接",
      });
      if (!form) throw error;
      return connectSession(id, {
        ...extra,
        password: form.password,
        passphrase: form.passphrase,
        remember: form.remember,
        prompted: true,
      });
    }
    if (error.payload && error.payload.hostKeyChanged && !extra.trustHost) {
      const ok = await openDialog({
        title: "主机密钥已变化",
        message: error.message,
        okText: "仍然连接",
        danger: true,
      });
      if (!ok) throw error;
      return connectSession(id, { ...extra, trustHost: true });
    }
    throw error;
  }
}

async function focusSession(id) {
  if (!id || id === state.activeId) return;
  rememberView();
  if (sizeAbort) sizeAbort.abort();
  state.activeId = id;
  showView(id);
  render();
  const session = state.sessions.find((item) => item.id === id);
  const saved = ensureView(id);
  if (session?.connected && (saved.dirty || (!saved.path && !saved.entries.length))) {
    await browse(saved.path || session.lastPath || "");
  }
}

async function openSession(id, options = {}) {
  if (state.busy) return;
  const known = state.sessions.find((item) => item.id === id);
  if (known?.connected && !options.force) {
    await focusSession(id);
    return;
  }
  rememberView();
  const previous = state.activeId;
  state.activeId = id;
  if (id !== previous) showView(id);
  renderSessions();
  await busy("正在连接…", async () => {
    await connectSession(id);
    const session = state.sessions.find((item) => item.id === id);
    if (session) session.connected = true;
    try {
      await browse(session && session.lastPath ? session.lastPath : "");
    } catch (error) {
      if (session && session.lastPath) await browse("");
      else throw error;
    }
  });
}

function splitUploadBatches(items) {
  const batches = [];
  let batch = [];
  let bytes = 0;
  for (const item of items) {
    const size = item.file?.size || 0;
    if (batch.length && (batch.length >= 2000 || bytes + size > 32 * 1024 * 1024)) {
      batches.push(batch);
      batch = [];
      bytes = 0;
    }
    batch.push(item);
    bytes += size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

function uploadPercent(doneBytes, totalBytes, doneCount, totalCount) {
  if (totalBytes > 0) return Math.round((doneBytes / totalBytes) * 100);
  if (totalCount > 0) return Math.round((doneCount / totalCount) * 100);
  return 0;
}

function paintTransfer(message, percent, sessionId = state.activeId) {
  if (!sessionId) return;
  const saved = ensureView(sessionId);
  saved.uploadMessage = message;
  saved.uploadPercent = percent;
  if (sessionId === state.activeId) {
    state.uploadMessage = message;
    state.uploadPercent = percent;
    renderChrome();
  }
  const caption = document.querySelector(`#session-list [data-session="${sessionId}"] span`);
  const session = state.sessions.find((item) => item.id === sessionId);
  if (caption && session) {
    const where = `${session.username}@${session.host}:${session.port}`;
    caption.textContent = transferring(sessionId) ? `${where} · 传输中` : where;
  }
}

function releaseTransfer(sessionId, kind) {
  const saved = ensureView(sessionId);
  if (kind === "upload") saved.uploading = false;
  if (kind === "download") saved.downloading = false;
  if (!saved.uploading && !saved.downloading) {
    saved.uploadMessage = "";
    saved.uploadPercent = 0;
  }
  if (sessionId === state.activeId) {
    state.uploading = saved.uploading;
    state.downloading = saved.downloading;
    state.uploadMessage = saved.uploadMessage;
    state.uploadPercent = saved.uploadPercent;
    render();
  } else {
    renderSessions();
  }
}

function yieldFrame() {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

async function beginUpload(collect) {
  const sessionId = state.activeId;
  if (!sessionId) return;
  if (ensureView(sessionId).uploading) {
    toast("这个连接已有上传在进行，可以切换到其他连接。");
    return;
  }
  const dest = state.path || "/";
  ensureView(sessionId).uploading = true;
  state.uploading = true;
  paintTransfer("正在读取本地文件…", 0, sessionId);
  try {
    const items = await collect(sessionId);
    if (!items.length) {
      toast("没有可上传的文件");
      return;
    }
    await sendUpload(sessionId, dest, items);
    const files = items.filter((item) => item.file).length;
    const dirs = items.length - files;
    toast("已上传 " + [files && `${files} 个文件`, dirs && `${dirs} 个空文件夹`].filter(Boolean).join("、"));
  } catch (error) {
    showError(error);
  } finally {
    releaseTransfer(sessionId, "upload");
  }
}

async function listLocalFiles(files, sessionId = state.activeId) {
  const total = files.length;
  const items = [];
  paintTransfer(`正在读取本地文件列表 0/${total}…`, 0, sessionId);
  await yieldFrame();
  for (let index = 0; index < total; index += 1) {
    const file = files[index];
    items.push({ file, relative: file.webkitRelativePath || file.name });
    if (index === total - 1 || index % 200 === 199) {
      paintTransfer(`正在读取本地文件列表 ${index + 1}/${total}…`, 0, sessionId);
      await yieldFrame();
    }
  }
  return items;
}

async function sendUpload(sessionId, dest, items) {
  const totalBytes = items.reduce((sum, item) => sum + (item.file?.size || 0), 0);
  const batches = splitUploadBatches(items);
  let doneBytes = 0;
  let doneCount = 0;
  paintTransfer(`正在准备上传，共 ${items.length} 项…`, 0, sessionId);
  await yieldFrame();
  try {
    for (const batch of batches) {
      const files = batch.filter((item) => item.file);
      const dirs = batch.filter((item) => !item.file).map((item) => item.relative);
      const batchBytes = files.reduce((sum, item) => sum + item.file.size, 0);
      paintTransfer(`正在提交本地文件 ${doneCount}/${items.length}…`, uploadPercent(doneBytes, totalBytes, doneCount, items.length), sessionId);
      await yieldFrame();
      const form = new FormData();
      form.append("dest", dest);
      form.append("relatives", JSON.stringify(files.map((item) => item.relative)));
      if (dirs.length) form.append("directories", JSON.stringify(dirs));
      for (const item of files) form.append("files", item.file, item.file.name);
      await postForm(`/api/sessions/${sessionId}/upload`, form, (event) => {
        if (!event || event.phase === "read") {
          const local = event && event.total ? Math.round((event.loaded / event.total) * 100) : 0;
          const percent = uploadPercent(doneBytes, totalBytes, doneCount, items.length);
          paintTransfer(`正在提交本地文件 ${doneCount}/${items.length}${local ? ` · 本批 ${local}%` : ""}…`, percent, sessionId);
          return;
        }
        if (event.phase && event.phase !== "write") {
          const saved = ensureView(sessionId);
          paintTransfer(event.message || "正在处理…", saved.uploadPercent, sessionId);
          return;
        }
        const remoteBytes = event.total ? (event.done / event.total) * batchBytes : 0;
        const percent = uploadPercent(doneBytes + remoteBytes, totalBytes, doneCount + (event.index || 0), items.length);
        const shown = event.ok ? Math.min(percent, 100) : Math.min(percent, 99);
        const name = event.message || `正在上传 ${doneCount + (event.index || 0)}/${items.length}`;
        paintTransfer(`${name} · ${shown}%`, shown, sessionId);
      });
      doneBytes += batchBytes;
      doneCount += batch.length;
    }
    if (state.activeId === sessionId && state.path === dest) await browse(dest);
    else ensureView(sessionId).dirty = true;
  } catch (error) {
    ensureView(sessionId).dirty = true;
    if (state.activeId === sessionId && state.path === dest) await browse(dest).catch(() => {});
    if (doneCount) error.message = `已上传 ${doneCount} 项，其余没有传完。${error.message}`;
    throw error;
  }
}

function lastJson(text) {
  const lines = String(text || "").trim().split(/\n+/).filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      return JSON.parse(lines[index]);
    } catch {
      /* 还没收到完整的一行 */
    }
  }
  return null;
}

function postForm(url, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let remoteStarted = false;
    const report = () => {
      const data = lastJson(xhr.responseText);
      if (!data) return;
      remoteStarted = true;
      if (onProgress) onProgress(data);
    };
    xhr.open("POST", url);
    xhr.setRequestHeader("X-RF-Request", "1");
    xhr.upload.onprogress = (event) => {
      if (!remoteStarted && onProgress) onProgress({ phase: "read", loaded: event.loaded, total: event.total });
    };
    xhr.upload.onload = () => {
      if (!remoteStarted && onProgress) onProgress({ phase: "connect", message: "本地文件已提交，正在建立传输连接…" });
    };
    xhr.onprogress = report;
    xhr.onreadystatechange = () => {
      if (xhr.readyState >= 3) report();
    };
    xhr.onload = () => {
      const data = lastJson(xhr.responseText) || {};
      const detail = typeof data.error === "string" ? data.error : (typeof data.detail === "string" ? data.detail : "");
      if (xhr.status >= 200 && xhr.status < 300 && !data.error) resolve(data);
      else {
        const error = new Error(detail || "上传失败");
        error.payload = data;
        reject(error);
      }
    };
    xhr.onerror = () => reject(new Error("上传中断，请再试一次"));
    xhr.send(form);
  });
}

function suggestedDownloadName(entries) {
  const raw = entries.length === 1
    ? (entries[0].isDir ? `${entries[0].name}.zip` : entries[0].name)
    : "download.zip";
  const cleaned = String(raw).replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/[. ]+$/g, "");
  return cleaned || "download";
}

async function downloadEntries(entries) {
  const sessionId = state.activeId;
  if (!entries.length || !sessionId || ensureView(sessionId).downloading) return;
  const bridge = window.pywebview && window.pywebview.api;
  if (!bridge || typeof bridge.choose_save_path !== "function") {
    showError(new Error("当前窗口不能弹出保存位置"));
    return;
  }
  const label = entries.length === 1 ? entries[0].name : `${entries.length} 项`;
  const chosen = await bridge.choose_save_path(suggestedDownloadName(entries));
  if (!chosen || chosen.cancelled) return;
  if (chosen.error) {
    showError(new Error(chosen.error));
    return;
  }
  ensureView(sessionId).downloading = true;
  state.downloading = true;
  paintTransfer(`正在准备下载 ${label}…`, 0, sessionId);
  const timer = setInterval(() => {
    api(`/api/sessions/${sessionId}/activity`).then((data) => {
      if (!ensureView(sessionId).downloading || !data.active) return;
      const percent = data.total ? Math.min(99, Math.round((data.done / data.total) * 100)) : 0;
      if (data.message && data.total) paintTransfer(`${data.message} · ${percent}%`, percent, sessionId);
      else paintTransfer(data.message || `正在下载 ${label}…`, percent, sessionId);
    }).catch(() => {});
  }, 400);
  try {
    const data = await api(`/api/sessions/${sessionId}/download`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ paths: entries.map((entry) => entry.path), saveToken: chosen.token }),
    });
    toast(`已保存到 ${data.path || chosen.path}`);
  } catch (error) {
    showError(error);
  } finally {
    clearInterval(timer);
    releaseTransfer(sessionId, "download");
  }
}

async function downloadSelected() {
  await downloadEntries(selectedEntries());
}

async function showDownloadHistory() {
  const data = await api("/api/downloads");
  const items = data.items || [];
  await openDialog({
    title: "下载记录",
    message: items.length ? "点一条可以在资源管理器里定位这个文件。" : "还没有下载记录。",
    hideCancel: true,
    okText: "关闭",
    build(body) {
      if (!items.length) return;
      const list = document.createElement("div");
      list.className = "download-history";
      for (const item of items) list.append(renderDownloadRecord(item));
      body.append(list);
    },
  });
}

function renderDownloadRecord(item) {
  const row = document.createElement("div");
  row.className = "download-history-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "locate";
  const title = document.createElement("strong");
  title.textContent = item.name || "文件";
  const where = document.createElement("small");
  where.textContent = item.path || "";
  button.append(title, where);
  button.addEventListener("click", () => {
    api("/api/downloads/reveal", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ path: item.path }),
    }).catch(showError);
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger";
  remove.textContent = "删除";
  remove.title = "删除这条记录，并删除本机上的文件";
  remove.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const name = item.name || "这个文件";
    if (!window.confirm(`删除「${name}」？本机上的文件也会一起删掉。`)) return;
    api("/api/downloads/delete", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ path: item.path, savedAt: item.savedAt || "" }),
    }).then(() => {
      row.remove();
      const list = $("modal-body").querySelector(".download-history");
      if (list && !list.children.length) {
        list.remove();
        const message = $("modal-message");
        message.hidden = false;
        message.textContent = "还没有下载记录。";
      }
    }).catch(showError);
  });
  row.append(button, remove);
  return row;
}

async function removeSelected() {
  const entries = selectedEntries();
  if (!entries.length) return;
  const folder = entries.some((entry) => entry.isDir);
  const target = entries.length === 1 ? `「${entries[0].name}」` : `这 ${entries.length} 项`;
  const ok = await openDialog({
    title: "确认删除",
    message: folder
      ? `确定删除${target}？文件夹会连同里面的内容一起删除，而且不能从这里恢复。`
      : `确定删除${target}？删除后不能从这里恢复。`,
    okText: "删除",
    danger: true,
  });
  if (!ok) return;
  await busy("正在删除…", async () => {
    await api(`/api/sessions/${state.activeId}/delete`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ paths: entries.map((entry) => entry.path) }),
    });
    await browse(state.path);
  });
}

function freshName(directory) {
  const used = new Set(state.entries.map((entry) => entry.name));
  const base = directory ? "新建文件夹" : "新建文件";
  const ext = directory ? "" : ".txt";
  let name = base + ext;
  let index = 2;
  while (used.has(name)) {
    name = `${base} ${index}${ext}`;
    index += 1;
  }
  return name;
}

async function createEntry(directory) {
  if (!currentSession()?.connected || !state.path) return;
  const form = await openDialog({
    title: directory ? "新建文件夹" : "新建文件",
    fields: [{ name: "name", label: "名称", value: freshName(directory), required: true }],
    okText: "创建",
  });
  if (!form) return;
  const action = directory ? "mkdir" : "mkfile";
  await busy(directory ? "正在创建文件夹…" : "正在创建文件…", async () => {
    await api(`/api/sessions/${state.activeId}/${action}`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ parent: state.path, name: form.name }),
    });
    await browse(state.path);
  });
}

function createMenuItems() {
  return [
    { label: "新建文件夹", run: () => createEntry(true).catch(showError) },
    { label: "新建文件", run: () => createEntry(false).catch(showError) },
  ];
}

async function openBash(path) {
  const target = path || state.path;
  if (!currentSession()?.connected || !target) return;
  const data = await api(`/api/sessions/${state.activeId}/terminal`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ path: target }),
  });
  if (data.message) toast(data.message);
}

async function renameEntry(entry) {
  const form = await openDialog({
    title: "重命名",
    fields: [{ name: "name", label: "新名称", value: entry.name, required: true }],
    okText: "重命名",
  });
  if (!form || form.name === entry.name) return;
  await busy("正在重命名…", async () => {
    await api(`/api/sessions/${state.activeId}/rename`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ path: entry.path, newName: form.name }),
    });
    await browse(state.path);
  });
}

async function openWithSystem(entry, choose) {
  if (isLargeFile(entry)) {
    const ok = await openDialog({
      title: "文件较大",
      message: `「${entry.name}」有 ${formatSize(entry.size)}。要下载到本机吗？`,
      okText: "下载",
    });
    if (!ok) return;
    await downloadEntries([entry]);
    return;
  }
  try {
    const data = await busy(choose ? "请在系统窗口里选择程序…" : "正在打开…", () => api(`/api/sessions/${state.activeId}/open-local`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ path: entry.path, choose: Boolean(choose) }),
    }));
    if (!data.cancelled && data.message) toast(data.message);
  } catch (error) {
    if (!choose && error && /文件夹/.test(error.message || "")) {
      await browse(entry.path);
      return;
    }
    throw error;
  }
}

let lastActivateAt = 0;

function activateEntry(entry) {
  if (!entry || state.busy) return;
  const now = Date.now();
  if (now - lastActivateAt < 400) return;
  lastActivateAt = now;
  if (entry.isDir) browse(entry.path).catch(showError);
  else openWithSystem(entry, false).catch(showError);
}

function sessionFields(session) {
  return [
    { name: "name", label: "名称", value: session ? session.name : "" },
    { name: "host", label: "主机", value: session ? session.host : "", required: true },
    { name: "port", label: "端口", type: "number", value: session ? String(session.port) : "22", required: true },
    { name: "username", label: "用户名", value: session ? session.username : "", required: true },
    {
      name: "auth",
      label: "认证",
      type: "select",
      value: session ? session.auth : "password",
      options: [
        ["password", "密码"],
        ["key", "私钥"],
        ["agent", "本机 SSH 代理 / 默认密钥"],
      ],
    },
    { name: "password", label: "密码", type: "password", placeholder: session && session.hasPassword ? "已保存，留空则不修改" : "", auth: "password" },
    { name: "rememberPassword", label: "记住密码", type: "checkbox", checked: true, auth: "password" },
    { name: "keyPath", label: "私钥路径", value: session ? session.keyPath : "", placeholder: "例如 C:\\Users\\你\\.ssh\\id_ed25519", browse: true, auth: "key" },
    { name: "passphrase", label: "私钥口令", type: "password", placeholder: session && session.hasPassphrase ? "已保存，留空则不修改" : "", auth: "key" },
    { name: "rememberPassphrase", label: "记住私钥口令", type: "checkbox", checked: true, auth: "key" },
  ];
}

async function saveSession(session) {
  const form = await openDialog({
    title: session ? "编辑连接" : "新建连接",
    fields: sessionFields(session),
    okText: "保存",
  });
  if (!form) return;
  const payload = {
    name: form.name || "",
    host: form.host.trim(),
    port: Number(form.port || 22),
    username: form.username.trim(),
    auth: form.auth,
    keyPath: form.keyPath || "",
    password: form.password || "",
    passphrase: form.passphrase || "",
    rememberPassword: Boolean(form.rememberPassword),
    rememberPassphrase: Boolean(form.rememberPassphrase),
  };
  const saved = session
    ? await api(`/api/sessions/${session.id}`, { method: "PUT", headers: jsonHeaders(), body: JSON.stringify(payload) })
    : await api("/api/sessions", { method: "POST", headers: jsonHeaders(), body: JSON.stringify(payload) });
  await loadSessions();
  if (!session) await openSession(saved.id);
  else toast("已保存。重新点一次连接即可用新的设置。");
}

async function installKey(session) {
  if (state.busy) return;
  const ok = await openDialog({
    title: "一键配置密钥",
    message: "会在本机准备一把专用密钥，并把公钥写入这台主机。完成后，这个连接改为密钥登录。如果还没连上，会先按现在的方式登录。",
    okText: "配置",
  });
  if (!ok) return;
  if (!session.connected) await openSession(session.id);
  const data = await busy("正在配置密钥…", () => api(`/api/sessions/${session.id}/install-key`, { method: "POST" }));
  await loadSessions();
  toast(data.message || "已配置密钥");
}

function sessionMenu(session) {
  return [
    { label: session.connected ? "重新连接" : "连接", run: () => openSession(session.id, { force: Boolean(session.connected) }).catch(showError) },
    { label: "编辑", run: () => saveSession(session).catch(showError) },
    { label: "一键配置密钥", run: () => installKey(session).catch(showError) },
    {
      label: "断开",
      run: () => api(`/api/sessions/${session.id}/disconnect`, { method: "POST" }).then(async () => {
        session.connected = false;
        views.delete(session.id);
        if (state.activeId === session.id) {
          state.path = "";
          state.parent = "/";
          state.entries = [];
          state.selected = new Set();
          state.uploading = false;
          state.downloading = false;
          state.uploadMessage = "";
          state.uploadPercent = 0;
        }
        render();
      }).catch(showError),
    },
    {
      label: "删除",
      danger: true,
      run: () => openDialog({
        title: "删除连接",
        message: `删除「${session.name}」？已保存的密码也会从系统凭据里去掉。`,
        okText: "删除",
        danger: true,
      }).then((ok) => {
        if (!ok) return null;
        return api(`/api/sessions/${session.id}`, { method: "DELETE" }).then(async () => {
          views.delete(session.id);
          if (state.activeId === session.id) {
            state.activeId = null;
            state.path = "";
            state.parent = "/";
            state.entries = [];
            state.selected = new Set();
            state.uploading = false;
            state.downloading = false;
            state.uploadMessage = "";
            state.uploadPercent = 0;
          }
          await loadSessions();
          render();
        });
      }).catch(showError),
    },
  ];
}

function entryMenu(entry) {
  const items = [
    {
      label: "打开",
      run: () => activateEntry(entry),
    },
  ];
  if (!entry.isDir) {
    items.push({ label: "打开方式…", run: () => openWithSystem(entry, true).catch(showError) });
  }
  items.push(
    ...createMenuItems(),
    { label: "打开 bash", run: () => openBash(entry.isDir ? entry.path : state.path).catch(showError) },
    { separator: true },
    { label: "下载", run: () => { state.selected = new Set([entry.path]); downloadSelected().catch(showError); } },
    { label: "复制路径", run: () => navigator.clipboard.writeText(entry.path).then(() => toast("已复制路径")).catch(showError) },
    { label: "重命名", run: () => renameEntry(entry).catch(showError) },
    { label: "删除", danger: true, run: () => { state.selected = new Set([entry.path]); removeSelected().catch(showError); } },
  );
  return items;
}

function showMenu(x, y, items) {
  const menu = $("menu");
  menu.replaceChildren();
  for (const item of items) {
    if (item.separator) {
      const separator = document.createElement("div");
      separator.className = "menu-sep";
      menu.append(separator);
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.label;
    if (item.danger) button.classList.add("danger");
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      hideMenu();
      item.run();
    });
    menu.append(button);
  }
  menu.hidden = false;
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) menu.style.left = `${window.innerWidth - rect.width - 8}px`;
  if (rect.bottom > window.innerHeight) menu.style.top = `${window.innerHeight - rect.height - 8}px`;
}

function hideMenu() {
  $("menu").hidden = true;
}

function openDialog({ title, message = "", fields = null, build = null, okText = "确定", danger = false, hideCancel = false }) {
  const modal = $("modal");
  const form = $("modal-form");
  const body = $("modal-body");
  const messageEl = $("modal-message");
  $("modal-title").textContent = title;
  messageEl.hidden = !message;
  messageEl.textContent = message || "";
  body.replaceChildren();
  if (fields) {
    for (const field of fields) {
      body.append(renderField(field));
    }
    const auth = form.elements.auth;
    if (auth) auth.onchange = () => applyAuthVisibility(form);
    applyAuthVisibility(form);
  }
  if (build) build(body);
  const ok = $("modal-ok");
  const cancel = $("modal-cancel");
  ok.textContent = okText;
  ok.classList.toggle("danger", danger);
  cancel.hidden = hideCancel;
  modal.hidden = false;
  const first = body.querySelector("input:not([hidden]), select, button");
  if (first) {
    first.focus();
    if (typeof first.select === "function") first.select();
  }
  return new Promise((resolve) => {
    const finish = (value) => {
      modal.hidden = true;
      state.dialogCancel = null;
      form.onsubmit = null;
      cancel.onclick = null;
      cancel.hidden = false;
      resolve(value);
    };
    state.dialogCancel = () => finish(null);
    $("modal-cancel").onclick = () => finish(null);
    form.onsubmit = (event) => {
      event.preventDefault();
      if (ok.disabled) return;
      if (!fields) {
        finish(true);
        return;
      }
      const data = {};
      for (const field of fields) {
        const input = form.elements[field.name];
        if (!input) continue;
        data[field.name] = field.type === "checkbox" ? input.checked : input.value;
        if (field.required && !String(data[field.name] || "").trim()) {
          toast(`请填写${field.label}`);
          return;
        }
      }
      finish(data);
    };
  });
}

async function chooseUploadFiles() {
  const session = currentSession();
  if (!session?.connected || state.dialogCancel) return;
  const dest = state.path || "/";
  const remote = new Map(state.entries.map((entry) => [entry.name, entry]));
  const rows = [];
  const queue = $("upload-queue-template").content.firstElementChild.cloneNode(true);
  const list = queue.querySelector("#queue-list");
  const all = queue.querySelector("#queue-all");
  const overwrite = queue.querySelector("#queue-overwrite");
  const submit = $("modal-ok");

  function update() {
    const selected = rows.filter((row) => row.check.checked);
    const names = new Set();
    const existing = [];
    let conflict = "";
    for (const { file } of selected) {
      if (names.has(file.name)) {
        conflict = `“${file.name}”有多个同名文件，请只勾选其中一个。`;
        break;
      }
      names.add(file.name);
      if (remote.get(file.name)?.isDir) {
        conflict = `目标目录已有名为“${file.name}”的文件夹，请取消勾选这个文件。`;
        break;
      }
      if (remote.has(file.name)) existing.push(file.name);
    }
    const needsOverwrite = !conflict && existing.length > 0;
    let warning = conflict;
    if (needsOverwrite) {
      const preview = existing.slice(0, 3).join("、");
      warning = `目标目录已有 ${existing.length} 个同名文件：${preview}${existing.length > 3 ? "…" : ""}`;
    }
    queue.querySelector("#queue-warning").textContent = warning;
    queue.querySelector("#queue-warning").hidden = !warning;
    queue.querySelector("#queue-overwrite-row").hidden = !needsOverwrite;
    queue.querySelector("#queue-empty").hidden = rows.length > 0;
    queue.querySelector("#queue-count").textContent = `已选 ${selected.length} / ${rows.length}`;
    all.disabled = !rows.length;
    all.checked = rows.length > 0 && rows.length === selected.length;
    all.indeterminate = selected.length > 0 && selected.length < rows.length;
    submit.disabled = !selected.length || Boolean(conflict) || (needsOverwrite && !overwrite.checked);
  }

  function addFiles(input, checked) {
    const fragment = document.createDocumentFragment();
    for (const file of input.files) {
      const label = document.createElement("label");
      label.className = "queue-row";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = checked;
      const path = document.createElement("span");
      path.textContent = file.webkitRelativePath || file.name;
      const size = document.createElement("small");
      size.textContent = formatSize(file.size, false);
      label.append(check, path, size);
      fragment.append(label);
      rows.push({ file, check });
    }
    list.append(fragment);
    input.value = "";
    overwrite.checked = false;
    update();
  }

  const files = queue.querySelector("#file-input");
  const folder = queue.querySelector("#queue-folder-input");
  queue.querySelector("#queue-add-files").onclick = () => files.click();
  queue.querySelector("#queue-browse-folder").onclick = () => folder.click();
  files.onchange = () => addFiles(files, true);
  folder.onchange = () => addFiles(folder, false);
  all.onchange = () => {
    for (const row of rows) row.check.checked = all.checked;
    overwrite.checked = false;
    update();
  };
  list.onchange = () => { overwrite.checked = false; update(); };
  overwrite.onchange = update;

  let items;
  $("app").inert = true;
  try {
    const confirmed = await openDialog({
      title: "上传文件",
      message: `所选文件将直接上传到 ${session.name} 的 ${dest}`,
      okText: "确认上传",
      build: (body) => { body.append(queue); update(); },
    });
    if (confirmed) {
      items = rows.filter((row) => row.check.checked).map(({ file }) => ({ file, relative: file.name }));
    }
  } finally {
    rows.length = 0;
    queue.remove();
    submit.disabled = false;
    $("app").inert = false;
    $("upload").focus();
  }
  if (!items?.length) return;
  if (state.activeId !== session.id || (state.path || "/") !== dest || !currentSession()?.connected) {
    toast("上传目标已变化，请重新选择文件。");
    return;
  }
  return beginUpload(() => items);
}

function renderField(field) {
  if (field.type === "checkbox") {
    const label = document.createElement("label");
    label.className = "check";
    if (field.auth) label.dataset.auth = field.auth;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = field.name;
    input.checked = Boolean(field.checked);
    const text = document.createElement("span");
    text.textContent = field.label;
    label.append(input, text);
    return label;
  }
  const wrap = document.createElement("div");
  wrap.className = "field";
  if (field.auth) wrap.dataset.auth = field.auth;
  const label = document.createElement("label");
  label.textContent = field.label;
  const row = document.createElement("div");
  row.className = "field-row";
  let input;
  if (field.type === "select") {
    input = document.createElement("select");
    for (const [value, text] of field.options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      input.append(option);
    }
    input.value = field.value || field.options[0][0];
  } else {
    input = document.createElement("input");
    input.type = field.type || "text";
    input.value = field.value || "";
    input.placeholder = field.placeholder || "";
  }
  input.name = field.name;
  input.autocomplete = "off";
  row.append(input);
  if (field.browse) {
    const browse = document.createElement("button");
    browse.type = "button";
    browse.textContent = "浏览";
    browse.addEventListener("click", async () => {
      try {
        const data = await api("/api/pick-file", { method: "POST" });
        if (data.path) input.value = data.path;
      } catch (error) {
        showError(error);
      }
    });
    row.append(browse);
  }
  wrap.append(label, row);
  return wrap;
}

function applyAuthVisibility(form) {
  const auth = form.elements.auth ? form.elements.auth.value : "";
  form.querySelectorAll("[data-auth]").forEach((element) => {
    element.hidden = auth ? !element.dataset.auth.split(" ").includes(auth) : false;
  });
}

function readEntries(reader) {
  return new Promise((resolve, reject) => {
    const all = [];
    const next = () => {
      reader.readEntries((batch) => {
        if (!batch.length) resolve(all);
        else {
          all.push(...batch);
          next();
        }
      }, reject);
    };
    next();
  });
}

// The drag data store is readable only during the synchronous drop event.
// Capture ALL roots before yielding; retaining DataTransferItem objects is not enough.
function captureDrop(transfer) {
  const items = Array.from(transfer.items || []).filter((item) => item.kind === "file");
  if (!items.length) return Array.from(transfer.files || [], (file) => ({ file }));
  return items.map((item) => {
    const entry = item.webkitGetAsEntry?.();
    if (entry) return { entry };
    const file = item.getAsFile?.();
    if (file) return { file };
    throw new Error("无法读取拖入的项目，请重新拖入，或点击“上传文件夹”。");
  });
}

async function collectDropped(roots, sessionId) {
  const items = [];
  async function add(item) {
    items.push(item);
    if (items.length === 1 || items.length % 40 === 0) {
      paintTransfer(`正在读取本地目录，已找到 ${items.length} 项…`, 0, sessionId);
      await yieldFrame();
    }
  }
  async function walk(entry, relative) {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      await add({ file, relative });
      return;
    }
    const children = await readEntries(entry.createReader());
    if (!children.length) await add({ relative });
    for (const child of children) await walk(child, relative + "/" + child.name);
  }
  for (const { entry, file } of roots) {
    if (entry) await walk(entry, entry.name);
    else await add({ file, relative: file.webkitRelativePath || file.name });
  }
  return items;
}

const rowClick = { path: "", at: 0 };

function paintSelection() {
  for (const tr of $("rows").querySelectorAll("tr[data-index]")) {
    const entry = state.entries[Number(tr.dataset.index)];
    tr.classList.toggle("selected", Boolean(entry && state.selected.has(entry.path)));
  }
}

function onRowClick(event) {
  const row = event.target.closest("tr[data-index]");
  if (!row) return;
  const entry = state.entries[Number(row.dataset.index)];
  if (!entry) return;
  const plain = !event.shiftKey && !event.ctrlKey && !event.metaKey;
  const now = Date.now();
  const again = plain && rowClick.path === entry.path && now - rowClick.at < 500;
  rowClick.path = entry.path;
  rowClick.at = now;
  if (again) {
    activateEntry(entry);
    return;
  }
  const visible = visibleEntries();
  if (event.shiftKey && state.anchor) {
    const paths = visible.map((item) => item.path);
    const start = paths.indexOf(state.anchor);
    const end = paths.indexOf(entry.path);
    if (start >= 0 && end >= 0) {
      const [lo, hi] = start < end ? [start, end] : [end, start];
      state.selected = new Set(paths.slice(lo, hi + 1));
    }
  } else if (event.ctrlKey || event.metaKey) {
    if (state.selected.has(entry.path)) state.selected.delete(entry.path);
    else state.selected.add(entry.path);
    state.anchor = entry.path;
  } else {
    state.selected = new Set([entry.path]);
    state.anchor = entry.path;
  }
  paintSelection();
  renderChrome();
}

function bind() {
  $("dir-input").setAttribute("webkitdirectory", "");
  $("dir-input").setAttribute("directory", "");
  $("new-session").addEventListener("click", () => saveSession(null).catch(showError));
  setInterval(() => {
    api("/api/notices").then((data) => {
      for (const note of data.notices || []) {
        if (note.message) toast(note.message);
      }
    }).catch(() => {});
  }, 2000);
  $("up").addEventListener("click", () => browse(state.parent || "/").catch(showError));
  $("refresh").addEventListener("click", () => browse(state.path).catch(showError));
  $("upload").addEventListener("click", () => chooseUploadFiles().catch(showError));
  $("upload-dir").addEventListener("click", () => $("dir-input").click());
  $("download").addEventListener("click", () => downloadSelected().catch(showError));
  $("download-history").addEventListener("click", () => showDownloadHistory().catch(showError));
  $("open-bash").addEventListener("click", () => openBash(state.path).catch(showError));
  $("sudo-btn").addEventListener("click", () => {
    const run = currentSession()?.sudo ? disableSudo() : offerSudo(false);
    run.catch(showError);
  });
  $("dir-input").addEventListener("change", () => {
    const input = $("dir-input");
    beginUpload(async (sessionId) => {
      try {
        return await listLocalFiles(input.files, sessionId);
      } finally {
        input.value = "";
      }
    });
  });
  $("address-bar").addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    beginAddressEdit();
  });
  $("address").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      endAddressEdit(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      endAddressEdit(false);
    }
  });
  $("address").addEventListener("blur", () => endAddressEdit(true));
  $("filter").addEventListener("input", () => {
    state.filter = $("filter").value;
    renderRows();
    renderChrome();
  });
  $("rows").addEventListener("click", onRowClick);
  $("rows").addEventListener("dblclick", (event) => {
    const row = event.target.closest("tr[data-index]");
    if (!row) return;
    const entry = state.entries[Number(row.dataset.index)];
    rowClick.path = "";
    activateEntry(entry);
  });
  document.querySelectorAll("#files th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sort.key === key) state.sort.dir *= -1;
      else state.sort = { key, dir: 1 };
      renderRows();
      renderChrome();
    });
  });
  const wrap = $("list-wrap");
  wrap.addEventListener("contextmenu", (event) => {
    if (!currentSession()?.connected || !state.path) return;
    const row = event.target.closest("tr[data-index]");
    event.preventDefault();
    if (!row) {
      showMenu(event.clientX, event.clientY, [
        ...createMenuItems(),
        { separator: true },
        { label: "打开 bash", run: () => openBash(state.path).catch(showError) },
      ]);
      return;
    }
    const entry = state.entries[Number(row.dataset.index)];
    if (!state.selected.has(entry.path)) {
      state.selected = new Set([entry.path]);
      state.anchor = entry.path;
      paintSelection();
      renderChrome();
    }
    showMenu(event.clientX, event.clientY, entryMenu(entry));
  });
  wrap.addEventListener("dragover", (event) => {
    if (!currentSession()?.connected) return;
    event.preventDefault();
    state.dropping = true;
    wrap.classList.add("dropping");
  });
  wrap.addEventListener("dragleave", () => {
    state.dropping = false;
    wrap.classList.remove("dropping");
  });
  wrap.addEventListener("drop", (event) => {
    event.preventDefault();
    state.dropping = false;
    wrap.classList.remove("dropping");
    if (!currentSession()?.connected) return;
    try {
      const roots = captureDrop(event.dataTransfer);
      beginUpload((sessionId) => collectDropped(roots, sessionId));
    } catch (error) {
      showError(error);
    }
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#menu")) hideMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (state.dialogCancel) state.dialogCancel();
      hideMenu();
      return;
    }
    if (state.dialogCancel || isTyping(event.target) || !currentSession()?.connected) return;
    if (event.key === "F5") {
      event.preventDefault();
      browse(state.path).catch(showError);
    } else if (event.key === "Delete") {
      removeSelected().catch(showError);
    } else if (event.key === "Backspace") {
      if (state.path && state.path !== "/") browse(state.parent || "/").catch(showError);
    } else if (event.key === "F2" && state.selected.size === 1) {
      renameEntry(selectedEntries()[0]).catch(showError);
    } else if (event.key === "Enter" && state.selected.size === 1) {
      const entry = selectedEntries()[0];
      activateEntry(entry);
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      state.selected = new Set(visibleEntries().map((entry) => entry.path));
      renderRows();
      renderChrome();
    }
  });
  const toggle = $("toggle-sidebar");
  const applySidebar = (collapsed) => {
    $("app").classList.toggle("sidebar-collapsed", collapsed);
    toggle.title = collapsed ? "展开连接面板" : "收起连接面板";
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.textContent = collapsed ? "›" : "‹";
  };
  applySidebar(localStorage.getItem("rf-sidebar-collapsed") === "1");
  toggle.addEventListener("click", () => {
    const collapsed = !$("app").classList.contains("sidebar-collapsed");
    applySidebar(collapsed);
    localStorage.setItem("rf-sidebar-collapsed", collapsed ? "1" : "0");
  });
  const resizer = $("resizer");
  resizer.addEventListener("mousedown", (event) => {
    if ($("app").classList.contains("sidebar-collapsed")) return;
    event.preventDefault();
    const move = (ev) => {
      const width = Math.min(460, Math.max(220, ev.clientX));
      document.documentElement.style.setProperty("--sidebar", `${width}px`);
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}

function isTyping(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "SELECT" || target.isContentEditable;
}

bind();
render();
loadSessions().then(render).catch(showError);
