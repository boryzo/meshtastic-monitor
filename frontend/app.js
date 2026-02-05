/* global window, document, localStorage, fetch */
const LS = {
  apiBaseUrl: "meshmon.apiBaseUrl",
  transport: "meshmon.transport",
  meshHost: "meshmon.meshHost",
  meshPort: "meshmon.meshPort",
  mqttHost: "meshmon.mqttHost",
  mqttPort: "meshmon.mqttPort",
  mqttUsername: "meshmon.mqttUsername",
  mqttPassword: "meshmon.mqttPassword",
  mqttTls: "meshmon.mqttTls",
  mqttRootTopic: "meshmon.mqttRootTopic",
  sendChannel: "meshmon.sendChannel",
  messagesHistoryLimit: "meshmon.messagesHistoryLimit",
};
function $(id) {
  return document.getElementById(id);
}
function getApiBaseUrl() {
  const v = (localStorage.getItem(LS.apiBaseUrl) || "").trim();
  return v.replace(/\/+$/, "");
}
async function apiFetch(path, opts = {}) {
  const base = getApiBaseUrl();
  const url = base ? `${base}${path}` : path;
  const res = await fetch(url, opts);
  if (!res.ok) {
    let body = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    const msg = body && body.error ? body.error : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return await res.json();
}
function fmtAge(ageSec) {
  if (ageSec === null || ageSec === undefined) return "—";
  const s = Math.max(0, Number(ageSec) || 0);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}
function fmtTime(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
let activeNodesTab = "direct";
let lastMessagesKey = "";
let lastHealth = null;
let channelsByIndex = new Map();
let channelsVersion = 0;
let lastMessageFp = null;
let unreadCount = 0;
const baseTitle = document.title;
let localRadioId = null;
let lastNodeDetailsId = null;
let lastStatsKey = "";
let lastMessages = null;
let activeMainTab = "status";
let activeMessageChannel = "all";
let nodesHistory = [];
let activeNodeId = null;
const nodesSort = {
  direct: { key: "age", dir: "asc" }, // fresh first
  relayed: { key: "age", dir: "asc" }, // fresh first
};
function showToast(kind, text) {
  const toast = $("toast");
  toast.classList.remove("hidden", "ok", "err");
  toast.classList.add(kind === "ok" ? "ok" : "err");
  toast.textContent = text;
  window.clearTimeout(showToast._t);
  showToast._t = window.setTimeout(() => toast.classList.add("hidden"), 2600);
}
function setConnStatus(connected) {
  const el = $("connStatus");
  el.classList.remove("status-good", "status-bad", "status-unknown");
  if (connected === true) {
    el.classList.add("status-good");
    el.textContent = "Connected";
  } else if (connected === false) {
    el.classList.add("status-bad");
    el.textContent = "Disconnected";
  } else {
    el.classList.add("status-unknown");
    el.textContent = "Unknown";
  }
}
function getNodesSortState() {
  return nodesSort[activeNodesTab] || nodesSort.direct;
}
function qualityRank(q) {
  const v = (q || "").toString().trim().toLowerCase();
  if (!v) return null;
  if (v === "good") return 4;
  if (v === "ok") return 3;
  if (v === "weak") return 2;
  if (v === "bad") return 1;
  return null;
}
function cmpNullableNumber(a, b, dir = "asc") {
  const aNull = a === null || a === undefined || Number.isNaN(Number(a));
  const bNull = b === null || b === undefined || Number.isNaN(Number(b));
  if (aNull && bNull) return 0;
  if (aNull) return 1; // nulls last
  if (bNull) return -1;
  const av = Number(a);
  const bv = Number(b);
  if (av === bv) return 0;
  const sign = av < bv ? -1 : 1;
  return dir === "desc" ? -sign : sign;
}
function compareNodes(a, b, sortState) {
  const key = sortState && sortState.key ? String(sortState.key) : "age";
  const dir = sortState && sortState.dir === "desc" ? "desc" : "asc";
  let res = 0;
  if (key === "snr") {
    res = cmpNullableNumber(a.snr, b.snr, dir);
  } else if (key === "quality") {
    res = cmpNullableNumber(qualityRank(a.quality), qualityRank(b.quality), dir);
  } else if (key === "hops") {
    res = cmpNullableNumber(a.hopsAway, b.hopsAway, dir);
  } else {
    // age (last seen)
    res = cmpNullableNumber(a.ageSec, b.ageSec, dir);
  }
  if (res !== 0) return res;
  // Tie-breakers: always keep freshest near top, then stable by id.
  res = cmpNullableNumber(a.ageSec, b.ageSec, "asc");
  if (res !== 0) return res;
  return String(a.id || "").localeCompare(String(b.id || ""));
}
function applyNodesSortUi() {
  const state = getNodesSortState();
  const table = $("nodesTable");
  if (!table) return;
  table.querySelectorAll("th[data-sort]").forEach((th) => {
    const key = th.getAttribute("data-sort");
    th.classList.remove("sorted-asc", "sorted-desc");
    th.removeAttribute("aria-sort");
    if (key && state.key === key) {
      const cls = state.dir === "desc" ? "sorted-desc" : "sorted-asc";
      th.classList.add(cls);
      th.setAttribute("aria-sort", state.dir === "desc" ? "descending" : "ascending");
    }
  });
}
function onNodesHeaderClick(ev) {
  const th = ev.target.closest("th[data-sort]");
  if (!th) return;
  const key = (th.getAttribute("data-sort") || "").trim();
  if (!key) return;
  const state = getNodesSortState();
  if (state.key === key) {
    state.dir = state.dir === "asc" ? "desc" : "asc";
  } else {
    state.key = key;
    state.dir = key === "age" ? "asc" : "desc";
  }
  applyNodesSortUi();
  if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
}
function renderNodes(nodes, filterText) {
  const tbody = $("nodesTbody");
  const rows = [];
  const f = (filterText || "").trim().toLowerCase();
  const list = activeNodesTab === "direct" ? nodes.direct : nodes.relayed;
  const filtered = list.filter((n) => {
    if (!f) return true;
    const hay = `${n.short || ""} ${n.long || ""} ${n.id || ""}`.toLowerCase();
    return hay.includes(f);
  });
  const sortState = getNodesSortState();
  filtered.sort((a, b) => compareNodes(a, b, sortState));
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="muted">No nodes</td></tr>`;
    return;
  }
  for (const n of filtered) {
    const nodeId = n.id ? String(n.id) : "";
    const snr = n.snr === null || n.snr === undefined ? "—" : String(n.snr);
    const hops = n.hopsAway === null || n.hopsAway === undefined ? "—" : String(n.hopsAway);
    const q = n.quality || "—";
    const qPill =
      n.quality
        ? `<span class="pill ${n.quality}">${n.quality}</span>`
        : `<span class="muted">—</span>`;
    const role = n.role || "—";
    const hwModel = n.hwModel || "—";
    rows.push(`
      <tr class="node-row" ${nodeId ? `data-node-id="${escapeHtml(nodeId)}"` : ""} title="Click to send to this node">
        <td>${escapeHtml(n.short || "—")}</td>
        <td>${escapeHtml(n.long || "—")}</td>
        <td class="col-role">${escapeHtml(role)}</td>
        <td class="col-device">${escapeHtml(hwModel)}</td>
        <td class="col-snr">${escapeHtml(snr)}</td>
        <td class="col-quality">${q === "—" ? `<span class="muted">—</span>` : qPill}</td>
        <td class="col-hops">${escapeHtml(hops)}</td>
        <td>${escapeHtml(fmtAge(n.ageSec))}</td>
        <td class="col-id">${escapeHtml(n.id || "—")}</td>
      </tr>
    `);
  }
  tbody.innerHTML = rows.join("");
}
function channelSuffix(info) {
  if (!info) return "";
  if (info.name) return ` (${info.name})`;
  if (info.preset) return ` (${info.preset})`;
  return "";
}
function formatChannelIndexLabel(index, info) {
  return `#${index}${channelSuffix(info)}`;
}
function formatChannelInfoLabel(index, info) {
  return `Ch ${index}${channelSuffix(info)}`;
}
function formatChannelHashLabel(index) {
  return `#${index} (hash)`;
}
function getObservedChannelIds(messages) {
  if (!Array.isArray(messages)) return [];
  const observed = new Set();
  for (const m of messages) {
    if (m.channel === null || m.channel === undefined) continue;
    const num = Number(m.channel);
    if (!Number.isInteger(num)) continue;
    observed.add(String(num));
  }
  return Array.from(observed).sort((a, b) => Number(a) - Number(b));
}
function channelInfo(chNum) {
  if (chNum === null || chNum === undefined || Number.isNaN(Number(chNum))) {
    return { label: "Ch —", known: false };
  }
  const num = Number(chNum);
  const info = channelsByIndex.get(num);
  if (info) {
    return { label: formatChannelInfoLabel(num, info), known: true };
  }
  return { label: `Ch hash ${num}`, known: false };
}
function renderMessages(messages) {
  const list = $("messagesList");
  const selected = activeMessageChannel;
  const filtered = Array.isArray(messages)
    ? messages.filter((m) => {
        if (selected === "all") return true;
        if (m.channel === null || m.channel === undefined) return false;
        return String(m.channel) === String(selected);
      })
    : [];
  const key = JSON.stringify([
    channelsVersion,
    selected,
    filtered.map((m) => [m.rxTime, m.fromId, m.toId, m.text, m.portnum, m.channel, m.app, m.requestId, m.wantResponse]),
  ]);
  if (key === lastMessagesKey) return;
  lastMessagesKey = key;
  if (!filtered || filtered.length === 0) {
    list.innerHTML = `<div class="muted">No messages yet</div>`;
    return;
  }
  const rows = [];
  for (const m of filtered) {
    const from = m.fromId || "—";
    const to = m.toId || "—";
    const snr = m.snr === null || m.snr === undefined ? "—" : String(m.snr);
    const rssi = m.rssi === null || m.rssi === undefined ? "—" : String(m.rssi);
    const chNum = m.channel === null || m.channel === undefined ? null : Number(m.channel);
    const chMeta = channelInfo(chNum);
    const chLabel = chMeta.label;
    const text = m.text ? escapeHtml(m.text) : `<span class="muted">port ${escapeHtml(String(m.portnum ?? "—"))}</span>`;
    const app = appNameForMessage(m);
    const isRequest = isAppRequestForMe(m, app);
    const appBadge = app && isRequest ? `<span class="pill ok">Request: ${escapeHtml(app)}</span>` : "";
    const direction = localRadioId && m.fromId === localRadioId ? "outgoing" : "incoming";
    const channelBadge =
      selected === "all" ? `<span class="pill">${escapeHtml(chLabel)}</span>` : "";
    rows.push(`
      <div class="msg ${direction}">
        <div class="meta">
          <span>${escapeHtml(fmtTime(m.rxTime))}</span>
          <span class="mono">${escapeHtml(from)} → ${escapeHtml(to)}</span>
          <span>SNR ${escapeHtml(snr)} / RSSI ${escapeHtml(rssi)}</span>
          ${channelBadge}
          ${appBadge}
        </div>
        <div class="text">${text}</div>
      </div>
    `);
  }
  list.innerHTML = rows.join("");
}
function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
async function tickHealth() {
  try {
    const h = await apiFetch("/api/health");
    lastHealth = h;
    if (h.transport === "mqtt") {
      $("meshHost").textContent = `MQTT ${h.mqttHost}:${h.mqttPort}`;
    } else if (h.meshHost) {
      $("meshHost").textContent = `${h.meshHost}:${h.meshPort}`;
    } else {
      $("meshHost").textContent = "TCP (not configured)";
    }
    if (h.configured === false) {
      setConnStatus(null);
      const el = $("connStatus");
      el.classList.remove("status-good", "status-bad", "status-unknown");
      el.classList.add("status-unknown");
      el.textContent = "Not configured";
    } else {
      setConnStatus(h.connected);
    }
    renderHealth(h);
  } catch (e) {
    $("meshHost").textContent = "—";
    setConnStatus(false);
    renderHealthError(e);
  }
}
function renderHealth(h) {
  const statusText =
    h.configured === false ? "Not configured" : h.connected ? "Connected" : "Disconnected";
  const rows = [];
  rows.push(kv("Status", statusText));
  rows.push(kv("Transport", String(h.transport || "—")));
  if (h.transport === "mqtt") {
    rows.push(kv("MQTT", `${h.mqttHost || "—"}:${h.mqttPort || "—"}`));
    rows.push(kv("MQTT TLS", h.mqttTls ? "true" : "false"));
    rows.push(kv("MQTT User", h.mqttUsername || "—"));
    rows.push(kv("MQTT Topic", h.mqttRootTopic || "—"));
  } else {
    rows.push(kv("Mesh", h.meshHost ? `${h.meshHost}:${h.meshPort}` : "—"));
  }
  if (h.lastError) {
    rows.push(kv("Last error", String(h.lastError), true));
  }
  rows.push(
    kv(
      "Updated",
      h.generatedAt ? new Date(h.generatedAt * 1000).toLocaleTimeString() : "—"
    )
  );
  $("healthDetails").innerHTML = rows.join("");
  $("healthJson").textContent = JSON.stringify(h, null, 2);
}
function renderHealthError(e) {
  $("healthDetails").innerHTML = `<div class="muted">Failed to load: ${escapeHtml(e.message)}</div>`;
  $("healthJson").textContent = "";
}
function kv(key, value, isErr = false) {
  return `<div class="k">${escapeHtml(key)}</div><div class="v${isErr ? " err" : ""}">${escapeHtml(value)}</div>`;
}
function fmtNum(val, digits = 2) {
  if (val === null || val === undefined) return "—";
  const n = Number(val);
  if (Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}
function fmtBool(val) {
  if (val === true) return "true";
  if (val === false) return "false";
  return "—";
}
function fmtCount(val) {
  if (val === null || val === undefined) return "0";
  const n = Number(val);
  return Number.isNaN(n) ? "0" : String(Math.max(0, Math.trunc(n)));
}
function fmtNumCompact(val) {
  if (val === null || val === undefined) return "—";
  const n = Number(val);
  if (Number.isNaN(n)) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.trunc(n));
}
function appNameForMessage(m) {
  const raw = m.app || m.portnum;
  if (!raw) return null;
  if (typeof raw === "string") {
    if (raw === "POSITION_APP") return "Position";
    if (raw === "NODEINFO_APP") return "NodeInfo";
    if (raw === "ROUTING_APP") return "Routing";
    if (raw === "TELEMETRY_APP") return "Telemetry";
    return null;
  }
  const num = Number(raw);
  if (Number.isNaN(num)) return null;
  if (num === 3) return "Position";
  if (num === 4) return "NodeInfo";
  if (num === 5) return "Routing";
  if (num === 0x43) return "Telemetry";
  return null;
}
function isAppRequestForMe(m, appName) {
  if (!appName) return false;
  const hasRequestId = m.requestId !== null && m.requestId !== undefined;
  const wantsResponse = m.wantResponse === true;
  if (!hasRequestId && !wantsResponse) return false;
  if (!localRadioId) return true;
  if (m.fromId && m.fromId === localRadioId) return false; // my own request
  if (!m.toId) return true;
  return m.toId === localRadioId;
}
let lastNodes = null;
async function tickNodes() {
  try {
    const n = await apiFetch("/api/nodes");
    lastNodes = n;
    recordNodesHistory(n.total, n.generatedAt);
    const ft = $("nodeFilter").value;
    renderNodes(n, ft);
    let src = "";
    if (n && n.includeObserved && typeof n.meshCount === "number") {
      const add = Number(n.observedAdded || 0);
      src = add > 0 ? ` • ${n.meshCount} mesh + ${add} observed` : ` • ${n.meshCount} mesh`;
    }
    $("nodesMeta").textContent = `${n.total} total${src} • updated ${new Date(n.generatedAt * 1000).toLocaleTimeString()}`;
    // Hide SNR/Quality columns on relayed tab
    const showDirectCols = activeNodesTab === "direct";
    [".col-snr", ".col-quality"].forEach((cls) => {
      document.querySelectorAll(cls).forEach((el) => (el.style.display = showDirectCols ? "" : "none"));
    });
    applyNodesSortUi();
  } catch (e) {
    $("nodesMeta").textContent = `Failed to load nodes: ${e.message}`;
  }
}
async function tickRadio() {
  try {
    const data = await apiFetch("/api/radio");
    renderRadio(data);
  } catch (e) {
    $("radioDetails").innerHTML = `<div class="muted">Failed to load: ${escapeHtml(e.message)}</div>`;
    $("radioMeta").textContent = "—";
  }
}
function renderRadio(data) {
  const details = $("radioDetails");
  const meta = $("radioMeta");
  if (!data || data.ok === false) {
    details.innerHTML = `<div class="muted">Not available</div>`;
    meta.textContent = "—";
    return;
  }
  const node = data.node;
  if (!node) {
    const status = data.configured === false ? "Not configured" : data.connected ? "Connected" : "Disconnected";
    details.innerHTML = `<div class="muted">No local node info yet (${status})</div>`;
    meta.textContent = status;
    localRadioId = null;
    return;
  }
  localRadioId = node.id || null;
  const rows = [];
  rows.push(kv("Status", data.connected ? "Connected" : "Disconnected"));
  rows.push(kv("ID", node.id || "—"));
  rows.push(kv("Short", node.short || "—"));
  rows.push(kv("Long", node.long || "—"));
  rows.push(kv("Device", node.hwModel || "—"));
  rows.push(kv("Role", node.role || "—"));
  rows.push(kv("Channel", node.channel ?? "—"));
  rows.push(kv("Hops", node.hopsAway ?? "—"));
  rows.push(kv("Last Seen", fmtAge(node.ageSec)));
  rows.push(kv("SNR", node.snr ?? "—"));
  rows.push(kv("Quality", node.quality || "—"));
  rows.push(kv("Battery", node.batteryLevel !== null && node.batteryLevel !== undefined ? `${fmtNum(node.batteryLevel, 0)}%` : "—"));
  rows.push(kv("Voltage", node.voltage !== null && node.voltage !== undefined ? `${fmtNum(node.voltage, 2)} V` : "—"));
  rows.push(kv("Channel Util", node.channelUtilization !== null && node.channelUtilization !== undefined ? `${fmtNum(node.channelUtilization, 1)}%` : "—"));
  rows.push(kv("Air Util Tx", node.airUtilTx !== null && node.airUtilTx !== undefined ? `${fmtNum(node.airUtilTx, 1)}%` : "—"));
  rows.push(kv("Favorite", fmtBool(node.isFavorite)));
  rows.push(kv("Muted", fmtBool(node.isMuted)));
  rows.push(kv("Ignored", fmtBool(node.isIgnored)));
  rows.push(kv("Via MQTT", fmtBool(node.viaMqtt)));
  if (node.position) {
    const lat = node.position.latitude !== undefined ? fmtNum(node.position.latitude, 5) : "—";
    const lon = node.position.longitude !== undefined ? fmtNum(node.position.longitude, 5) : "—";
    const alt = node.position.altitude !== undefined ? fmtNum(node.position.altitude, 1) : "—";
    rows.push(kv("Lat", lat));
    rows.push(kv("Lon", lon));
    rows.push(kv("Alt", alt));
  }
  details.innerHTML = rows.join("");
  meta.textContent = data.generatedAt
    ? `updated ${new Date(data.generatedAt * 1000).toLocaleTimeString()}`
    : "—";
}
async function loadNodeDetails(nodeId) {
  if (!nodeId) return;
  lastNodeDetailsId = nodeId;
  $("nodeDetailsMeta").textContent = `Loading ${nodeId}…`;
  try {
    const data = await apiFetch(`/api/node/${encodeURIComponent(nodeId)}`);
    renderNodeDetails(nodeId, data);
    renderNodeMessages(nodeId);
  } catch (e) {
    $("nodeDetails").innerHTML = `<div class="muted">Failed to load: ${escapeHtml(e.message)}</div>`;
    $("nodeDetailsMeta").textContent = "—";
  }
}
function renderNodeDetails(nodeId, data) {
  const el = $("nodeDetails");
  if (!data || data.ok === false) {
    el.innerHTML = `<div class="muted">No data</div>`;
    $("nodeDetailsMeta").textContent = "—";
    return;
  }
  const node = data.node || {};
  const stats = data.stats || {};
  const rows = [];
  rows.push(kv("ID", nodeId));
  rows.push(kv("Short", node.short || "—"));
  rows.push(kv("Long", node.long || "—"));
  rows.push(kv("Role", node.role || stats.role || "—"));
  rows.push(kv("Device", node.hwModel || stats.hwModel || "—"));
  rows.push(kv("Firmware", node.firmware || stats.firmware || "—"));
  rows.push(kv("SNR", node.snr ?? stats.snr ?? "—"));
  rows.push(kv("RSSI", stats.rssi ?? "—"));
  rows.push(kv("Quality", node.quality || stats.quality || "—"));
  rows.push(kv("Hops", node.hopsAway ?? stats.hopsAway ?? "—"));
  rows.push(kv("Last Seen", fmtAge(node.ageSec ?? stats.ageSec)));
  rows.push(kv("Last Rx", stats.lastRx ? fmtTime(stats.lastRx) : "—"));
  rows.push(kv("From Count", fmtCount(stats.fromCount)));
  rows.push(kv("To Count", fmtCount(stats.toCount)));
  el.innerHTML = rows.join("");
  $("nodeDetailsMeta").textContent = data.generatedAt
    ? `updated ${new Date(data.generatedAt * 1000).toLocaleTimeString()}`
    : "—";
}
function renderNodeMessages(nodeId) {
  const list = $("nodeMessages");
  if (!list) return;
  if (!nodeId) {
    list.innerHTML = `<div class="muted">Select a node to view messages.</div>`;
    return;
  }
  if (!Array.isArray(lastMessages)) {
    list.innerHTML = `<div class="muted">Loading…</div>`;
    return;
  }
  const filtered = lastMessages.filter(
    (m) => String(m.fromId || "") === String(nodeId) || String(m.toId || "") === String(nodeId)
  );
  if (filtered.length === 0) {
    list.innerHTML = `<div class="muted">No messages for this node yet.</div>`;
    return;
  }
  const recent = filtered.slice(-50);
  const rows = recent.map((m) => {
    const from = m.fromId || "—";
    const to = m.toId || "—";
    const snr = m.snr === null || m.snr === undefined ? "—" : String(m.snr);
    const rssi = m.rssi === null || m.rssi === undefined ? "—" : String(m.rssi);
    const text = m.text
      ? escapeHtml(m.text)
      : `<span class="muted">port ${escapeHtml(String(m.portnum ?? "—"))}</span>`;
    const direction = localRadioId && m.fromId === localRadioId ? "outgoing" : "incoming";
    return `
      <div class="msg ${direction}">
        <div class="meta">
          <span>${escapeHtml(fmtTime(m.rxTime))}</span>
          <span class="mono">${escapeHtml(from)} → ${escapeHtml(to)}</span>
          <span>SNR ${escapeHtml(snr)} / RSSI ${escapeHtml(rssi)}</span>
        </div>
        <div class="text">${text}</div>
      </div>
    `;
  });
  list.innerHTML = rows.join("");
}
async function tickMessages() {
  try {
    const limitRaw = ($("messagesHistoryLimit") && $("messagesHistoryLimit").value) || "0";
    const limit = Number(limitRaw);
    const qs = Number.isFinite(limit) ? `?limit=${limit}` : "";
    const msgs = await apiFetch(`/api/messages${qs}`);
    lastMessages = msgs;
    notifyIfNewMessages(msgs);
    renderMessages(msgs);
    if (activeNodeId) renderNodeMessages(activeNodeId);
    const limitLabel = limitRaw === "0" ? "all" : `last ${limitRaw}`;
    let meta = `${msgs.length} messages (${limitLabel}) • refresh ${new Date().toLocaleTimeString()}`;
    if (unreadCount > 0) meta += ` • unread ${unreadCount}`;
    $("messagesMeta").textContent = meta;
  } catch (e) {
    $("messagesMeta").textContent = `Failed to load messages: ${e.message}`;
  }
}
function messageFingerprint(m) {
  const parts = [
    m.rxTime,
    m.fromId,
    m.toId,
    m.channel,
    m.portnum,
    m.text,
    m.payload_b64,
    m.error,
  ];
  return parts
    .map((v) => (v === null || v === undefined ? "" : String(v)))
    .join("|");
}
function countNewMessages(messages, lastFp) {
  if (!lastFp) return 0;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messageFingerprint(messages[i]) === lastFp) {
      return messages.length - 1 - i;
    }
  }
  // Ring buffer rotated or missing history; assume "some" new messages.
  return Math.min(messages.length, 51);
}
function msgPreview(m) {
  if (m.error) return `error: ${String(m.error)}`;
  const t = typeof m.text === "string" ? m.text.trim() : "";
  if (t) return t.length > 80 ? `${t.slice(0, 80)}…` : t;
  return `port ${m.portnum ?? "—"}`;
}
function updateTitle() {
  document.title = unreadCount > 0 ? `(${unreadCount}) ${baseTitle}` : baseTitle;
}
function recordNodesHistory(total, generatedAt) {
  const t = typeof generatedAt === "number" ? generatedAt : Math.floor(Date.now() / 1000);
  const count = Number(total);
  if (!Number.isFinite(count)) return;
  const last = nodesHistory.length ? nodesHistory[nodesHistory.length - 1] : null;
  if (last && last.ts === t) {
    last.total = count;
  } else {
    nodesHistory.push({ ts: t, total: count });
  }
  if (nodesHistory.length > 120) {
    nodesHistory = nodesHistory.slice(-120);
  }
  renderNodesVisibleHistory();
}
function renderNodesVisibleHistory() {
  const el = $("statsNodesVisible");
  if (!el) return;
  if (!nodesHistory.length) {
    el.innerHTML = `<div class="muted">No data</div>`;
    return;
  }
  const max = Math.max(1, ...nodesHistory.map((p) => Number(p.total) || 0));
  const bars = nodesHistory.map((p) => {
    const count = Number(p.total) || 0;
    const height = 10 + Math.round((count / max) * 50);
    const label = new Date(p.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const title = `${label} • ${count} nodes`;
    return `<div class="bar" style="height:${height}px" title="${escapeHtml(title)}">
      <span>${count}</span>
    </div>`;
  });
  el.innerHTML = bars.join("");
}
function setMainTab(tab) {
  const mapped = tab === "messages" ? "channels" : tab;
  activeMainTab = mapped;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const isActive = panel.getAttribute("data-tab") === mapped;
    panel.classList.toggle("active", isActive);
  });
  document.querySelectorAll("[data-main-tab]").forEach((btn) => {
    const isActive = btn.getAttribute("data-main-tab") === mapped;
    btn.classList.toggle("active", isActive);
    if (btn.getAttribute("data-main-tab")) {
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    }
  });
  localStorage.setItem("meshmon.mainTab", mapped);
}
function updateMessageChannelTabs(channels) {
  const container = $("messageChannelTabs");
  if (!container) return;
  const items = [{ id: "all", label: "All" }];
  const seen = new Set();
  if (Array.isArray(channels)) {
    for (const ch of channels) {
      if (typeof ch.index !== "number") continue;
      const id = String(ch.index);
      items.push({ id, label: formatChannelIndexLabel(ch.index, ch) });
      seen.add(id);
    }
  }
  for (const id of getObservedChannelIds(lastMessages)) {
    if (seen.has(id)) continue;
    items.push({ id, label: formatChannelHashLabel(id) });
  }
  container.innerHTML = items
    .map(
      (it) =>
        `<button class="tab" data-msg-channel="${escapeHtml(it.id)}">${escapeHtml(it.label)}</button>`
    )
    .join("");
  const selected = String(activeMessageChannel || "all");
  container.querySelectorAll("[data-msg-channel]").forEach((btn) => {
    const isActive = btn.getAttribute("data-msg-channel") === selected;
    btn.classList.toggle("active", isActive);
  });
}
function setMessageChannel(channelId) {
  activeMessageChannel = channelId || "all";
  const container = $("messageChannelTabs");
  if (container) {
    const selected = String(activeMessageChannel || "all");
    container.querySelectorAll("[data-msg-channel]").forEach((btn) => {
      const isActive = btn.getAttribute("data-msg-channel") === selected;
      btn.classList.toggle("active", isActive);
    });
  }
  if (lastMessages) renderMessages(lastMessages);
  if (activeMessageChannel !== "all") {
    const select = $("sendChannel");
    const nodeSelect = $("nodeSendChannel");
    const num = Number(activeMessageChannel);
    if (Number.isInteger(num) && channelsByIndex.has(num)) {
      if (select) select.value = String(activeMessageChannel);
      if (nodeSelect) nodeSelect.value = String(activeMessageChannel);
      localStorage.setItem(LS.sendChannel, String(activeMessageChannel));
    }
  }
}
function notifyIfNewMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    lastMessageFp = null;
    return;
  }
  const newest = messages[messages.length - 1];
  const newestFp = messageFingerprint(newest);
  if (lastMessageFp && newestFp !== lastMessageFp) {
    const count = countNewMessages(messages, lastMessageFp);
    const countLabel = count > 50 ? "50+ new messages" : `${count} new message${count === 1 ? "" : "s"}`;
    const from = newest.fromId || "—";
    const to = newest.toId || "—";
    const preview = msgPreview(newest);
    showToast("ok", `${countLabel}: ${from} → ${to} • ${preview}`);
    if (document.hidden) {
      unreadCount += count > 50 ? 50 : count;
      updateTitle();
    }
  }
  lastMessageFp = newestFp;
}
async function tickStats() {
  try {
    const data = await apiFetch("/api/stats");
    renderStats(data);
  } catch (e) {
    $("statsMeta").textContent = `Failed to load stats: ${e.message}`;
    $("statsSummary").innerHTML = `<div class="muted">Stats unavailable</div>`;
    ["statsHourly", "statsNodesVisible", "statsApps", "statsAppRequests", "statsTopFrom", "statsTopTo", "statsEvents"].forEach(
      (id) => {
        const el = $(id);
        if (el) el.innerHTML = "";
      }
    );
  }
}
function prettyAppName(app) {
  if (!app) return "—";
  if (app === "POSITION_APP") return "Position";
  if (app === "NODEINFO_APP") return "NodeInfo";
  if (app === "ROUTING_APP") return "Routing";
  if (app === "TELEMETRY_APP") return "Telemetry";
  return String(app);
}
function renderBars(hourly) {
  const el = $("statsHourly");
  if (!el) return;
  if (!Array.isArray(hourly) || hourly.length === 0) {
    el.innerHTML = `<div class="muted">No data</div>`;
    return;
  }
  const max = Math.max(1, ...hourly.map((h) => Number(h.messages) || 0));
  const bars = hourly.map((h) => {
    const count = Number(h.messages) || 0;
    const withText = Number(h.with_text ?? h.withText ?? h.with_text) || 0;
    const withPayload = Number(h.with_payload ?? h.withPayload ?? h.with_payload) || 0;
    const height = 10 + Math.round((count / max) * 50);
    const label = new Date((Number(h.hour) || 0) * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const title = `${label} • ${count} msgs (${withText} text, ${withPayload} payload)`;
    return `<div class="bar" style="height:${height}px" title="${escapeHtml(title)}">
      <span>${count}</span>
    </div>`;
  });
  el.innerHTML = bars.join("");
}
function renderList(elId, items, formatter, emptyText) {
  const el = $(elId);
  if (!el) return;
  if (!Array.isArray(items) || items.length === 0) {
    el.innerHTML = `<div class="muted">${emptyText}</div>`;
    return;
  }
  el.innerHTML = items.map(formatter).join("");
}
function renderStats(data) {
  if (!data || data.ok === false) {
    $("statsMeta").textContent = data && data.error ? data.error : "Stats disabled";
    $("statsSummary").innerHTML = `<div class="muted">Stats disabled</div>`;
    ["statsHourly", "statsNodesVisible", "statsApps", "statsAppRequests", "statsTopFrom", "statsTopTo", "statsEvents"].forEach(
      (id) => {
        const el = $(id);
        if (el) el.innerHTML = "";
      }
    );
    return;
  }
  const key = JSON.stringify([data.generatedAt, data.counters, data.messages, data.apps, data.nodes, data.events]);
  if (key === lastStatsKey) return;
  lastStatsKey = key;
  const counters = data.counters || {};
  const messages = data.messages || {};
  const apps = data.apps || {};
  const nodes = data.nodes || {};
  const events = data.events || [];
  const updated = data.generatedAt
    ? new Date(data.generatedAt * 1000).toLocaleTimeString()
    : "—";
  const windowHours = messages.windowHours || data.windowHours || "—";
  $("statsMeta").textContent = `db ${data.dbPath || "—"} • window ${windowHours}h • updated ${updated}`;
  const currentNodes = lastNodes ? lastNodes.total : null;
  const summary = `
    <div class="stat">
      <div class="label">Messages (1h)</div>
      <div class="value">${fmtNumCompact(messages.lastHour)}</div>
    </div>
    <div class="stat">
      <div class="label">Messages (window)</div>
      <div class="value">${fmtNumCompact(messages.window)}</div>
    </div>
    <div class="stat">
      <div class="label">Total Messages</div>
      <div class="value">${fmtNumCompact(counters.messages_total)}</div>
    </div>
    <div class="stat">
      <div class="label">Text / Payload</div>
      <div class="value">${fmtNumCompact(counters.messages_text)} / ${fmtNumCompact(counters.messages_payload)}</div>
    </div>
    <div class="stat">
      <div class="label">Sends (ok/err)</div>
      <div class="value">${fmtNumCompact(counters.send_ok)} / ${fmtNumCompact(counters.send_error)}</div>
    </div>
    <div class="stat">
      <div class="label">Mesh (conn/disc)</div>
      <div class="value">${fmtNumCompact(counters.mesh_connect)} / ${fmtNumCompact(counters.mesh_disconnect)}</div>
    </div>
    <div class="stat">
      <div class="label">Nodes Visible</div>
      <div class="value">${fmtNumCompact(currentNodes)}</div>
    </div>
  `;
  $("statsSummary").innerHTML = summary;
  renderNodesVisibleHistory();
  renderBars(messages.hourlyWindow || []);
  renderList(
    "statsApps",
    apps.counts || [],
    (a) =>
      `<div class="list-row">
        <div>${escapeHtml(prettyAppName(a.app))}</div>
        <div class="mono">${fmtCount(a.total)} total</div>
        <div class="mono">${fmtCount(a.requests)} req</div>
      </div>`,
    "No app stats yet"
  );
  renderList(
    "statsAppRequests",
    apps.requestsToMe || [],
    (r) =>
      `<div class="list-row">
        <div>${escapeHtml(prettyAppName(r.app))}</div>
        <div class="mono">${escapeHtml(r.fromId || "—")} → ${escapeHtml(r.toId || "—")}</div>
        <div class="mono">${fmtCount(r.count)}</div>
      </div>`,
    "No requests recorded"
  );
  renderList(
    "statsTopFrom",
    nodes.topFrom || [],
    (n) =>
      `<div class="list-row">
        <div class="mono">${escapeHtml(n.id || "—")}</div>
        <div>${fmtCount(n.count)} msgs</div>
        <div class="muted">${n.lastSnr !== null && n.lastSnr !== undefined ? `SNR ${n.lastSnr}` : ""}</div>
      </div>`,
    "No incoming yet"
  );
  renderList(
    "statsTopTo",
    nodes.topTo || [],
    (n) =>
      `<div class="list-row">
        <div class="mono">${escapeHtml(n.id || "—")}</div>
        <div>${fmtCount(n.count)} msgs</div>
        <div class="muted">${n.lastRssi !== null && n.lastRssi !== undefined ? `RSSI ${n.lastRssi}` : ""}</div>
      </div>`,
    "No outgoing yet"
  );
  renderList(
    "statsEvents",
    events || [],
    (e) =>
      `<div class="list-row">
        <div>${escapeHtml(e.event || "event")}</div>
        <div class="muted">${escapeHtml(fmtTime(e.ts) || "—")}</div>
        <div class="muted">${escapeHtml(e.detail || "")}</div>
      </div>`,
    "No recent events"
  );
}
async function tickChannels() {
  try {
    const data = await apiFetch("/api/channels");
    channelsByIndex = new Map();
    if (Array.isArray(data.channels)) {
      for (const ch of data.channels) {
        if (typeof ch.index === "number") {
          const nm = (ch.name || "").trim();
          channelsByIndex.set(ch.index, {
            name: nm || null,
            preset: ch.preset || null,
          });
        }
      }
    }
    channelsVersion += 1;
    renderChannels(data);
  } catch (e) {
    const meta = $("channelsMeta");
    if (meta) meta.textContent = `Failed to load channels: ${e.message}`;
  }
}
function renderChannels(data) {
  const list = Array.isArray(data.channels) ? data.channels : [];
  updateSendChannelOptions(list);
  updateMessageChannelTabs(list);
  const updated = data.generatedAt ? new Date(data.generatedAt * 1000).toLocaleTimeString() : "—";
  if ($("channelsMeta")) {
    $("channelsMeta").textContent = `${list.length} channels • updated ${updated}`;
  }
}
function updateSendChannelOptions(channels) {
  const selects = [$("sendChannel"), $("nodeSendChannel")].filter(Boolean);
  const current = (localStorage.getItem(LS.sendChannel) || "").trim();
  const opts = [`<option value="">Auto (default)</option>`];
  const seen = new Set();
  if (Array.isArray(channels)) {
    for (const ch of channels) {
      if (typeof ch.index !== "number") continue;
      const idx = String(ch.index);
      const label = formatChannelIndexLabel(ch.index, ch);
      opts.push(`<option value="${escapeHtml(idx)}">${escapeHtml(label)}</option>`);
      seen.add(idx);
    }
  }
  for (const idx of getObservedChannelIds(lastMessages)) {
    if (seen.has(idx)) continue;
    const label = formatChannelHashLabel(idx);
    opts.push(`<option value="${escapeHtml(idx)}" disabled>${escapeHtml(label)}</option>`);
  }
  for (const select of selects) {
    select.innerHTML = opts.join("");
    if (current) {
      select.value = current;
    }
  }
}
function openNodeModal(nodeId) {
  activeNodeId = nodeId;
  $("nodeModalBackdrop").classList.remove("hidden");
  $("nodeModal").classList.remove("hidden");
  $("nodeModalBackdrop").setAttribute("aria-hidden", "false");
  if ($("nodeSendResult")) $("nodeSendResult").textContent = "";
  if (nodeId) {
    $("nodeDetailsMeta").textContent = `Loading ${nodeId}…`;
    loadNodeDetails(nodeId);
    renderNodeMessages(nodeId);
    const textEl = $("nodeSendText");
    if (textEl) textEl.focus();
  }
}
function closeNodeModal() {
  $("nodeModalBackdrop").classList.add("hidden");
  $("nodeModal").classList.add("hidden");
  $("nodeModalBackdrop").setAttribute("aria-hidden", "true");
  activeNodeId = null;
}
function openModal() {
  $("modalBackdrop").classList.remove("hidden");
  $("modal").classList.remove("hidden");
  $("modalBackdrop").setAttribute("aria-hidden", "false");
  $("apiBaseUrl").value = localStorage.getItem(LS.apiBaseUrl) || "";
  $("transportSelect").value = localStorage.getItem(LS.transport) || "tcp";
  $("meshHostInput").value = localStorage.getItem(LS.meshHost) || "";
  $("meshPortInput").value = localStorage.getItem(LS.meshPort) || "4403";
  $("mqttHostInput").value = localStorage.getItem(LS.mqttHost) || "";
  $("mqttPortInput").value = localStorage.getItem(LS.mqttPort) || "1883";
  $("mqttUsernameInput").value = localStorage.getItem(LS.mqttUsername) || "";
  $("mqttPasswordInput").value = localStorage.getItem(LS.mqttPassword) || "";
  $("mqttTlsInput").checked = (localStorage.getItem(LS.mqttTls) || "") === "1";
  $("mqttRootTopicInput").value = localStorage.getItem(LS.mqttRootTopic) || "";
  applyTransportUi();
}
function closeModal() {
  $("modalBackdrop").classList.add("hidden");
  $("modal").classList.add("hidden");
  $("modalBackdrop").setAttribute("aria-hidden", "true");
}
function applyTransportUi() {
  const transport = ($("transportSelect").value || "tcp").toLowerCase();
  if (transport === "mqtt") {
    $("tcpSettings").classList.add("hidden");
    $("mqttSettings").classList.remove("hidden");
  } else {
    $("mqttSettings").classList.add("hidden");
    $("tcpSettings").classList.remove("hidden");
  }
}
async function saveSettings() {
  const apiBaseUrl = ($("apiBaseUrl").value || "").trim();
  const transport = ($("transportSelect").value || "tcp").trim().toLowerCase();
  const meshHost = ($("meshHostInput").value || "").trim();
  const meshPort = ($("meshPortInput").value || "").trim();
  const mqttHost = ($("mqttHostInput").value || "").trim();
  const mqttPort = ($("mqttPortInput").value || "").trim();
  const mqttUsername = ($("mqttUsernameInput").value || "").trim();
  const mqttPassword = ($("mqttPasswordInput").value || "").trim();
  const mqttTls = $("mqttTlsInput").checked;
  const mqttRootTopic = ($("mqttRootTopicInput").value || "").trim();
  localStorage.setItem(LS.apiBaseUrl, apiBaseUrl);
  localStorage.setItem(LS.transport, transport);
  localStorage.setItem(LS.meshHost, meshHost);
  localStorage.setItem(LS.meshPort, meshPort);
  localStorage.setItem(LS.mqttHost, mqttHost);
  localStorage.setItem(LS.mqttPort, mqttPort);
  localStorage.setItem(LS.mqttUsername, mqttUsername);
  if (mqttPassword) {
    localStorage.setItem(LS.mqttPassword, mqttPassword);
  }
  localStorage.setItem(LS.mqttTls, mqttTls ? "1" : "0");
  localStorage.setItem(LS.mqttRootTopic, mqttRootTopic);
  if (transport === "tcp" && !meshHost) {
    showToast("err", "Meshtastic host is required for TCP");
    return;
  }
  if (transport === "mqtt" && !mqttHost) {
    showToast("err", "MQTT host is required for MQTT");
    return;
  }
  const body = { transport };
  if (meshHost) body.meshHost = meshHost;
  if (meshPort) body.meshPort = Number(meshPort || "4403");
  if (mqttHost) body.mqttHost = mqttHost;
  if (mqttPort) body.mqttPort = Number(mqttPort || "1883");
  if (mqttUsername) body.mqttUsername = mqttUsername;
  if (mqttPassword) body.mqttPassword = mqttPassword; // omit when blank => keep current
  body.mqttTls = mqttTls;
  if (mqttRootTopic) body.mqttRootTopic = mqttRootTopic;
  try {
    await apiFetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (transport === "mqtt") {
      showToast("ok", `Applied MQTT: ${mqttHost}:${mqttPort || "1883"}`);
    } else {
      showToast("ok", `Applied TCP: ${meshHost}:${meshPort || "4403"}`);
    }
    await tickHealth();
    await tickNodes();
  } catch (e) {
    showToast("err", `Failed to apply settings: ${e.message}`);
  }
  closeModal();
}
function downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
async function copyHealthJson() {
  if (!lastHealth) {
    showToast("err", "Health not loaded yet");
    return;
  }
  const text = JSON.stringify(lastHealth, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    showToast("ok", "Copied health JSON");
  } catch {
    // Fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    showToast("ok", "Copied health JSON");
  }
}
function toggleHealthJson() {
  const pre = $("healthJson");
  const btn = $("btnToggleHealthJson");
  const isHidden = pre.classList.contains("hidden");
  if (isHidden) {
    pre.classList.remove("hidden");
    btn.textContent = "Hide JSON";
  } else {
    pre.classList.add("hidden");
    btn.textContent = "Show JSON";
  }
}
async function exportConfig() {
  try {
    const h = await apiFetch("/api/health");
    const includeSecrets = window.confirm(
      "Include channel PSKs/secrets in export? Click Cancel to exclude."
    );
    let deviceConfig = null;
    let deviceConfigError = null;
    try {
      const cfg = await apiFetch(
        `/api/device/config?includeSecrets=${includeSecrets ? "1" : "0"}`
      );
      if (cfg && cfg.ok) {
        deviceConfig = cfg.device || null;
      } else {
        deviceConfigError = (cfg && cfg.error) || "device config not available";
      }
    } catch (e) {
      deviceConfigError = e.message;
    }
    const exported = {
      exportedAt: new Date().toISOString(),
      apiBaseUrl: localStorage.getItem(LS.apiBaseUrl) || "",
      backend: {
        transport: h.transport,
        configured: h.configured,
        meshHost: h.meshHost,
        meshPort: h.meshPort,
        mqttHost: h.mqttHost,
        mqttPort: h.mqttPort,
        mqttUsername: h.mqttUsername,
        mqttTls: h.mqttTls,
        mqttRootTopic: h.mqttRootTopic,
        mqttPasswordSet: h.mqttPasswordSet,
      },
      device: deviceConfig,
      deviceConfigError,
      secretsIncluded: includeSecrets,
      note: "mqttPassword is not included in exports; device config may omit PSKs unless included",
    };
    const safeTransport = String(h.transport || "tcp").toLowerCase();
    const suffix = safeTransport === "mqtt" ? "mqtt" : "tcp";
    downloadJson(exported, `meshtastic-monitor-config.${suffix}.json`);
    if (deviceConfigError) {
      showToast("err", `Exported, but device config unavailable: ${deviceConfigError}`);
    } else {
      showToast("ok", "Exported configuration");
    }
  } catch (e) {
    showToast("err", `Export failed: ${e.message}`);
  }
}
async function onSend(ev) {
  ev.preventDefault();
  const textEl = $("sendText");
  const toEl = $("sendTo");
  const channelEl = $("sendChannel");
  const text = (textEl.value || "").trim();
  const to = toEl ? (toEl.value || "").trim() : "";
  const channelRaw = channelEl ? (channelEl.value || "").trim() : "";
  $("sendResult").textContent = "";
  if (!text) {
    showToast("err", "Text is required");
    return;
  }
  let channel = undefined;
  if (channelRaw) {
    const num = Number(channelRaw);
    if (!Number.isInteger(num) || num < 0) {
      showToast("err", "Channel must be a non-negative integer");
      return;
    }
    channel = num;
  }
  try {
    await apiFetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, to: to || undefined, channel }),
    });
    textEl.value = "";
    showToast("ok", "Message sent");
  } catch (e) {
    $("sendResult").textContent = e.message;
    showToast("err", `Send failed: ${e.message}`);
  }
}
async function onNodeSend(ev) {
  ev.preventDefault();
  if (!activeNodeId) return;
  const textEl = $("nodeSendText");
  const channelEl = $("nodeSendChannel");
  const text = (textEl.value || "").trim();
  const channelRaw = channelEl ? (channelEl.value || "").trim() : "";
  $("nodeSendResult").textContent = "";
  if (!text) {
    showToast("err", "Text is required");
    return;
  }
  let channel = undefined;
  if (channelRaw) {
    const num = Number(channelRaw);
    if (!Number.isInteger(num) || num < 0) {
      showToast("err", "Channel must be a non-negative integer");
      return;
    }
    channel = num;
  }
  try {
    await apiFetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, to: activeNodeId, channel }),
    });
    textEl.value = "";
    showToast("ok", `Message sent to ${activeNodeId}`);
  } catch (e) {
    $("nodeSendResult").textContent = e.message;
    showToast("err", `Send failed: ${e.message}`);
  }
}
function onNodeRowClick(ev) {
  const tr = ev.target.closest("tr[data-node-id]");
  if (!tr) return;
  const nodeId = (tr.getAttribute("data-node-id") || "").trim();
  if (!nodeId) return;
  openNodeModal(nodeId);
}
function init() {
  const savedMain = localStorage.getItem("meshmon.mainTab");
  if (savedMain) activeMainTab = savedMain === "messages" ? "channels" : savedMain;
  setMainTab(activeMainTab);
  const savedHistory = localStorage.getItem(LS.messagesHistoryLimit);
  if ($("messagesHistoryLimit")) {
    const value = savedHistory || "0";
    $("messagesHistoryLimit").value = value;
    if (!savedHistory) localStorage.setItem(LS.messagesHistoryLimit, value);
  }
  document.querySelectorAll("[data-main-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-main-tab") || "status";
      setMainTab(tab);
    });
  });
  const msgTabContainer = $("messageChannelTabs");
  if (msgTabContainer) {
    msgTabContainer.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-msg-channel]");
      if (!btn) return;
      const id = btn.getAttribute("data-msg-channel") || "all";
      setMessageChannel(id);
    });
  }
  $("btnSettings").addEventListener("click", openModal);
  $("btnCloseModal").addEventListener("click", closeModal);
  $("modalBackdrop").addEventListener("click", closeModal);
  $("btnCloseNodeModal").addEventListener("click", closeNodeModal);
  $("nodeModalBackdrop").addEventListener("click", closeNodeModal);
  $("btnSaveSettings").addEventListener("click", saveSettings);
  $("btnExportSettings").addEventListener("click", exportConfig);
  $("transportSelect").addEventListener("change", applyTransportUi);
  $("btnCopyHealthJson").addEventListener("click", copyHealthJson);
  $("btnToggleHealthJson").addEventListener("click", toggleHealthJson);
  $("sendChannel").addEventListener("change", () => {
    localStorage.setItem(LS.sendChannel, $("sendChannel").value || "");
  });
  if ($("nodeSendChannel")) {
    $("nodeSendChannel").addEventListener("change", () => {
      localStorage.setItem(LS.sendChannel, $("nodeSendChannel").value || "");
    });
  }
  $("messagesHistoryLimit").addEventListener("change", () => {
    localStorage.setItem(LS.messagesHistoryLimit, $("messagesHistoryLimit").value || "0");
    tickMessages();
  });
  $("nodesTable").addEventListener("click", onNodesHeaderClick);
  $("tabDirect").addEventListener("click", () => {
    activeNodesTab = "direct";
    $("tabDirect").classList.add("active");
    $("tabRelayed").classList.remove("active");
    if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
    applyNodesSortUi();
    tickNodes();
  });
  $("tabRelayed").addEventListener("click", () => {
    activeNodesTab = "relayed";
    $("tabRelayed").classList.add("active");
    $("tabDirect").classList.remove("active");
    if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
    applyNodesSortUi();
    tickNodes();
  });
  $("nodeFilter").addEventListener("input", () => {
    if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
  });
  $("nodesTbody").addEventListener("click", onNodeRowClick);
  $("sendForm").addEventListener("submit", onSend);
  $("nodeSendForm").addEventListener("submit", onNodeSend);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      unreadCount = 0;
      updateTitle();
    }
  });
  window.addEventListener("focus", () => {
    unreadCount = 0;
    updateTitle();
  });
  tickHealth();
  tickNodes();
  tickChannels();
  tickRadio();
  tickMessages();
  tickStats();
  if (lastNodeDetailsId) loadNodeDetails(lastNodeDetailsId);
  window.setInterval(tickHealth, 2500);
  window.setInterval(tickNodes, 5000);
  window.setInterval(tickChannels, 15000);
  window.setInterval(tickRadio, 5000);
  window.setInterval(tickMessages, 2000);
  window.setInterval(tickStats, 10000);
}
document.addEventListener("DOMContentLoaded", init);
