#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

CURRENT_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
git pull
NEW_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
if [[ -n "$CURRENT_SHA" && -n "$NEW_SHA" && "$CURRENT_SHA" != "$NEW_SHA" ]]; then
  echo "Updated repo. Restarting with new code..."
  exec "$0" "$@"
fi

usage() {
  cat <<'EOF'
Meshtastic Monitor runner
Usage: ./run.sh [MESH_HOST] [options]
Options:
  --host|--mesh-host HOST
  --mesh-port PORT (default 4403)
  --http-port PORT (default 8880)
  --nodes-history-interval SEC (default 60)
  --no-install | --no-check | --dev
  -h|--help
EOF
}

MESH_HOST="${MESH_HOST:-}"
MESH_PORT="${MESH_PORT:-4403}"
HTTP_PORT="${HTTP_PORT:-8880}"
NODES_HISTORY_INTERVAL_SEC="${NODES_HISTORY_INTERVAL_SEC:-60}"
DO_INSTALL=1
DO_CHECK=1
DO_DEV=0
USE_VENV=1

sudo_run() { if [[ "$(id -u)" -eq 0 ]]; then "$@"; elif command -v sudo >/dev/null 2>&1; then sudo "$@"; else "$@"; fi; }
is_openwrt() { [[ -f /etc/openwrt_release ]] || { [[ -f /etc/os-release ]] && grep -qi '^ID=openwrt' /etc/os-release; }; }

ensure_venv_support() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  if python3 -m venv "$tmpdir" >/dev/null 2>&1; then rm -rf "$tmpdir"; return 0; fi
  rm -rf "$tmpdir" || true
  echo "Python venv not available; attempting to install system package..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo_run apt-get update -y && sudo_run apt-get install -y python3-venv
  elif command -v dnf >/dev/null 2>&1; then
    sudo_run dnf install -y python3-virtualenv python3
  elif command -v yum >/dev/null 2>&1; then
    sudo_run yum install -y python3-virtualenv python3
  elif command -v apk >/dev/null 2>&1; then
    sudo_run apk add --no-cache python3 py3-virtualenv
  elif command -v pacman >/dev/null 2>&1; then
    sudo_run pacman -Sy --noconfirm python
  elif command -v brew >/dev/null 2>&1; then
    brew install python
  else
    echo "No supported package manager found to install venv support." >&2
    exit 3
  fi
  tmpdir="$(mktemp -d)"
  if python3 -m venv "$tmpdir" >/dev/null 2>&1; then rm -rf "$tmpdir"; return 0; fi
  rm -rf "$tmpdir" || true
  echo "venv still not available after install. Please install Python venv support manually." >&2
  exit 3
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --host|--mesh-host) MESH_HOST="${2:-}"; shift 2 ;;
    --mesh-port) MESH_PORT="${2:-}"; shift 2 ;;
    --http-port) HTTP_PORT="${2:-}"; shift 2 ;;
    --nodes-history-interval) NODES_HISTORY_INTERVAL_SEC="${2:-}"; shift 2 ;;
    --no-install) DO_INSTALL=0; shift ;;
    --no-check) DO_CHECK=0; shift ;;
    --dev) DO_DEV=1; shift ;;
    *)
      if [[ -z "$MESH_HOST" ]]; then MESH_HOST="$1"; shift; else echo "Unknown arg: $1" >&2; usage >&2; exit 2; fi
      ;;
  esac
done

if [[ $# -eq 0 && -t 0 ]]; then
  if [[ -z "$(echo "$MESH_HOST" | xargs)" ]]; then read -r -p "Meshtastic host/IP: " MESH_HOST; fi
  read -r -p "Meshtastic TCP port [${MESH_PORT}]: " _mesh_port_in; [[ -n "${_mesh_port_in:-}" ]] && MESH_PORT="$_mesh_port_in"
  read -r -p "App HTTP port [${HTTP_PORT}]: " _http_port_in; [[ -n "${_http_port_in:-}" ]] && HTTP_PORT="$_http_port_in"
fi

if ! [[ "$MESH_PORT" =~ ^[0-9]+$ ]]; then echo "Invalid --mesh-port: $MESH_PORT" >&2; exit 2; fi
if ! [[ "$HTTP_PORT" =~ ^[0-9]+$ ]]; then echo "Invalid --http-port: $HTTP_PORT" >&2; exit 2; fi

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-"$ROOT_DIR/.pycache"}"
mkdir -p "$PYTHONPYCACHEPREFIX"

if is_openwrt; then USE_VENV=0; fi
if [[ "$USE_VENV" -eq 1 ]]; then
  if [[ ! -d ".venv" ]]; then ensure_venv_support; echo "Creating venv at .venv/"; python3 -m venv .venv; fi
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [[ "$DO_INSTALL" -eq 1 ]]; then
  if [[ "$USE_VENV" -eq 1 ]]; then
    python -m pip install -r backend/requirements.txt
    [[ "$DO_DEV" -eq 1 ]] && python -m pip install -r backend/requirements-dev.txt
  else
    python3 -m pip install --user -r backend/requirements.txt
    [[ "$DO_DEV" -eq 1 ]] && python3 -m pip install --user -r backend/requirements-dev.txt
  fi
fi

export MESH_HOST="$MESH_HOST"
export MESH_PORT="$MESH_PORT"
export HTTP_PORT="$HTTP_PORT"
export NODES_HISTORY_INTERVAL_SEC="$NODES_HISTORY_INTERVAL_SEC"

if [[ "$DO_CHECK" -eq 1 ]]; then
  if [[ -z "$(echo "$MESH_HOST" | xargs)" ]]; then
    echo "Note: no Meshtastic host provided; skipping TCP reachability check."
    echo "Tip: open http://localhost:${HTTP_PORT}/ and set host/port in Settings."
  else
    python - "$MESH_HOST" "$MESH_PORT" <<'PY'
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
attempts, timeout = 3, 1.5
last_err = None
for _ in range(attempts):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            print(f"OK: Meshtastic TCP reachable at {host}:{port}")
            sys.exit(0)
    except Exception as e:
        last_err = e
        time.sleep(0.4)
print(f"ERROR: Cannot reach {host}:{port} over TCP: {last_err}", file=sys.stderr)
print("Tip: verify Meshtastic host/port and network reachability.", file=sys.stderr)
print("You can start the server anyway with --no-check.", file=sys.stderr)
sys.exit(3)
PY
  fi
fi

echo "Starting Meshtastic Monitor on http://localhost:${HTTP_PORT}/"
exec python -m backend.app
