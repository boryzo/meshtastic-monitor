/* global window, document, localStorage, fetch */
const LS = {
  apiBaseUrl: "meshmon.apiBaseUrl",
  meshHost: "meshmon.meshHost",
  meshPort: "meshmon.meshPort",
  smsEnabled: "meshmon.smsEnabled",
  smsApiUrl: "meshmon.smsApiUrl",
  smsPhone: "meshmon.smsPhone",
  smsAllowFromIds: "meshmon.smsAllowFromIds",
  smsAllowTypes: "meshmon.smsAllowTypes",
  relayEnabled: "meshmon.relayEnabled",
  relayHost: "meshmon.relayHost",
  relayPort: "meshmon.relayPort",
  statsCacheMinutes: "meshmon.statsCacheMinutes",
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
function fmtDuration(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined) return "—";
  let s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  const parts = [];
  if (h > 0) parts.push(`${h}h`);
  if (h > 0 || m > 0) parts.push(`${m}min`);
  if (s > 0 || (!h && !m)) parts.push(`${s}sec`);
  return parts.join("");
}
function fmtTime(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtMessageTime(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (sameDay) return time;
  const date = d.toLocaleDateString();
  return `${date} ${time}`;
}
let activeNodesTab = "direct";
let lastMessagesKey = "";
let lastStatus = null;
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
const STATUS_REPORT_GRACE_SEC = 60;
let statusFirstSeenAt = null;
let lastReportOkAt = null;
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
    filtered.map((m) => [
      m.rxTime,
      m.fromId,
      m.toId,
      m.fromShort,
      m.fromLong,
      m.toShort,
      m.toLong,
      m.text,
      m.portnum,
      m.channel,
      m.app,
      m.requestId,
      m.wantResponse,
    ]),
  ]);
  if (key === lastMessagesKey) return;
  lastMessagesKey = key;
  if (!filtered || filtered.length === 0) {
    list.innerHTML = `<div class="muted">No messages yet</div>`;
    return;
  }
  const rows = [];
  for (const m of filtered) {
    const from = messageNodeLabel(m, "from");
    const to = messageNodeLabel(m, "to");
    const snr = m.snr === null || m.snr === undefined ? "—" : String(m.snr);
    const rssi = m.rssi === null || m.rssi === undefined ? "—" : String(m.rssi);
    const chNum = m.channel === null || m.channel === undefined ? null : Number(m.channel);
    const showChannel = selected === "all" && chNum !== null && !Number.isNaN(chNum);
    const chLabel = showChannel ? channelInfo(chNum).label : "";
    const text = m.text ? escapeHtml(m.text) : `<span class="muted">port ${escapeHtml(String(m.portnum ?? "—"))}</span>`;
    const app = appNameForMessage(m);
    const isRequest = isAppRequestForMe(m, app);
    const appBadge = app && isRequest ? `<span class="pill ok">Request: ${escapeHtml(app)}</span>` : "";
    const direction = localRadioId && m.fromId === localRadioId ? "outgoing" : "incoming";
    const channelBadge = showChannel ? `<span class="pill">${escapeHtml(chLabel)}</span>` : "";
    rows.push(`
      <div class="msg ${direction}">
        <div class="meta">
          <span>${escapeHtml(fmtMessageTime(m.rxTime))}</span>
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
function nodeLabelHtml(node) {
  const short = (node && node.short ? String(node.short).trim() : "") || "";
  const long = (node && node.long ? String(node.long).trim() : "") || "";
  const id = (node && node.id ? String(node.id).trim() : "") || "";
  const primary = short || long || id || "—";
  const secondaryParts = [];
  if (short && long && long !== short) secondaryParts.push(long);
  if (id && id !== primary) secondaryParts.push(id);
  const secondary = secondaryParts.length
    ? `<div class="muted mono">${escapeHtml(secondaryParts.join(" • "))}</div>`
    : "";
  return `<div>${escapeHtml(primary)}</div>${secondary}`;
}
async function tickStatus() {
  try {
    const h = await apiFetch("/api/status");
    lastStatus = h;
    if (h.meshHost) {
      $("meshHost").textContent = `${h.meshHost}:${h.meshPort}`;
    } else {
      $("meshHost").textContent = "Not configured";
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
    renderStatus(h);
  } catch (e) {
    $("meshHost").textContent = "—";
    setConnStatus(false);
    renderStatusError(e);
  }
}
function renderStatus(h) {
  const nowSec = Math.floor(Date.now() / 1000);
  if (statusFirstSeenAt === null) statusFirstSeenAt = nowSec;
  const statusText =
    h.configured === false ? "Not configured" : h.connected ? "Connected" : "Disconnected";
  const overview = [];
  overview.push(kv("Connection", statusText));
  overview.push(kv("Mesh", h.meshHost ? `${h.meshHost}:${h.meshPort}` : "—"));
  overview.push(kv("Report", h.reportOk ? (h.reportStatus || "ok") : "unavailable"));
  if (h.reportUrl) overview.push(kvLink("JSON", h.reportUrl, "/json/report"));
  if (h.lastError) overview.push(kv("Last error", String(h.lastError), true));
  $("statusOverview").innerHTML = overview.join("");

  if (h.reportOk && h.report) {
    lastReportOkAt = nowSec;
  }
  const graceStart = lastReportOkAt ?? statusFirstSeenAt ?? nowSec;
  const inGrace = nowSec - graceStart < STATUS_REPORT_GRACE_SEC;

  const updated = h.reportFetchedAt
    ? new Date(h.reportFetchedAt * 1000).toLocaleTimeString()
    : "—";
  $("statusMeta").textContent = `updated ${updated}`;
  $("statusError").textContent = h.reportOk
    ? ""
    : inGrace
      ? "Waiting for report…"
      : h.reportError
        ? `Report error: ${h.reportError}`
        : "Report unavailable";

  if (!h.report) {
    ["statusPower", "statusMemory", "statusAirtime", "statusRadio", "statusWifi"].forEach(
      (id) => {
        const el = $(id);
        if (el) el.innerHTML = `<div class="muted">No report yet</div>`;
      }
    );
    $("healthJson").textContent = JSON.stringify(h, null, 2);
    return;
  }

  const airtime = h.report.airtime || {};
  const power = h.report.power || {};
  const memory = h.report.memory || {};
  const wifi = h.report.wifi || {};
  const radio = h.report.radio || {};
  const device = h.report.device || {};

  $("statusPower").innerHTML = [
    kv("Battery", fmtPercent(power.battery_percent, 0)),
    kv("Voltage", fmtVoltageMv(power.battery_voltage_mv)),
    kv("Charging", fmtBoolish(power.is_charging)),
    kv("USB", fmtBoolish(power.has_usb)),
    kv("Has battery", fmtBoolish(power.has_battery)),
  ].join("");

  $("statusMemory").innerHTML = [
    kv("Heap", fmtBytesPair(memory.heap_free, memory.heap_total)),
    kv("FS", fmtBytesPair(memory.fs_free, memory.fs_total)),
    kv("PSRAM", fmtBytesPair(memory.psram_free, memory.psram_total)),
  ].join("");

  $("statusAirtime").innerHTML = [
    kv("Channel util", fmtPercent(airtime.channel_utilization, 1)),
    kv("Util TX", fmtPercent(airtime.utilization_tx, 2)),
    kv("RX log", fmtCount(firstListVal(airtime.rx_log))),
    kv("TX log", fmtCount(firstListVal(airtime.tx_log))),
    kv("RX all", fmtCount(firstListVal(airtime.rx_all_log))),
    kv("Uptime", fmtAge(airtime.seconds_since_boot)),
  ].join("");

  $("statusRadio").innerHTML = [
    kv("Frequency", fmtNum(radio.frequency, 3)),
    kv("LoRa channel", radio.lora_channel ?? "—"),
    kv("Reboots", fmtCount(device.reboot_counter)),
  ].join("");

  $("statusWifi").innerHTML = [
    kv("IP", wifi.ip || "—"),
    kv("RSSI", wifi.rssi ?? "—"),
  ].join("");

  $("healthJson").textContent = JSON.stringify(h, null, 2);
}
function renderStatusError(e) {
  ["statusOverview", "statusPower", "statusMemory", "statusAirtime", "statusRadio", "statusWifi"].forEach(
    (id) => {
      const el = $(id);
      if (el) el.innerHTML = `<div class="muted">Failed to load</div>`;
    }
  );
  $("statusError").textContent = e.message ? `Failed to load: ${e.message}` : "Failed to load";
  $("statusMeta").textContent = "—";
  $("healthJson").textContent = "";
}
function kv(key, value, isErr = false) {
  return `<div class="k">${escapeHtml(key)}</div><div class="v${isErr ? " err" : ""}">${escapeHtml(value)}</div>`;
}
function kvLink(key, href, label) {
  const safeHref = escapeHtml(href || "");
  const safeLabel = escapeHtml(label || href || "—");
  return `<div class="k">${escapeHtml(key)}</div><div class="v"><a href="${safeHref}" target="_blank" rel="noreferrer">${safeLabel}</a></div>`;
}
function fmtNum(val, digits = 2) {
  if (val === null || val === undefined) return "—";
  const n = Number(val);
  if (Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}
function fmtPercent(val, digits = 1) {
  if (val === null || val === undefined) return "—";
  const n = Number(val);
  if (Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}
function fmtVoltageMv(val) {
  if (val === null || val === undefined) return "—";
  const n = Number(val);
  if (Number.isNaN(n)) return "—";
  return `${(n / 1000).toFixed(2)} V`;
}
function fmtBytes(val) {
  if (val === null || val === undefined) return "—";
  const n = Number(val);
  if (Number.isNaN(n)) return "—";
  if (n < 1024) return `${n.toFixed(0)} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(2)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(2)} GB`;
}
function fmtBytesPair(free, total) {
  const f = fmtBytes(free);
  const t = fmtBytes(total);
  if (f === "—" && t === "—") return "—";
  if (t === "—") return f;
  if (f === "—") return `— / ${t}`;
  return `${f} / ${t}`;
}
function fmtBool(val) {
  if (val === true) return "true";
  if (val === false) return "false";
  return "—";
}
function fmtBoolish(val) {
  if (val === true || val === false) return fmtBool(val);
  if (typeof val === "string") {
    const v = val.trim().toLowerCase();
    if (["1", "true", "yes", "y", "on"].includes(v)) return "true";
    if (["0", "false", "no", "n", "off"].includes(v)) return "false";
  }
  if (typeof val === "number") {
    if (Number.isNaN(val)) return "—";
    return val === 0 ? "false" : "true";
  }
  return "—";
}
function firstListVal(val) {
  if (!Array.isArray(val) || val.length === 0) return null;
  return val[0];
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
function getNodeNameById(nodeId) {
  if (!nodeId || !lastNodes) return {};
  const list = []
    .concat(lastNodes.direct || [])
    .concat(lastNodes.relayed || []);
  const match = list.find((n) => String(n.id || "") === String(nodeId));
  return match ? { short: match.short, long: match.long } : {};
}
function formatNodeLabel(shortName, longName, fallbackId) {
  const short = shortName ? String(shortName).trim() : "";
  const long = longName ? String(longName).trim() : "";
  if (short && long && long !== short) return `${short} - ${long}`;
  if (short) return short;
  if (long) return long;
  return fallbackId || "—";
}
function messageNodeLabel(m, side) {
  const id = side === "to" ? m.toId : m.fromId;
  const shortKey = side === "to" ? "toShort" : "fromShort";
  const longKey = side === "to" ? "toLong" : "fromLong";
  let shortName = m[shortKey];
  let longName = m[longKey];
  if (!shortName && !longName) {
    const lookup = getNodeNameById(id);
    shortName = lookup.short;
    longName = lookup.long;
  }
  return formatNodeLabel(shortName, longName, id || "—");
}
async function tickNodes() {
  try {
    const n = await apiFetch("/api/nodes");
    lastNodes = n;
    recordNodesHistory(n, n.generatedAt);
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
  const rows = filtered.map((m) => {
    const from = messageNodeLabel(m, "from");
    const to = messageNodeLabel(m, "to");
    const snr = m.snr === null || m.snr === undefined ? "—" : String(m.snr);
    const rssi = m.rssi === null || m.rssi === undefined ? "—" : String(m.rssi);
    const text = m.text
      ? escapeHtml(m.text)
      : `<span class="muted">port ${escapeHtml(String(m.portnum ?? "—"))}</span>`;
    const direction = localRadioId && m.fromId === localRadioId ? "outgoing" : "incoming";
    return `
      <div class="msg ${direction}">
        <div class="meta">
          <span>${escapeHtml(fmtMessageTime(m.rxTime))}</span>
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
function recordNodesHistory(nodes, generatedAt) {
  const t = typeof generatedAt === "number" ? generatedAt : Math.floor(Date.now() / 1000);
  const total = Number(nodes?.total);
  const meshCount = Number(nodes?.meshCount ?? nodes?.total);
  if (!Number.isFinite(meshCount)) return;
  const observedAdded = Number(nodes?.observedAdded || 0);
  const observedCount = Number(nodes?.observedCount || 0);
  const entry = {
    ts: t,
    total: Number.isFinite(total) ? total : meshCount,
    meshCount,
    observedAdded: Number.isFinite(observedAdded) ? observedAdded : 0,
    observedCount: Number.isFinite(observedCount) ? observedCount : 0,
  };
  const last = nodesHistory.length ? nodesHistory[nodesHistory.length - 1] : null;
  if (last && last.ts === t) {
    Object.assign(last, entry);
  } else {
    nodesHistory.push(entry);
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
  const points = nodesHistory.map((p) => {
    const count = Number(p.meshCount ?? p.total) || 0;
    const label = new Date(p.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const observedAdded = Number(p.observedAdded || 0);
    const title = observedAdded > 0 ? `${label} • ${count} mesh (+${observedAdded} observed)` : `${label} • ${count} mesh`;
    return { ts: p.ts, value: count, title };
  });
  renderLineChartWithLabels(el, points, 6, { height: 280 });
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
  if (mapped === "settings") {
    loadSettingsPanel();
  }
}
function updateMessageChannelTabs(channels) {
  const container = $("messageChannelTabs");
  if (!container) return;
  const observed = getObservedChannelIds(lastMessages);
  const items = computeMessageChannelTabItems(
    observed,
    channelsByIndex,
    formatChannelIndexLabel,
    formatChannelHashLabel
  );
  container.innerHTML = items
    .map(
      (it) =>
        `<button class="tab" data-msg-channel="${escapeHtml(it.id)}">${escapeHtml(it.label)}</button>`
    )
    .join("");
  const ids = new Set(items.map((it) => String(it.id)));
  if (!ids.has(String(activeMessageChannel || "all"))) {
    activeMessageChannel = "all";
    if (lastMessages) renderMessages(lastMessages);
  }
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
    [
      "statsHourly",
      "statsNodesVisible",
      "statsRelay",
      "statsApps",
      "statsAppRequests",
      "statsRequesters",
      "statsTopFrom",
      "statsTopTo",
      "statsMostVisible",
      "statsZeroHop",
      "statsSnr",
      "statsFlaky",
      "statsEvents",
    ].forEach(
      (id) => {
        const el = $(id);
        if (el) el.innerHTML = "";
      }
    );
  }
}

async function tickRelay() {
  const el = $("statsRelay");
  if (!el) return;
  try {
    const data = await apiFetch("/api/relay");
    renderRelayStats(data);
  } catch (e) {
    el.innerHTML = `<div class="muted">Relay unavailable</div>`;
  }
}

async function tickDiag() {
  try {
    const data = await apiFetch("/api/diag?limit=50");
    const items = Array.isArray(data.items) ? data.items : [];
    const updated = data.generatedAt
      ? new Date(data.generatedAt * 1000).toLocaleTimeString()
      : "—";
    $("diagMeta").textContent = `${items.length} packets • updated ${updated}`;
    renderList(
      "diagList",
      items,
      (d) => {
        const from = d.fromId || "—";
        const to = d.toId || "—";
        const ts = d.ts ? fmtTime(d.ts) : "—";
        const port = d.portnum !== null && d.portnum !== undefined ? d.portnum : "—";
        const decoded = d.decoded ? "decoded" : "no-decoded";
        const ch =
          d.chIndex !== null && d.chIndex !== undefined
            ? `ch ${d.chIndex}`
            : d.chHash
              ? `ch ${d.chHash}`
              : "ch —";
        const enc =
          d.encrypted === true ? "enc" : d.encrypted === false ? "open" : "enc ?";
        const pLen =
          d.payloadLen !== null && d.payloadLen !== undefined ? `payload ${d.payloadLen}` : "";
        const pUtf =
          d.payloadUtf8 === true ? "utf8" : d.payloadUtf8 === false ? "bin" : "";
        const preview = d.textPreview ? `txt "${d.textPreview}"` : "";
        const meta = [decoded, `port ${port}`, ch, enc, pLen, pUtf, preview]
          .filter((v) => v && String(v).trim() !== "")
          .join(" • ");
        return `<div class="list-row">
          <div class="mono">${escapeHtml(ts)} ${escapeHtml(from)} → ${escapeHtml(to)}</div>
          <div class="mono">${escapeHtml(String(port))}</div>
          <div class="muted">${escapeHtml(meta)}</div>
        </div>`;
      },
      "No diagnostics yet"
    );
  } catch (e) {
    $("diagMeta").textContent = `Failed to load diagnostics: ${e.message}`;
    $("diagList").innerHTML = `<div class="muted">Diagnostics unavailable</div>`;
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
function _timeParts(ts) {
  if (!ts) return { day: "—", time: "—" };
  const d = new Date(ts * 1000);
  return {
    day: d.toLocaleDateString([], { month: "2-digit", day: "2-digit" }),
    time: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  };
}
function buildTimeLabels(points, maxLabels = 6) {
  if (!Array.isArray(points) || points.length === 0) return "";
  const every = Math.max(1, Math.floor(points.length / maxLabels));
  let lastDay = null;
  return points
    .map((p, idx) => {
      const isEdge = idx === 0 || idx === points.length - 1;
      const show = isEdge || idx % every === 0;
      if (!show) return `<div class="bar-label empty"></div>`;
      const parts = _timeParts(p.ts);
      const showDay = lastDay !== parts.day;
      lastDay = parts.day;
      const dayHtml = showDay ? `<div class="day">${escapeHtml(parts.day)}</div>` : "";
      const timeHtml = `<div class="time">${escapeHtml(parts.time)}</div>`;
      return `<div class="bar-label">${dayHtml}${timeHtml}</div>`;
    })
    .join("");
}
function buildLineChartSvg(points, opts = {}) {
  const height = Number(opts.height) || 280;
  const pad = Number(opts.pad) || 12;
  if (!Array.isArray(points) || points.length === 0) {
    return `<div class="muted">No data</div>`;
  }
  const values = points.map((p) => p.value).filter((v) => Number.isFinite(v));
  if (!values.length) {
    return `<div class="muted">No data</div>`;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = Math.max(1, points.length - 1);
  const usableH = Math.max(1, height - pad * 2);
  const coords = points.map((p, idx) => {
    const value = Number(p.value);
    const norm = Number.isFinite(value) ? (value - min) / span : 0;
    const x = points.length === 1 ? width / 2 : (idx / (points.length - 1)) * width;
    const y = pad + (1 - norm) * usableH;
    return { x, y, value, title: p.title || "" };
  });
  const linePoints = coords.map((p) => `${p.x},${p.y}`).join(" ");
  const areaPath = `M ${coords[0].x} ${coords[0].y} L ${coords
    .map((p) => `${p.x} ${p.y}`)
    .join(" L ")} L ${coords[coords.length - 1].x} ${height - pad} L ${coords[0].x} ${height - pad} Z`;
  const circles = coords
    .map(
      (p) =>
        `<circle class="line-point" cx="${p.x}" cy="${p.y}" r="2"><title>${escapeHtml(
          p.title || ""
        )}</title></circle>`
    )
    .join("");
  return `<svg class="line-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img">
    <path class="line-area" d="${areaPath}"></path>
    <polyline class="line-path" points="${linePoints}"></polyline>
    ${circles}
  </svg>`;
}
function renderLineChartWithLabels(el, points, maxLabels = 6, opts = {}) {
  if (!el) return;
  if (!Array.isArray(points) || points.length === 0) {
    el.innerHTML = `<div class="muted">No data</div>`;
    return;
  }
  const svg = buildLineChartSvg(points, opts);
  const labels = buildTimeLabels(points, maxLabels);
  el.innerHTML = `<div class="line-chart-wrap">${svg}</div><div class="bar-labels">${labels}</div>`;
}
function renderBars(hourly) {
  const el = $("statsHourly");
  if (!el) return;
  if (!Array.isArray(hourly) || hourly.length === 0) {
    el.innerHTML = `<div class="muted">No data</div>`;
    return;
  }
  const points = hourly.map((h) => {
    const count = Number(h.messages) || 0;
    const withText = Number(h.with_text ?? h.withText ?? h.with_text) || 0;
    const withPayload = Number(h.with_payload ?? h.withPayload ?? h.with_payload) || 0;
    const label = new Date((Number(h.hour) || 0) * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const title = `${label} • ${count} msgs (${withText} text, ${withPayload} payload)`;
    return { ts: Number(h.hour) || 0, value: count, title };
  });
  renderLineChartWithLabels(el, points, 8, { height: 280 });
}
function renderSeriesBars(elId, series, valueKey, formatLabel) {
  const el = $(elId);
  if (!el) return;
  if (!Array.isArray(series) || series.length === 0) {
    el.innerHTML = `<div class="muted">No data</div>`;
    return;
  }
  const points = series
    .map((s) => {
      const v = Number(s[valueKey]);
      const value = Number.isFinite(v) ? v : null;
      return { ts: s.ts, value, title: value === null ? "" : formatLabel(value) };
    })
    .filter((p) => p.value !== null);
  if (!points.length) {
    el.innerHTML = `<div class="muted">No data</div>`;
    return;
  }
  renderLineChartWithLabels(el, points, 8, { height: 280 });
}
function renderStatusSummary(latest) {
  const el = $("statsStatusSummary");
  if (!el) return;
  if (!latest) {
    el.innerHTML = `<div class="muted">No status data yet</div>`;
    return;
  }
  const rows = [];
  rows.push(kv("Battery", fmtPercent(latest.batteryPercent, 0)));
  rows.push(kv("Channel util", fmtPercent(latest.channelUtilization, 1)));
  rows.push(kv("Util TX", fmtPercent(latest.utilizationTx, 2)));
  rows.push(kv("WiFi RSSI", latest.wifiRssi ?? "—"));
  rows.push(kv("Heap free", fmtBytes(latest.heapFree)));
  rows.push(kv("FS free", fmtBytes(latest.fsFree)));
  el.innerHTML = rows.join("");
}
function renderRelayStats(relay) {
  const el = $("statsRelay");
  if (!el) return;
  if (!relay || relay.enabled !== true) {
    el.innerHTML = `<div class="muted">Relay disabled</div>`;
    return;
  }
  const listen = relay.listenHost && relay.listenPort ? `${relay.listenHost}:${relay.listenPort}` : "—";
  const upstream = relay.upstreamHost && relay.upstreamPort ? `${relay.upstreamHost}:${relay.upstreamPort}` : "—";
  const upstreamState = relay.upstreamConnected ? "connected" : "disconnected";
  const summary = `
    <div class="list-row">
      <div>Listening</div>
      <div class="mono">${escapeHtml(listen)}</div>
    </div>
    <div class="list-row">
      <div>Upstream</div>
      <div class="mono">${escapeHtml(upstream)} • ${escapeHtml(upstreamState)}</div>
    </div>
    <div class="list-row">
      <div>Clients</div>
      <div class="mono">${fmtCount(relay.clientCount)}</div>
    </div>
  `;
  const clients = Array.isArray(relay.clients) ? relay.clients : [];
  if (!clients.length) {
    el.innerHTML = `${summary}<div class="muted">No clients connected</div>`;
    return;
  }
  const rows = clients.map((c) => {
    const addr = `${c.addr || "—"}:${c.port || "—"}`;
    const connectedAt = c.connectedAt ? fmtTime(c.connectedAt) : "—";
    const lastSeen = c.lastSeen ? fmtTime(c.lastSeen) : "—";
    return `<div class="list-row">
      <div class="mono">${escapeHtml(addr)}</div>
      <div class="muted">conn ${escapeHtml(connectedAt)} • last ${escapeHtml(lastSeen)}</div>
    </div>`;
  });
  el.innerHTML = summary + rows.join("");
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
    [
      "statsHourly",
      "statsNodesVisible",
      "statsRelay",
      "statsApps",
      "statsAppRequests",
      "statsRequesters",
      "statsTopFrom",
      "statsTopTo",
      "statsMostVisible",
      "statsZeroHop",
      "statsSnr",
      "statsFlaky",
      "statsEvents",
      "statsStatusSummary",
      "statsBattery",
      "statsChannelUtil",
      "statsWifiRssi",
    ].forEach(
      (id) => {
        const el = $(id);
        if (el) el.innerHTML = "";
      }
    );
    return;
  }
  const statusKey = data.status && data.status.latest ? data.status.latest.ts : null;
  const key = JSON.stringify([
    data.generatedAt,
    data.counters,
    data.messages,
    data.apps,
    data.nodes,
    data.events,
    statusKey,
  ]);
  if (key === lastStatsKey) return;
  lastStatsKey = key;
  const counters = data.counters || {};
  const messages = data.messages || {};
  const apps = data.apps || {};
  const nodes = data.nodes || {};
  const events = data.events || [];
  const status = data.status || {};
  const updated = data.generatedAt
    ? new Date(data.generatedAt * 1000).toLocaleTimeString()
    : "—";
  const windowHours = messages.windowHours || data.windowHours || "—";
  $("statsMeta").textContent = `db ${data.dbPath || "—"} • window ${windowHours}h • updated ${updated}`;
  const liveNodes =
    lastNodes && Number.isFinite(Number(lastNodes.meshCount))
      ? Number(lastNodes.meshCount)
      : lastNodes
        ? Number(lastNodes.total)
        : null;
  const observedAdded = lastNodes ? Number(lastNodes.observedAdded || 0) : 0;
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
      <div class="value">${fmtNumCompact(liveNodes)}${observedAdded > 0 ? ` <span class="muted">+${fmtNumCompact(observedAdded)}</span>` : ""}</div>
    </div>
  `;
  $("statsSummary").innerHTML = summary;
  renderNodesVisibleHistory();
  renderBars(messages.hourlyWindow || []);
  renderStatusSummary(status.latest);
  renderSeriesBars("statsBattery", status.series || [], "batteryPercent", (v) => `${v.toFixed(0)}%`);
  renderSeriesBars("statsChannelUtil", status.series || [], "channelUtilization", (v) => `${v.toFixed(1)}%`);
  renderSeriesBars("statsWifiRssi", status.series || [], "wifiRssi", (v) => `${v.toFixed(0)}`);
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
    "statsRequesters",
    apps.requesters || [],
    (r) => {
      const last = r.lastTs ? fmtTime(r.lastTs) : "—";
      return `<div class="list-row">
        <div>${nodeLabelHtml(r)}</div>
        <div>${fmtCount(r.count)} req</div>
        <div class="muted">${escapeHtml(last)}</div>
      </div>`;
    },
    "No requesters recorded"
  );
  renderList(
    "statsTopFrom",
    nodes.topFrom || [],
    (n) =>
      `<div class="list-row">
        <div>${nodeLabelHtml(n)}</div>
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
        <div>${nodeLabelHtml(n)}</div>
        <div>${fmtCount(n.count)} msgs</div>
        <div class="muted">${n.lastRssi !== null && n.lastRssi !== undefined ? `RSSI ${n.lastRssi}` : ""}</div>
      </div>`,
    "No outgoing yet"
  );
  renderList(
    "statsMostVisible",
    nodes.mostVisible || [],
    (n) => {
      const seconds = n.seconds ?? null;
      const timeLabel = seconds ? fmtDuration(seconds) : "—";
      const pct =
        n.availabilityPct !== null && n.availabilityPct !== undefined
          ? `${n.availabilityPct}%`
          : null;
      return `<div class="list-row">
        <div>${nodeLabelHtml(n)}</div>
        <div>${escapeHtml(timeLabel)}</div>
        <div class="muted">${escapeHtml(pct || "—")}</div>
      </div>`;
    },
    "No visibility history"
  );
  renderList(
    "statsZeroHop",
    nodes.zeroHop || [],
    (n) => {
      const seconds = n.seconds ?? null;
      const timeLabel = seconds ? fmtDuration(seconds) : "—";
      return `<div class="list-row">
        <div>${nodeLabelHtml(n)}</div>
        <div>${escapeHtml(timeLabel)}</div>
        <div class="muted">—</div>
      </div>`;
    },
    "No zero-hop history"
  );
  renderList(
    "statsSnr",
    nodes.snrStats || [],
    (n) => {
      const fmt = (val) =>
        val === null || val === undefined ? "—" : Number(val).toFixed(1);
      return `<div class="list-row">
        <div>${nodeLabelHtml(n)}</div>
        <div>${fmtCount(n.samples)} samples</div>
        <div class="muted">min ${fmt(n.minSnr)} • avg ${fmt(n.avgSnr)} • max ${fmt(n.maxSnr)}</div>
      </div>`;
    },
    "No SNR history"
  );
  renderList(
    "statsFlaky",
    nodes.flaky || [],
    (n) => {
      return `<div class="list-row">
        <div>${nodeLabelHtml(n)}</div>
        <div>${fmtCount(n.hopChanges)} changes</div>
      </div>`;
    },
    "No hop changes"
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
function setSmsKeyHint(apiKeySet) {
  const hint = $("smsKeyHint");
  if (!hint) return;
  const text = apiKeySet
    ? "API key is saved. Leave blank to keep it."
    : "Leave blank to keep existing key.";
  hint.setAttribute("title", text);
  hint.setAttribute("aria-label", text);
}
async function loadSettingsPanel() {
  $("apiBaseUrl").value = localStorage.getItem(LS.apiBaseUrl) || "";
  $("meshHostInput").value = localStorage.getItem(LS.meshHost) || "";
  $("meshPortInput").value = localStorage.getItem(LS.meshPort) || "4403";
  $("relayEnabled").checked = (localStorage.getItem(LS.relayEnabled) || "").trim() === "1";
  $("relayHost").value = localStorage.getItem(LS.relayHost) || "0.0.0.0";
  $("relayPort").value = localStorage.getItem(LS.relayPort) || "4403";
  $("smsEnabled").checked = (localStorage.getItem(LS.smsEnabled) || "").trim() === "1";
  $("smsApiUrl").value = localStorage.getItem(LS.smsApiUrl) || "";
  $("smsPhone").value = localStorage.getItem(LS.smsPhone) || "";
  $("smsAllowFromIds").value = localStorage.getItem(LS.smsAllowFromIds) || "";
  $("smsAllowTypes").value = localStorage.getItem(LS.smsAllowTypes) || "";
  $("statsCacheMinutes").value = localStorage.getItem(LS.statsCacheMinutes) || "30";
  $("smsApiKey").value = "";
  setSmsKeyHint(false);
  try {
    const cfg = await apiFetch("/api/config");
    if (cfg && cfg.meshHost) {
      $("meshHostInput").value = cfg.meshHost;
      localStorage.setItem(LS.meshHost, cfg.meshHost);
    }
    if (cfg && cfg.meshPort) {
      $("meshPortInput").value = String(cfg.meshPort);
      localStorage.setItem(LS.meshPort, String(cfg.meshPort));
    }
    if (cfg && cfg.sms) {
      const sms = cfg.sms;
      const enabled =
        sms.enabled === true ||
        String(sms.enabled || "").toLowerCase() === "true" ||
        String(sms.enabled || "").toLowerCase() === "1";
      $("smsEnabled").checked = enabled;
      localStorage.setItem(LS.smsEnabled, enabled ? "1" : "0");
      $("smsApiUrl").value = sms.apiUrl || "";
      $("smsPhone").value = sms.phone || "";
      localStorage.setItem(LS.smsApiUrl, sms.apiUrl || "");
      localStorage.setItem(LS.smsPhone, sms.phone || "");
      $("smsAllowFromIds").value = sms.allowFromIds || "ALL";
      $("smsAllowTypes").value = sms.allowTypes || "ALL";
      localStorage.setItem(LS.smsAllowFromIds, sms.allowFromIds || "ALL");
      localStorage.setItem(LS.smsAllowTypes, sms.allowTypes || "ALL");
      setSmsKeyHint(Boolean(sms.apiKeySet));
    }
    if (cfg && cfg.relay) {
      const relay = cfg.relay;
      const enabled =
        relay.enabled === true ||
        String(relay.enabled || "").toLowerCase() === "true" ||
        String(relay.enabled || "").toLowerCase() === "1";
      $("relayEnabled").checked = enabled;
      localStorage.setItem(LS.relayEnabled, enabled ? "1" : "0");
      if (relay.listenHost) {
        $("relayHost").value = relay.listenHost;
        localStorage.setItem(LS.relayHost, relay.listenHost);
      }
      if (relay.listenPort) {
        $("relayPort").value = String(relay.listenPort);
        localStorage.setItem(LS.relayPort, String(relay.listenPort));
      }
    }
    if (cfg && cfg.stats && cfg.stats.cacheMinutes) {
      $("statsCacheMinutes").value = String(cfg.stats.cacheMinutes);
      localStorage.setItem(LS.statsCacheMinutes, String(cfg.stats.cacheMinutes));
    }
  } catch {
    // ignore config fetch errors
  }
}
async function saveSettings() {
  const apiBaseUrl = ($("apiBaseUrl").value || "").trim();
  const meshHost = ($("meshHostInput").value || "").trim();
  const meshPort = ($("meshPortInput").value || "").trim();
  const relayEnabled = $("relayEnabled").checked;
  const relayHost = ($("relayHost").value || "").trim() || "0.0.0.0";
  const relayPort = ($("relayPort").value || "").trim() || "4403";
  const smsEnabled = $("smsEnabled").checked;
  const smsApiUrl = ($("smsApiUrl").value || "").trim();
  const smsApiKey = ($("smsApiKey").value || "").trim();
  const smsPhone = ($("smsPhone").value || "").trim();
  const smsAllowFromIds = ($("smsAllowFromIds").value || "").trim();
  const smsAllowTypes = ($("smsAllowTypes").value || "").trim();
  const statsCacheMinutes = ($("statsCacheMinutes").value || "").trim();
  localStorage.setItem(LS.apiBaseUrl, apiBaseUrl);
  localStorage.setItem(LS.meshHost, meshHost);
  localStorage.setItem(LS.meshPort, meshPort);
  localStorage.setItem(LS.relayEnabled, relayEnabled ? "1" : "0");
  localStorage.setItem(LS.relayHost, relayHost);
  localStorage.setItem(LS.relayPort, relayPort);
  localStorage.setItem(LS.smsEnabled, smsEnabled ? "1" : "0");
  localStorage.setItem(LS.smsApiUrl, smsApiUrl);
  localStorage.setItem(LS.smsPhone, smsPhone);
  localStorage.setItem(LS.smsAllowFromIds, smsAllowFromIds);
  localStorage.setItem(LS.smsAllowTypes, smsAllowTypes);
  localStorage.setItem(LS.statsCacheMinutes, statsCacheMinutes);
  if (!meshHost) {
    showToast("err", "Meshtastic host is required");
    return;
  }
  if (relayPort && !Number.isFinite(Number(relayPort))) {
    showToast("err", "Relay port must be a number");
    return;
  }
  if (statsCacheMinutes && !Number.isFinite(Number(statsCacheMinutes))) {
    showToast("err", "Stats cache minutes must be a number");
    return;
  }
  if (statsCacheMinutes && Number(statsCacheMinutes) < 1) {
    showToast("err", "Stats cache minutes must be >= 1");
    return;
  }
  const body = {};
  if (meshHost) body.meshHost = meshHost;
  if (meshPort) body.meshPort = Number(meshPort || "4403");
  if (relayHost) body.relayHost = relayHost;
  if (relayPort) body.relayPort = Number(relayPort || "4403");
  body.relayEnabled = relayEnabled;
  body.smsEnabled = smsEnabled;
  body.smsApiUrl = smsApiUrl;
  body.smsPhone = smsPhone;
  body.smsAllowFromIds = smsAllowFromIds;
  body.smsAllowTypes = smsAllowTypes;
  if (statsCacheMinutes) body.statsCacheMinutes = Number(statsCacheMinutes);
  if (smsApiKey) body.smsApiKey = smsApiKey;
  try {
    await apiFetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("smsApiKey").value = "";
    if (smsApiKey) {
      setSmsKeyHint(true);
    }
    showToast("ok", `Applied TCP: ${meshHost}:${meshPort || "4403"}`);
    await tickStatus();
    await tickNodes();
  } catch (e) {
    showToast("err", `Failed to apply settings: ${e.message}`);
  }
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
  if (!lastStatus) {
    showToast("err", "Status not loaded yet");
    return;
  }
  const text = JSON.stringify(lastStatus, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    showToast("ok", "Copied status JSON");
  } catch {
    // Fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    showToast("ok", "Copied status JSON");
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
    let runtimeConfig = null;
    let runtimeConfigError = null;
    try {
      runtimeConfig = await apiFetch("/api/config");
    } catch (e) {
      runtimeConfigError = e.message;
    }
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
        configured: h.configured,
        meshHost: h.meshHost,
        meshPort: h.meshPort,
      },
      relay: runtimeConfig ? runtimeConfig.relay || null : null,
      sms: runtimeConfig ? runtimeConfig.sms || null : null,
      configPath: runtimeConfig ? runtimeConfig.configPath || null : null,
      configError: runtimeConfigError,
      device: deviceConfig,
      deviceConfigError,
      secretsIncluded: includeSecrets,
      note: "device config may omit PSKs unless included",
    };
    downloadJson(exported, "meshtastic-monitor-config.tcp.json");
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
  $("btnCloseNodeModal").addEventListener("click", closeNodeModal);
  $("nodeModalBackdrop").addEventListener("click", closeNodeModal);
  $("btnSaveSettings").addEventListener("click", saveSettings);
  $("btnExportSettings").addEventListener("click", exportConfig);
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
  tickStatus();
  tickNodes();
  tickChannels();
  tickRadio();
  tickMessages();
  tickStats();
  tickDiag();
  tickRelay();
  if (lastNodeDetailsId) loadNodeDetails(lastNodeDetailsId);
  window.setInterval(tickStatus, 2500);
  window.setInterval(tickNodes, 5000);
  window.setInterval(tickChannels, 15000);
  window.setInterval(tickRadio, 5000);
  window.setInterval(tickMessages, 2000);
  window.setInterval(tickStats, 10000);
  window.setInterval(tickDiag, 5000);
  window.setInterval(tickRelay, 5000);
}
document.addEventListener("DOMContentLoaded", init);
