# Meshtastic Monitor

A tiny web dashboard + JSON API for **Meshtastic over TCP** (direct to your node, default port `4403`).

## What you need (before you start)

1. A Meshtastic node reachable over Wi‑Fi (you know its IP/hostname)
2. Python `3.9+`

## Start (2 minutes)

### 1) Install (one time)

```bash
pip install meshtastic-monitor
```

If `pip` is not available, use:

```bash
python3 -m pip install meshtastic-monitor
```

### 2) Run

```bash
python -m meshtastic_monitor --host YOUR_MESH_IP
```

If `python` is not available, use:

```bash
python3 -m meshtastic_monitor --host YOUR_MESH_IP
```

Then open:

- UI: `http://localhost:8880/`
- Health (JSON): `http://localhost:8880/api/health`

If you run this on another computer in your LAN, open:

- `http://SERVER_IP:8880/`

### 2a) Config file (created automatically)

On first run, a config file is created in the current folder:

- `./meshmon.ini`

This file stores your **mesh host/port** and optional **SMS relay** settings.
You can also choose a custom path:

```bash
python -m meshtastic_monitor --config /path/to/meshmon.ini
```

### 3) Stop

Press `Ctrl+C` in the terminal where it is running.

## Configure (if you didn’t pass `--host`)

1. Open the UI
2. Click **Settings**
3. Put your Meshtastic IP/host + port (`4403`)
4. Click **Save & Apply**

The UI stores these settings in your browser (`localStorage`) and the backend config file
(`meshmon.ini`) when started via `python -m meshtastic_monitor`.

### SMS relay (optional)

If you want **every incoming packet** forwarded via SMS, configure it in Settings:

- Enable **SMS relay**
- Set **SMS API URL**, **API key**, and **Phone**

Example API format:

```
https://your-sms-gateway.example/api?api_key=YOUR_KEY&phone=604632342&message=hello
```

## Command-line options (copy/paste)

Show all options:

```bash
python -m meshtastic_monitor --help
```

Most used:

- `--host` / `--mesh-host` (Meshtastic IP/hostname)
- `--mesh-port` (default `4403`)
- `--http-port` (default `8880`)
- `--log-file` (default `./meshmon.log`)
- `--log-level` (default `INFO`)
- `--nodes-history-interval` (default `60` seconds)
- `--config` (path to `meshmon.ini`)
- `--sms-enabled` / `--sms-disabled`
- `--sms-api-url`
- `--sms-api-key`
- `--sms-phone`

You can also set env vars instead of flags:

- `MESH_HOST` (default empty; can also be set via UI)
- `MESH_PORT` (default `4403`)
- `HTTP_PORT` (default `8880` when using `python -m meshtastic_monitor`)
- `NODES_REFRESH_SEC` (default `5`) refresh live node snapshot
- `MAX_MESSAGES` (default `200`) in-memory ring buffer size
- `STATS_DB_PATH` (default `meshmon.db`, set to `off` to disable persistence)
- `NODES_HISTORY_INTERVAL_SEC` (default `60`) how often to store node history samples
- `STATUS_HISTORY_INTERVAL_SEC` (default `60`) how often to store status samples
- `STATS_WINDOW_HOURS` (default `24`) used by `/api/stats`
- `MESH_HTTP_PORT` (default `80`) for `http://MESH_HOST[:port]/json/report`
- `STATUS_TTL_SEC` (default `5`) cache `/json/report` for this many seconds
- `LOG_LEVEL` (default `INFO`)
- `MESHMON_LOG_FILE` (default `./meshmon.log`)
- `MESHMON_CONFIG` (path to `meshmon.ini`)
- `SMS_ENABLED` (`1`/`0`)
- `SMS_API_URL` (base URL, e.g. `https://your-sms-gateway.example/api`)
- `SMS_API_KEY`
- `SMS_PHONE`
- `SMS_TIMEOUT_SEC` (seconds, default `4`)

## Logs (where are they?)

By default the app writes logs to:

- `./meshmon.log` (the directory where you started the command)

You can change it:

```bash
python -m meshtastic_monitor --log-file /path/to/meshmon.log
```

Logs are rotated (to avoid infinite growth): ~2MB per file, up to 3 backups.

## How it works (simple mental model)

### Two layers

1. **Backend (Python/Flask)** exposes JSON endpoints under `/api/*` and serves the UI.
2. **Frontend (HTML/JS)** is just static files that poll the JSON API.

### What it connects to

- Meshtastic **TCP** interface (library: `meshtastic`, class: `TCPInterface`)
- Your node’s optional HTTP status endpoint: `http://<mesh-host>/json/report`

### Data flow (incoming packets)

1. Backend connects to your node over TCP (`MESH_HOST:MESH_PORT`)
2. The `meshtastic` library publishes received packets on `meshtastic.receive` (via `pypubsub`)
3. We convert every packet to a **thin, JSON-safe** dict (no raw `bytes`) and:
   - keep the last `MAX_MESSAGES` in memory
   - store everything in SQLite (if enabled)
4. If SMS relay is enabled, the packet is forwarded to your SMS gateway URL

### Nodes

- Live node list comes from the Meshtastic node DB (`iface.nodes`)
- Node history is sampled to the DB every `NODES_HISTORY_INTERVAL_SEC` seconds (default `60`) to avoid DB explosion

### Status (“/json/report”)

- The UI shows a nice summary of `http://<mesh-host>/json/report` (if your node exposes it)
- Backend caches it for `STATUS_TTL_SEC` and stores a compact subset in SQLite every `STATUS_HISTORY_INTERVAL_SEC`

## Persistence (history / database)

By default, the app creates a SQLite file:

- `meshmon.db`

It stores:

- message history (so you can scroll back)
- per-node counters (`fromCount`, `toCount`, last RSSI/SNR, etc.)
- node “quality over time” samples (SNR/quality/hops/lastHeard)
- mesh connect/disconnect/error events
- compact status samples (battery/utilization/wifi/etc.)

To disable persistence:

```bash
export STATS_DB_PATH=off
python -m meshtastic_monitor --host YOUR_MESH_IP
```

To reset history: stop the app and delete `meshmon.db`.

## Security notes (important)

- There is **no login/auth**. Run this only on a trusted network.
- `GET /api/device/config` redacts PSKs by default.
  - `GET /api/device/config?includeSecrets=1` will include secrets — do this only if you understand the risk.

## API (for developers)

Quick overview:

- `GET /api/health` – backend status + mesh connection status
- `GET /api/status` – cached `/json/report` + link to the JSON
- `GET /api/config` – current runtime config (mesh + SMS, no secrets)
- `GET /api/nodes` – live nodes (direct + relayed)
- `GET /api/messages` – message history (SQLite if enabled, else memory)
- `POST /api/send` – send text (optional `to` and `channel`)
- `GET /api/stats` – aggregates + charts data
- `GET /api/node/<id>` – combined live + persisted stats for one node
- `GET /api/nodes/history` / `GET /api/node/<id>/history` – history samples

Example:

```bash
curl -s http://localhost:8880/api/health
curl -s http://localhost:8880/api/nodes
curl -s http://localhost:8880/api/messages?limit=20
curl -s -X POST http://localhost:8880/api/send -H 'Content-Type: application/json' -d '{"text":"hello","channel":0}'
```

## Development (optional)

From a git checkout:

```bash
pip install -U pip
pip install -e .
pip install -r backend/requirements-dev.txt
pytest -q
```
