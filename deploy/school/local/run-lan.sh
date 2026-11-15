#!/usr/bin/env bash
# Run the school edition on a Linux machine for testing inside the LAN (SQLite + local files, NOT production).
#
# Same steps as setup-local.ps1 + run-local.ps1, for Linux:
#   1. Python venv with the dependencies of the matching label-studio wheel, importing this checkout
#   2. frontend build (npm + nx webpack target) when web/dist is missing or REBUILD=1
#   3. migrate, collectstatic, demo colleges and accounts (SEED=1, password Demo@2026)
#   4. the 3D point cloud ML backend on :9090 (TRACKER=0 to skip) and Label Studio on 0.0.0.0:$PORT
#
# Usage (from anywhere):   bash deploy/school/local/run-lan.sh
#   SEED=1 bash deploy/school/local/run-lan.sh       first run: also create the demo accounts
#   REBUILD=1 ...                                     rebuild the frontend after pulling new code
#   PORT=8080 VENV=... DATA_DIR=... LAN_IP=... PYTHON=python3.12 ...
# Needs: Python 3.10-3.12 with venv, Node.js 20+ (22 recommended) with yarn 1 (installed if missing),
# ~8 GB RAM for the frontend build.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
WORKSPACE=$(dirname "$REPO")
VENV=${VENV:-$WORKSPACE/.venv}
DATA_DIR=${DATA_DIR:-$WORKSPACE/local-data}
PORT=${PORT:-8080}
TRACKER_PORT=${TRACKER_PORT:-9090}
LS_VERSION=${LS_VERSION:-1.23.2}
PYTHON=${PYTHON:-python3}
LAN_IP=${LAN_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}
LAN_IP=${LAN_IP:-127.0.0.1}

say() { printf '\n==> %s\n' "$*"; }

# 1. Python environment ------------------------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating virtual environment $VENV"
  "$PYTHON" -m venv "$VENV"
fi
PY="$VENV/bin/python"
if ! "$PY" -c 'import django, label_studio' 2>/dev/null; then
  say "Installing label-studio $LS_VERSION dependencies"
  "$PY" -m pip install --quiet --upgrade pip
  WHEELS=$(mktemp -d)
  "$PY" -m pip download --quiet --no-deps -d "$WHEELS" "label-studio==$LS_VERSION"
  "$PY" -m pip install --quiet "$WHEELS"/label_studio-"$LS_VERSION"-*.whl
  # keep the dependencies, import this checkout instead of the packaged copy
  "$PY" -m pip uninstall --quiet -y label-studio
  "$PY" - "$REPO" "$LS_VERSION" <<'EOF'
import pathlib, sys, sysconfig
repo, version = pathlib.Path(sys.argv[1]), sys.argv[2]
# purelib: site-packages of the venv (Debian's site.getsitepackages() also lists a missing dist-packages)
packages = pathlib.Path(sysconfig.get_paths()['purelib'])
(packages / 'label_studio_src.pth').write_text(str(repo) + '\n')
info = packages / f'label_studio-{version}.dist-info'
info.mkdir(exist_ok=True)
(info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: label-studio\nVersion: {version}\n')
(info / 'RECORD').write_text('')
EOF
  rm -rf "$WHEELS"
fi

# 2. Frontend ---------------------------------------------------------------------------------------
if [ "${REBUILD:-0}" = 1 ] || [ ! -d "$REPO/web/dist/apps/labelstudio" ]; then
  command -v node >/dev/null || { echo "Node.js 20+ is required to build the frontend"; exit 1; }
  [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] || { echo "Node.js 20+ is required (found $(node -v))"; exit 1; }
  say "Building the frontend (several minutes, needs ~8 GB RAM)"
  cd "$REPO/web"
  if [ ! -f node_modules/nx/bin/nx.js ] || [ ! -d node_modules/three ]; then
    # install exactly what yarn.lock pins: without the lockfile npm adds a second webpack copy and the build fails
    command -v yarn >/dev/null || npm install -g yarn@1.22.22
    NODE_OPTIONS=--max-old-space-size=4096 yarn install --frozen-lockfile --ignore-scripts --network-timeout 600000
  fi
  NODE_OPTIONS=--max-old-space-size=6144 NX_DAEMON=false BUILD_NO_SERVER=true node node_modules/nx/bin/nx.js build labelstudio --configuration=development
fi

# 3. Database, static files, demo data --------------------------------------------------------------
export LABEL_STUDIO_BASE_DATA_DIR="$DATA_DIR"
export DJANGO_SETTINGS_MODULE=core.settings.school_local
export PYTHONIOENCODING=utf-8
# other machines in the LAN post forms to http://<this machine>:PORT
export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-http://$LAN_IP:$PORT,http://127.0.0.1:$PORT,http://localhost:$PORT}"
mkdir -p "$DATA_DIR"
cd "$REPO/label_studio"
say "Migrating ($DATA_DIR)"
"$PY" manage.py migrate --noinput
"$PY" manage.py collectstatic --noinput >/dev/null
if [ "${SEED:-0}" = 1 ]; then
  say "Creating demo colleges and accounts (password Demo@2026)"
  "$PY" manage.py school_seed_demo
fi

# 4. ML backend + Label Studio ----------------------------------------------------------------------
TRACKER_PID=
if [ "${TRACKER:-1}" = 1 ]; then
  say "Starting the 3D point cloud ML backend on :$TRACKER_PORT (log: $DATA_DIR/tracker.log)"
  (
    cd "$REPO/ml_backends/pointcloud_tracker"
    PORT=$TRACKER_PORT MODEL_DIR="$DATA_DIR/tracker-models" LABEL_STUDIO_URL="http://127.0.0.1:$PORT" \
      exec "$PY" _wsgi.py
  ) >"$DATA_DIR/tracker.log" 2>&1 &
  TRACKER_PID=$!
  trap '[ -n "$TRACKER_PID" ] && kill "$TRACKER_PID" 2>/dev/null' EXIT
fi

say "Label Studio:      http://$LAN_IP:$PORT/        college workbench: http://$LAN_IP:$PORT/school/"
[ -n "$TRACKER_PID" ] && echo "    ML backend (connect it in project Settings > Model): http://$LAN_IP:$TRACKER_PORT"
echo "    Open the ports in the firewall if needed, e.g.: sudo ufw allow $PORT/tcp; sudo ufw allow $TRACKER_PORT/tcp"
"$PY" manage.py runserver "0.0.0.0:$PORT" --noreload
