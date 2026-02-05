#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Meshtastic Monitor runner

Usage:
  ./run.sh [MESH_HOST]
  ./run.sh --host your-mesh-host --mesh-port 4403 --http-port 8080
  ./run.sh --transport mqtt --mqtt-host broker.example --mqtt-port 1883

Options:
  --transport           Connection type: tcp|mqtt (default: tcp)
  --host, --mesh-host   Meshtastic host/IP (required for TCP unless configured in UI)
  --mesh-port           Meshtastic TCP port (default: 4403)
  --mqtt-host           MQTT broker host (default: mqtt.meshtastic.org)
  --mqtt-port           MQTT broker port (default: 1883)
  --mqtt-username       MQTT username
  --mqtt-password       MQTT password
  --mqtt-tls            Use TLS for MQTT (sets MQTT_TLS=1)
  --mqtt-root-topic     MQTT root topic (optional)
  --http-port           HTTP port for the web app (default: 8080)
  --nodes-history-interval  Node history sample interval in seconds (default: 60)
  --no-install          Skip pip install step
  --no-check            Skip reachability check
  --dev                 Also install dev requirements (pytest)
  -h, --help            Show this help
EOF
}

MESH_TRANSPORT="${MESH_TRANSPORT:-tcp}"
MESH_HOST="${MESH_HOST:-}"
MESH_PORT="${MESH_PORT:-4403}"
HTTP_PORT="${HTTP_PORT:-8080}"
MQTT_HOST="${MQTT_HOST:-mqtt.meshtastic.org}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USERNAME="${MQTT_USERNAME:-}"
MQTT_PASSWORD="${MQTT_PASSWORD:-}"
MQTT_TLS="${MQTT_TLS:-0}"
MQTT_ROOT_TOPIC="${MQTT_ROOT_TOPIC:-}"
NODES_HISTORY_INTERVAL_SEC="${NODES_HISTORY_INTERVAL_SEC:-60}"
DO_INSTALL=1
DO_CHECK=1
DO_DEV=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --transport)
      MESH_TRANSPORT="${2:-}"
      shift 2
      ;;
    --host|--mesh-host)
      MESH_HOST="${2:-}"
      shift 2
      ;;
    --mesh-port)
      MESH_PORT="${2:-}"
      shift 2
      ;;
    --mqtt-host)
      MQTT_HOST="${2:-}"
      shift 2
      ;;
    --mqtt-port)
      MQTT_PORT="${2:-}"
      shift 2
      ;;
    --mqtt-username)
      MQTT_USERNAME="${2:-}"
      shift 2
      ;;
    --mqtt-password)
      MQTT_PASSWORD="${2:-}"
      shift 2
      ;;
    --mqtt-tls)
      MQTT_TLS="1"
      shift
      ;;
    --mqtt-root-topic)
      MQTT_ROOT_TOPIC="${2:-}"
      shift 2
      ;;
    --http-port)
      HTTP_PORT="${2:-}"
      shift 2
      ;;
    --nodes-history-interval)
      NODES_HISTORY_INTERVAL_SEC="${2:-}"
      shift 2
      ;;
    --no-install)
      DO_INSTALL=0
      shift
      ;;
    --no-check)
      DO_CHECK=0
      shift
      ;;
    --dev)
      DO_DEV=1
      shift
      ;;
    *)
      if [[ -z "$MESH_HOST" ]]; then
        MESH_HOST="$1"
        shift
      else
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

MESH_TRANSPORT="$(echo "$MESH_TRANSPORT" | tr '[:upper:]' '[:lower:]' | xargs)"
if [[ "$MESH_TRANSPORT" != "tcp" && "$MESH_TRANSPORT" != "mqtt" ]]; then
  echo "Invalid --transport: $MESH_TRANSPORT (expected tcp|mqtt)" >&2
  exit 2
fi

if ! [[ "$MESH_PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid --mesh-port: $MESH_PORT" >&2
  exit 2
fi
if ! [[ "$HTTP_PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid --http-port: $HTTP_PORT" >&2
  exit 2
fi
if ! [[ "$MQTT_PORT" =~ ^[0-9]+$ ]]; then
  echo "Invalid --mqtt-port: $MQTT_PORT" >&2
  exit 2
fi
if [[ "$MESH_TRANSPORT" == "mqtt" && -z "$(echo "$MQTT_HOST" | xargs)" ]]; then
  echo "MQTT transport requires --mqtt-host (or MQTT_HOST env var)" >&2
  exit 2
fi

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-"$ROOT_DIR/.pycache"}"
mkdir -p "$PYTHONPYCACHEPREFIX"

if [[ ! -d ".venv" ]]; then
  echo "Creating venv at .venv/"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

if [[ "$DO_INSTALL" -eq 1 ]]; then
  python -m pip install -r backend/requirements.txt
  if [[ "$DO_DEV" -eq 1 ]]; then
    python -m pip install -r backend/requirements-dev.txt
  fi
fi

export MESH_HOST="$MESH_HOST"
export MESH_PORT="$MESH_PORT"
export MESH_TRANSPORT="$MESH_TRANSPORT"
export HTTP_PORT="$HTTP_PORT"
export MQTT_HOST="$MQTT_HOST"
export MQTT_PORT="$MQTT_PORT"
export MQTT_USERNAME="$MQTT_USERNAME"
export MQTT_PASSWORD="$MQTT_PASSWORD"
export MQTT_TLS="$MQTT_TLS"
export MQTT_ROOT_TOPIC="$MQTT_ROOT_TOPIC"
export NODES_HISTORY_INTERVAL_SEC="$NODES_HISTORY_INTERVAL_SEC"

if [[ "$DO_CHECK" -eq 1 ]]; then
  if [[ "$MESH_TRANSPORT" == "mqtt" ]]; then
    CHECK_HOST="$MQTT_HOST"
    CHECK_PORT="$MQTT_PORT"
    CHECK_LABEL="MQTT broker"
  else
    if [[ -z "$(echo "$MESH_HOST" | xargs)" ]]; then
      echo "Note: no Meshtastic host provided; skipping TCP reachability check."
      echo "Tip: open http://localhost:${HTTP_PORT}/ and set host/port in Settings."
      CHECK_HOST=""
      CHECK_PORT=""
      CHECK_LABEL=""
    else
      CHECK_HOST="$MESH_HOST"
      CHECK_PORT="$MESH_PORT"
      CHECK_LABEL="Meshtastic TCP"
    fi
  fi

  if [[ -n "$CHECK_HOST" && -n "$CHECK_PORT" ]]; then
    python - "$CHECK_HOST" "$CHECK_PORT" "$CHECK_LABEL" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
label = sys.argv[3]

attempts = 3
timeout = 1.5
last_err = None

for _ in range(attempts):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            print(f"OK: {label} reachable at {host}:{port}")
            sys.exit(0)
    except Exception as e:
        last_err = e
        time.sleep(0.4)

print(f"ERROR: Cannot reach {host}:{port} over TCP: {last_err}", file=sys.stderr)
print(f"Tip: verify {label} host/port and network reachability.", file=sys.stderr)
print("You can start the server anyway with --no-check.", file=sys.stderr)
sys.exit(3)
PY
  fi
fi

echo "Starting Meshtastic Monitor on http://localhost:${HTTP_PORT}/"
exec python -m backend.app
