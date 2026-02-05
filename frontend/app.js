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

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted">No nodes</td></tr>`;
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
    rows.push(`
      <tr class="node-row" ${nodeId ? `data-node-id="${escapeHtml(nodeId)}"` : ""} title="Click to send to this node">
        <td>${escapeHtml(n.short || "—")}</td>
        <td>${escapeHtml(n.long || "—")}</td>
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

function renderMessages(messages) {
  const list = $("messagesList");
  const key = JSON.stringify([
    channelsVersion,
    messages.map((m) => [m.rxTime, m.fromId, m.toId, m.text, m.portnum, m.channel]),
  ]);
  if (key === lastMessagesKey) return;
  lastMessagesKey = key;

  if (!messages || messages.length === 0) {
    list.innerHTML = `<div class="muted">No messages yet</div>`;
    return;
  }

  const rows = [];
  for (const m of messages) {
    const from = m.fromId || "—";
    const to = m.toId || "—";
    const snr = m.snr === null || m.snr === undefined ? "—" : String(m.snr);
    const rssi = m.rssi === null || m.rssi === undefined ? "—" : String(m.rssi);
    const chNum = m.channel === null || m.channel === undefined ? null : Number(m.channel);
    const chName = chNum !== null && channelsByIndex.has(chNum) ? channelsByIndex.get(chNum) : null;
    const chLabel = chNum === null ? "Ch —" : chName ? `Ch ${chNum} (${chName})` : `Ch ${chNum}`;
    const text = m.text ? escapeHtml(m.text) : `<span class="muted">port ${escapeHtml(String(m.portnum ?? "—"))}</span>`;

    rows.push(`
      <div class="msg">
        <div class="meta">
          <span>${escapeHtml(fmtTime(m.rxTime))}</span>
          <span class="mono">${escapeHtml(from)} → ${escapeHtml(to)}</span>
          <span>${escapeHtml(chLabel)}</span>
          <span>SNR ${escapeHtml(snr)} / RSSI ${escapeHtml(rssi)}</span>
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

let lastNodes = null;
async function tickNodes() {
  try {
    const n = await apiFetch("/api/nodes");
    lastNodes = n;
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
  } catch (e) {
    $("nodesMeta").textContent = `Failed to load nodes: ${e.message}`;
  }
}

async function tickMessages() {
  try {
    const msgs = await apiFetch("/api/messages");
    notifyIfNewMessages(msgs);
    renderMessages(msgs);
    let meta = `${msgs.length} messages • refresh ${new Date().toLocaleTimeString()}`;
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

async function tickChannels() {
  try {
    const data = await apiFetch("/api/channels");
    channelsByIndex = new Map();
    if (Array.isArray(data.channels)) {
      for (const ch of data.channels) {
        if (typeof ch.index === "number") {
          const nm = (ch.name || "").trim();
          if (nm) channelsByIndex.set(ch.index, nm);
        }
      }
    }
    channelsVersion += 1;
    renderChannels(data);
  } catch (e) {
    $("channelsMeta").textContent = `Failed to load channels: ${e.message}`;
  }
}

function renderChannels(data) {
  const tbody = $("channelsTbody");
  const list = Array.isArray(data.channels) ? data.channels : [];
  if (list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">No channels</td></tr>`;
    $("channelsMeta").textContent = `0 channels`;
    return;
  }

  const rows = [];
  for (const ch of list) {
    const idx = ch.index === null || ch.index === undefined ? "—" : String(ch.index);
    const name = ch.name || "—";
    const role = ch.role || "—";
    const enabled =
      ch.enabled === true ? "true" : ch.enabled === false ? "false" : "—";
    rows.push(`
      <tr>
        <td class="col-ch-index">${escapeHtml(idx)}</td>
        <td>${escapeHtml(name)}</td>
        <td>${escapeHtml(role)}</td>
        <td>${escapeHtml(enabled)}</td>
      </tr>
    `);
  }
  tbody.innerHTML = rows.join("");
  const updated = data.generatedAt ? new Date(data.generatedAt * 1000).toLocaleTimeString() : "—";
  $("channelsMeta").textContent = `${list.length} channels • updated ${updated}`;
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
      note: "mqttPassword is not included in exports",
    };

    const safeTransport = String(h.transport || "tcp").toLowerCase();
    const suffix = safeTransport === "mqtt" ? "mqtt" : "tcp";
    downloadJson(exported, `meshtastic-monitor-config.${suffix}.json`);
    showToast("ok", "Exported configuration");
  } catch (e) {
    showToast("err", `Export failed: ${e.message}`);
  }
}

async function onSend(ev) {
  ev.preventDefault();
  const textEl = $("sendText");
  const toEl = $("sendTo");

  const text = (textEl.value || "").trim();
  const to = (toEl.value || "").trim();
  $("sendResult").textContent = "";

  if (!text) {
    showToast("err", "Text is required");
    return;
  }

  try {
    await apiFetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, to: to || undefined }),
    });
    textEl.value = "";
    showToast("ok", "Message sent");
  } catch (e) {
    $("sendResult").textContent = e.message;
    showToast("err", `Send failed: ${e.message}`);
  }
}

function onNodeRowClick(ev) {
  const tr = ev.target.closest("tr[data-node-id]");
  if (!tr) return;
  const nodeId = (tr.getAttribute("data-node-id") || "").trim();
  if (!nodeId) return;

  $("sendTo").value = nodeId;
  $("sendText").focus();
  try {
    $("sendText").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch {
    // ignore if not supported
  }
  showToast("ok", `To: ${nodeId}`);
}

function init() {
  $("btnSettings").addEventListener("click", openModal);
  $("btnCloseModal").addEventListener("click", closeModal);
  $("modalBackdrop").addEventListener("click", closeModal);
  $("btnSaveSettings").addEventListener("click", saveSettings);
  $("btnExportSettings").addEventListener("click", exportConfig);
  $("transportSelect").addEventListener("change", applyTransportUi);
  $("btnCopyHealthJson").addEventListener("click", copyHealthJson);
  $("btnToggleHealthJson").addEventListener("click", toggleHealthJson);

  $("tabDirect").addEventListener("click", () => {
    activeNodesTab = "direct";
    $("tabDirect").classList.add("active");
    $("tabRelayed").classList.remove("active");
    if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
    tickNodes();
  });
  $("tabRelayed").addEventListener("click", () => {
    activeNodesTab = "relayed";
    $("tabRelayed").classList.add("active");
    $("tabDirect").classList.remove("active");
    if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
    tickNodes();
  });

  $("nodeFilter").addEventListener("input", () => {
    if (lastNodes) renderNodes(lastNodes, $("nodeFilter").value);
  });

  $("nodesTbody").addEventListener("click", onNodeRowClick);

  $("sendForm").addEventListener("submit", onSend);

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
  tickMessages();

  window.setInterval(tickHealth, 2500);
  window.setInterval(tickNodes, 5000);
  window.setInterval(tickChannels, 15000);
  window.setInterval(tickMessages, 2000);
}

document.addEventListener("DOMContentLoaded", init);
