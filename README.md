# Meshtastic Monitor (simple)

A small, clean monitoring UI + JSON API for a Meshtastic network reachable over **TCP** (direct to node, default `4403`) or optionally via **MQTT** (broker).

## Start in 2 minutes (the simple way)

```bash
chmod +x run.sh
./run.sh --host YOUR_MESH_IP
```

Then open:

- UI: `http://localhost:8080/`
- API health: `http://localhost:8080/api/health`

That’s it. You can also configure the IP/port later in **Settings** inside the UI.

## Features

- **Backend (data layer)**: Python + Flask + `meshtastic` TCP/MQTT client, robust reconnect, JSON-safe endpoints
- **Frontend (view layer)**: Vanilla HTML/CSS/JS that talks only to the JSON API
- **No bytes in JSON**: binary payload is base64-encoded (`payload_b64`)

## Repo layout

```
backend/
  app.py
  jsonsafe.py
  mesh_service.py
  requirements.txt
  requirements-dev.txt
  tests/
frontend/
  index.html
  app.js
  styles.css
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Run

### One command (recommended)

```bash
chmod +x run.sh
./run.sh
```

This will:

- create `.venv/` (if missing)
- install Python dependencies
- (optional) verify target is reachable (Meshtastic TCP or MQTT broker, depending on `--transport`)
- start the backend (which also serves the UI)

Then open `http://localhost:8080/` and set the Meshtastic host/port in **Settings**.

Options:

- `./run.sh --host your-mesh-host --mesh-port 4403 --http-port 8080`
- `./run.sh --transport mqtt --mqtt-host broker.example --mqtt-port 1883`
- `./run.sh --nodes-history-interval 60` (store node history every 60s)
- `./run.sh --no-check` (start even if reachability check fails)

### Manual (env vars)

```bash
# Optional: set here, or configure it in the UI Settings.
export MESH_HOST=your-mesh-host
export MESH_PORT=4403
export MESH_TRANSPORT=tcp   # or mqtt
# MQTT_* env vars are used only when MESH_TRANSPORT=mqtt
export MQTT_HOST=mqtt.meshtastic.org
export MQTT_PORT=1883
export MQTT_USERNAME=
export MQTT_PASSWORD=
export MQTT_TLS=0
export MQTT_ROOT_TOPIC=
export HTTP_PORT=8080
python3 -m backend.app
```

Then open:

- UI: `http://localhost:8080/`
- Health: `http://localhost:8080/api/health`

### Backend config

- Env vars:
  - `MESH_TRANSPORT` (`tcp` or `mqtt`, default `tcp`)
  - `MESH_HOST` (default empty; set via env or the UI)
  - `MESH_PORT` (default `4403`)
  - `MQTT_HOST` (default `mqtt.meshtastic.org`)
  - `MQTT_PORT` (default `1883`)
  - `MQTT_USERNAME` (default empty)
  - `MQTT_PASSWORD` (default empty)
  - `MQTT_TLS` (`0/1`, default `0`)
  - `MQTT_ROOT_TOPIC` (optional)
  - `HTTP_PORT` (default `8080`)
  - `NODES_REFRESH_SEC` (default `5`)
  - `MAX_MESSAGES` (default `200`)
  - `STATS_DB_PATH` (default `meshmon.db`, set to `off` to disable)
  - `STATS_WINDOW_HOURS` (default `24`) used by `/api/stats`
  - `NODES_HISTORY_INTERVAL_SEC` (default `60`, set `0` to store every snapshot)
- Optional runtime update (used by the UI Settings modal):
  - `POST /api/config` (partial updates allowed), e.g. `{ "transport":"tcp", "meshHost":"...", "meshPort":4403 }` or `{ "transport":"mqtt", "mqttHost":"...", "mqttPort":1883 }`
  - Settings modal also supports **Export Config** (downloads a JSON snapshot including device config; MQTT password is never included)

## API

### `GET /api/health`

```json
{
  "ok": true,
  "transport": "tcp",
  "configured": true,
  "meshHost": "your-mesh-host",
  "meshPort": 4403,
  "mqttHost": "mqtt.meshtastic.org",
  "mqttPort": 1883,
  "mqttTls": false,
  "mqttPasswordSet": false,
  "connected": true,
  "generatedAt": 1730000000
}
```

### `GET /api/nodes`

Returns direct + relayed nodes (sorted by freshness; lowest `ageSec` first).

### `GET /api/nodes/history`

Returns history snapshots for all nodes (from SQLite). Query params:

- `nodeId` (optional, filter by node)
- `limit` (default `500`, use `0` for all)
- `since` (epoch seconds, optional)
- `order` (`asc` or `desc`, default `desc`)

### `GET /api/messages`

Returns a list of messages from the persistent history (SQLite). **Ordering: newest last (chronological).**
Query params:

- `limit` (default `200`, use `0` for all)
- `offset` (default `0`)
- `order` (`asc` or `desc`, default `asc`)

History is stored when `STATS_DB_PATH` is enabled (default `meshmon.db`).

### `GET /api/channels`

Returns configured channels (if available from the active transport/interface). Secrets/PSKs are never included.

### `GET /api/radio`

Returns a JSON-safe snapshot of **your local radio** (id, names, hops, metrics, position when available).

### `GET /api/device/config`

Returns the full device configuration (local + module config, channels, metadata, myInfo).
Use `?includeSecrets=1` to include channel PSKs; default redacts PSKs.

### `GET /api/stats`

Returns persisted counters and simple aggregates (SQLite at `STATS_DB_PATH`), including:

- message totals + per-hour buckets (`messages.windowHours`, `messages.hourlyWindow`)
- app appearance counts (`apps.counts`) for Position/NodeInfo/Telemetry/Routing
- requesters targeting you or broadcast (`apps.requestsToMe`)
- top talkers (`nodes.topFrom`, `nodes.topTo`)
- recent mesh/connectivity events (`events`)

### `GET /api/node/<id>`

Returns a combined view of live node data + persisted stats for a single node.

### `GET /api/node/<id>/history`

Returns history snapshots for one node (from SQLite). Query params:

- `limit` (default `500`, use `0` for all)
- `since` (epoch seconds, optional)
- `order` (`asc` or `desc`, default `desc`)

### `POST /api/send`

Body:

```json
{ "text": "hello", "to": "!abcd1234", "channel": 0 }
```

## Curl examples

```bash
curl -s http://localhost:8080/api/health | jq
curl -s http://localhost:8080/api/nodes | jq
curl -s http://localhost:8080/api/messages | jq
curl -s -X POST http://localhost:8080/api/send \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello from curl"}' | jq
```

## Tests

The test suite uses a fake mesh service (no Meshtastic device required).

```bash
pip install -r backend/requirements-dev.txt
pytest
```
