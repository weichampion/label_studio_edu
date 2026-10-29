#!/usr/bin/env bash
# Native single-server installer for the Label Studio school edition (Ubuntu 22.04 / 24.04).
#
#   PostgreSQL (official PGDG packages)      -> accounts, projects, tasks, annotation results
#   Garage (pinned, checksum-verified binary)-> uploaded files, avatars, exports (S3 API, loopback only)
#   Label Studio (uv + uWSGI, systemd)       -> application, 127.0.0.1:8000
#   nginx                                    -> HTTPS entry point
#   systemd timer                            -> daily backup to a second disk
#
# Usage:
#   sudo bash deploy/school/install.sh <school.conf>                    install or upgrade (re-runnable)
#   bash deploy/school/install.sh --render-only <school.conf> <out-dir> render config files for review only
set -Eeuo pipefail
umask 022

GARAGE_VERSION=v2.4.1
declare -A GARAGE_SHA256=(
  [x86_64-unknown-linux-musl]=ae49f8de4aaee6b5ca305f7cbdccc8cd8a29b2804aac074649f18bfb83d0a2b6
  [aarch64-unknown-linux-musl]=004b6081c6e3cccbb80317c69e3c5749fbb3fc889e217cc9b89c215bf0dd7ded
)
UV_VERSION=0.13.5
declare -A UV_SHA256=(
  [x86_64-unknown-linux-gnu]=f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f
  [aarch64-unknown-linux-gnu]=4bb69cf95ed13f23713bb7bc09f241896ea0c75b7c557e615a20604b82f9c403
)
BUN_VERSION=1.3.11
declare -A BUN_SHA256=(
  [x64-baseline]=abe346f63414547cdf6b35b7a649a490c728b93d006226156923918a84c0e59b
  [aarch64]=d13944da12a53ecc74bf6a720bd1d04c4555c038dfe422365356a7be47691fdf
)
PYTHON_VERSION=3.12
PGDG_KEY_FINGERPRINT=B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8

APP_ROOT=/opt/label-studio
ETC_DIR=/etc/label-studio
ENV_FILE=$ETC_DIR/label-studio.env
INSTALL_CONF=$ETC_DIR/install.conf
STATE_DIR=/var/lib/label-studio
CACHE_DIR=/var/cache/label-studio
GARAGE_BIN=/usr/local/bin/garage
GARAGE_CONF=/etc/garage/garage.toml
TLS_DIR=/etc/ssl/label-studio

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC_DIR=$(cd "$SCRIPT_DIR/../.." && pwd)
TEMPLATE_VARS='${SERVER_NAME} ${SRC_DIR} ${APP_ROOT} ${DATA_ROOT} ${BACKUP_ROOT} ${PG_MAJOR} ${PG_PORT}
${TLS_CERT} ${TLS_KEY} ${UWSGI_PROCESSES} ${BACKUP_ON_CALENDAR} ${LS_SECRET_KEY} ${S3_BUCKET} ${S3_KEY_ID}
${S3_KEY_SECRET} ${GARAGE_RPC_SECRET} ${PG_SHARED_BUFFERS} ${PG_EFFECTIVE_CACHE_SIZE}'

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}
trap 'die "command failed (line $LINENO): $BASH_COMMAND"' ERR

# ---------------------------------------------------------------------------------------------------
# Configuration and rendering
# ---------------------------------------------------------------------------------------------------

load_config() {
  local conf=$1
  [[ -f $conf ]] || die "config file not found: $conf (copy deploy/school/school.conf.example)"
  # shellcheck disable=SC1090
  source <(tr -d '\r' <"$conf")

  : "${SERVER_NAME:?SERVER_NAME is required}" "${DATA_ROOT:?DATA_ROOT is required}" "${BACKUP_ROOT:?BACKUP_ROOT is required}"
  PG_MAJOR=${PG_MAJOR:-17}
  PGDG_MIRROR=${PGDG_MIRROR:-https://apt.postgresql.org/pub/repos/apt}
  S3_BUCKET=${S3_BUCKET:-label-studio}
  UWSGI_PROCESSES=${UWSGI_PROCESSES:-4}
  BACKUP_ON_CALENDAR=${BACKUP_ON_CALENDAR:-*-*-* 02:30:00}
  BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-30}
  ALLOW_SAME_DISK_BACKUP=${ALLOW_SAME_DISK_BACKUP:-no}
  SKIP_FRONTEND_BUILD=${SKIP_FRONTEND_BUILD:-no}
  TLS_CERT=${TLS_CERT:-}
  TLS_KEY=${TLS_KEY:-}

  [[ $SERVER_NAME =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "SERVER_NAME must be a host name or IPv4 address"
  local path
  for path in "$DATA_ROOT" "$BACKUP_ROOT" "$SRC_DIR"; do
    [[ $path =~ ^/[A-Za-z0-9/._-]+$ ]] || die "path must be absolute without spaces or special characters: $path"
  done
  case "$BACKUP_ROOT/" in "$DATA_ROOT/"*) die "BACKUP_ROOT must not be inside DATA_ROOT" ;; esac
  case "$DATA_ROOT/" in "$BACKUP_ROOT/"*) die "DATA_ROOT must not be inside BACKUP_ROOT" ;; esac
  [[ $PG_MAJOR =~ ^[0-9]+$ ]] && ((PG_MAJOR >= 14)) || die "PG_MAJOR must be 14 or newer"
  [[ $S3_BUCKET =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]] || die "S3_BUCKET must be a lowercase S3 bucket name"
  [[ $UWSGI_PROCESSES =~ ^[1-9][0-9]?$ ]] || die "UWSGI_PROCESSES must be a number between 1 and 99"
  [[ $BACKUP_RETENTION_DAYS =~ ^[1-9][0-9]*$ ]] || die "BACKUP_RETENTION_DAYS must be a positive number"
  [[ $BACKUP_ON_CALENDAR =~ ^[0-9A-Za-z*:/,.\ -]+$ ]] || die "BACKUP_ON_CALENDAR contains unsupported characters"
  if [[ -n $TLS_CERT || -n $TLS_KEY ]]; then
    [[ $TLS_CERT == /* && $TLS_KEY == /* ]] || die "TLS_CERT and TLS_KEY must both be absolute paths"
  fi
  [[ -f $SRC_DIR/label_studio/manage.py && -f $SRC_DIR/web/package.json ]] || die "unexpected source layout in $SRC_DIR"

  export SERVER_NAME DATA_ROOT BACKUP_ROOT PG_MAJOR S3_BUCKET UWSGI_PROCESSES BACKUP_ON_CALENDAR TLS_CERT TLS_KEY
  export SRC_DIR APP_ROOT
}

# Render a template; nginx/uWSGI runtime variables ($host, $(UWSGI_PROCESSES)) are left untouched.
render() {
  local src=$1 dest=$2
  tr -d '\r' <"$src" | envsubst "$TEMPLATE_VARS" >"$dest"
  if grep -Eq '\$\{(SERVER_NAME|SRC_DIR|APP_ROOT|DATA_ROOT|BACKUP_ROOT|PG_[A-Z_]+|TLS_[A-Z]+|UWSGI_PROCESSES|BACKUP_ON_CALENDAR|LS_SECRET_KEY|S3_[A-Z_]+|GARAGE_RPC_SECRET)\}' "$dest"; then
    die "unrendered placeholder left in $dest"
  fi
}

# Render into place; sets CHANGED=1 when the destination content changed.
install_rendered() {
  local src=$1 dest=$2 mode=$3 owner=$4 group=$5 tmp
  tmp=$(mktemp)
  render "$src" "$tmp"
  if [[ -f $dest ]] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    chown "$owner:$group" "$dest"
    chmod "$mode" "$dest"
    return 0
  fi
  install -D -m "$mode" -o "$owner" -g "$group" "$tmp" "$dest"
  rm -f "$tmp"
  CHANGED=1
}

write_install_conf() {
  local dest=$1
  {
    echo '# Managed by deploy/school/install.sh; read by label-studio-manage and the backup scripts.'
    local name
    for name in SERVER_NAME SRC_DIR APP_ROOT DATA_ROOT BACKUP_ROOT PG_MAJOR PG_PORT S3_BUCKET \
      BACKUP_RETENTION_DAYS ALLOW_SAME_DISK_BACKUP; do
      printf '%s=%q\n' "$name" "${!name}"
    done
  } >"$dest"
}

render_only() {
  local out=$2
  load_config "$1"
  mkdir -p "$out"
  export PG_PORT=5432 PG_SHARED_BUFFERS=1024MB PG_EFFECTIVE_CACHE_SIZE=4096MB
  export LS_SECRET_KEY=render-only-secret-key-render-only-secret-key-render-only-secret-key
  export S3_KEY_ID=GKrenderonly000000000000 S3_KEY_SECRET=render-only-s3-secret-key-render-only
  export GARAGE_RPC_SECRET=0000000000000000000000000000000000000000000000000000000000000000
  TLS_CERT=${TLS_CERT:-$TLS_DIR/selfsigned.crt}
  TLS_KEY=${TLS_KEY:-$TLS_DIR/selfsigned.key}
  export TLS_CERT TLS_KEY
  local f
  for f in "$SCRIPT_DIR"/templates/* "$SCRIPT_DIR"/systemd/*; do
    render "$f" "$out/$(basename "$f")"
  done
  write_install_conf "$out/install.conf"
  echo "Rendered files written to $out (secrets are placeholders)."
}

# ---------------------------------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------------------------------

physical_disks() {
  local src
  src=$(findmnt -n -o SOURCE --target "$1" 2>/dev/null) || return 0
  src=${src%%\[*}
  [[ -b $src ]] || return 0
  lsblk -n -r -s -o NAME,TYPE "$src" 2>/dev/null | awk '$2=="disk"{print $1}' | sort -u | tr '\n' ' '
}

preflight() {
  [[ $EUID -eq 0 ]] || die "run as root: sudo bash $0 <school.conf>"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ $ID == ubuntu && ($VERSION_ID == 22.04 || $VERSION_ID == 24.04) ]] || die "only Ubuntu 22.04 and 24.04 are supported (found $PRETTY_NAME)"
  case "$SRC_DIR" in /home/* | /root | /root/*) die "move the source checkout to /opt/label-studio/src (services cannot read /home or /root)" ;; esac

  case "$(uname -m)" in
    x86_64) GARAGE_TARGET=x86_64-unknown-linux-musl UV_TARGET=x86_64-unknown-linux-gnu BUN_TARGET=x64-baseline ;;
    aarch64) GARAGE_TARGET=aarch64-unknown-linux-musl UV_TARGET=aarch64-unknown-linux-gnu BUN_TARGET=aarch64 ;;
    *) die "unsupported CPU architecture: $(uname -m)" ;;
  esac

  MEM_MB=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
  ((MEM_MB >= 3800)) || die "at least 4 GB RAM is required (found ${MEM_MB} MB)"
  if [[ $SKIP_FRONTEND_BUILD != yes ]] && ((MEM_MB < 7500)); then
    warn "the front-end build needs about 6 GB of memory; add swap or set SKIP_FRONTEND_BUILD=yes with a prebuilt web/dist"
  fi

  install -d -m 755 "$DATA_ROOT"
  if ! mountpoint -q "$BACKUP_ROOT"; then
    [[ $ALLOW_SAME_DISK_BACKUP == yes ]] || die "$BACKUP_ROOT is not a mount point: mount the second disk there and add it to /etc/fstab"
    warn "$BACKUP_ROOT is not a separate mount (ALLOW_SAME_DISK_BACKUP=yes): backups will NOT survive a disk failure"
  fi
  install -d -m 700 "$BACKUP_ROOT"
  local data_disks backup_disks
  data_disks=$(physical_disks "$DATA_ROOT")
  backup_disks=$(physical_disks "$BACKUP_ROOT")
  if [[ -z $data_disks || -z $backup_disks ]]; then
    warn "could not determine the physical disks behind $DATA_ROOT / $BACKUP_ROOT; verify with lsblk that they differ"
  elif comm -12 <(tr ' ' '\n' <<<"$data_disks" | sed '/^$/d' | sort -u) <(tr ' ' '\n' <<<"$backup_disks" | sed '/^$/d' | sort -u) | grep -q .; then
    [[ $ALLOW_SAME_DISK_BACKUP == yes ]] || die "data ($data_disks) and backup ($backup_disks) share a physical disk"
    warn "data and backup share a physical disk (ALLOW_SAME_DISK_BACKUP=yes)"
  fi
  log "Data disk(s): ${data_disks:-unknown}  Backup disk(s): ${backup_disks:-unknown}"
}

# ---------------------------------------------------------------------------------------------------
# Installation steps
# ---------------------------------------------------------------------------------------------------

download_verified() {
  local url=$1 sha=$2 dest=$3 tmp
  tmp=$(mktemp)
  curl -fL --retry 3 --connect-timeout 20 -o "$tmp" "$url"
  if ! echo "$sha  $tmp" | sha256sum -c --quiet -; then
    rm -f "$tmp"
    die "checksum mismatch for $url"
  fi
  mv "$tmp" "$dest"
}

install_packages() {
  log "Installing system packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl gnupg git unzip openssl jq nginx \
    gettext-base build-essential util-linux postgresql-common
}

create_users() {
  id -u labelstudio &>/dev/null || useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin labelstudio
  id -u garage &>/dev/null || useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin garage
}

install_postgresql() {
  log "Installing PostgreSQL $PG_MAJOR (PGDG)"
  local keyring=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc fpr gnupg_home
  install -d /usr/share/postgresql-common/pgdg
  if [[ ! -s $keyring ]]; then
    curl -fsSL --retry 3 -o "$keyring.tmp" https://www.postgresql.org/media/keys/ACCC4CF8.asc
    mv "$keyring.tmp" "$keyring"
  fi
  gnupg_home=$(mktemp -d)
  fpr=$(GNUPGHOME=$gnupg_home gpg --show-keys --with-colons "$keyring" | awk -F: '$1=="fpr"{print $10; exit}')
  rm -rf "$gnupg_home"
  [[ $fpr == "$PGDG_KEY_FINGERPRINT" ]] || die "unexpected PGDG signing key fingerprint: $fpr"
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [signed-by=$keyring] $PGDG_MIRROR ${VERSION_CODENAME}-pgdg main" >/etc/apt/sources.list.d/pgdg.list

  # The cluster is created explicitly on DATA_ROOT instead of the package default in /var/lib.
  local cc=/etc/postgresql-common/createcluster.conf
  if grep -Eq '^[[:space:]]*#?[[:space:]]*create_main_cluster[[:space:]]*=' "$cc"; then
    sed -i -E 's/^[[:space:]]*#?[[:space:]]*create_main_cluster[[:space:]]*=.*/create_main_cluster = false/' "$cc"
  else
    echo 'create_main_cluster = false' >>"$cc"
  fi
  apt-get update
  apt-get install -y --no-install-recommends "postgresql-$PG_MAJOR" "postgresql-client-$PG_MAJOR"

  local pgdata=$DATA_ROOT/postgresql/$PG_MAJOR/main
  if ! pg_lsclusters -h | awk -v v="$PG_MAJOR" '$1==v && $2=="main" {found=1} END {exit !found}'; then
    install -d -o postgres -g postgres -m 700 "$DATA_ROOT/postgresql" "$DATA_ROOT/postgresql/$PG_MAJOR"
    if [[ -d $pgdata && -n $(ls -A "$pgdata") ]]; then
      die "$pgdata is not empty but no cluster is registered; inspect it before continuing"
    fi
    pg_createcluster --encoding=UTF8 --locale=C.UTF-8 -d "$pgdata" "$PG_MAJOR" main -- --data-checksums
  fi
  local actual_dir
  actual_dir=$(pg_lsclusters -h | awk -v v="$PG_MAJOR" '$1==v && $2=="main" {print $6}')
  [[ $actual_dir == "$pgdata" ]] || warn "existing cluster $PG_MAJOR/main uses $actual_dir, not $pgdata"
  PG_PORT=$(pg_lsclusters -h | awk -v v="$PG_MAJOR" '$1==v && $2=="main" {print $3}')
  export PG_PORT

  export PG_SHARED_BUFFERS="$((MEM_MB / 4 > 4096 ? 4096 : MEM_MB / 4))MB"
  export PG_EFFECTIVE_CACHE_SIZE="$((MEM_MB / 2))MB"
  CHANGED=0
  install_rendered "$SCRIPT_DIR/templates/postgresql.conf" \
    "/etc/postgresql/$PG_MAJOR/main/conf.d/90-label-studio.conf" 644 postgres postgres
  systemctl enable "postgresql@$PG_MAJOR-main" >/dev/null
  if ((CHANGED)); then
    systemctl restart "postgresql@$PG_MAJOR-main"
  else
    systemctl start "postgresql@$PG_MAJOR-main"
  fi
  local i
  for i in $(seq 30); do
    runuser -u postgres -- pg_isready -q -p "$PG_PORT" && break
    sleep 1
  done
  runuser -u postgres -- pg_isready -q -p "$PG_PORT" || die "PostgreSQL did not become ready"

  psql_admin() { runuser -u postgres -- psql -p "$PG_PORT" -v ON_ERROR_STOP=1 -tAq "$@"; }
  [[ $(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = 'labelstudio'") == 1 ]] ||
    psql_admin -c 'CREATE ROLE labelstudio LOGIN'
  [[ $(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = 'labelstudio'") == 1 ]] ||
    runuser -u postgres -- createdb -p "$PG_PORT" -O labelstudio -E UTF8 -T template0 labelstudio
}

read_env_value() {
  [[ -f $ENV_FILE ]] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

load_or_create_secrets() {
  LS_SECRET_KEY=$(read_env_value SECRET_KEY)
  S3_KEY_ID=$(read_env_value STORAGE_AWS_ACCESS_KEY_ID)
  S3_KEY_SECRET=$(read_env_value STORAGE_AWS_SECRET_ACCESS_KEY)
  [[ -n $LS_SECRET_KEY ]] || LS_SECRET_KEY=$(openssl rand -hex 48)
  [[ -n $S3_KEY_ID ]] || S3_KEY_ID="GK$(openssl rand -hex 12)"
  [[ -n $S3_KEY_SECRET ]] || S3_KEY_SECRET=$(openssl rand -hex 32)
  export LS_SECRET_KEY S3_KEY_ID S3_KEY_SECRET
}

gcli() { runuser -u garage -- "$GARAGE_BIN" -c "$GARAGE_CONF" "$@"; }

install_garage() {
  log "Installing Garage $GARAGE_VERSION"
  local upgraded=0
  if [[ ! -x $GARAGE_BIN ]] || ! "$GARAGE_BIN" --version 2>/dev/null | grep -qF "$GARAGE_VERSION"; then
    local tmp
    tmp=$(mktemp)
    download_verified "https://garagehq.deuxfleurs.fr/_releases/$GARAGE_VERSION/$GARAGE_TARGET/garage" \
      "${GARAGE_SHA256[$GARAGE_TARGET]}" "$tmp"
    install -m 755 "$tmp" "$GARAGE_BIN"
    rm -f "$tmp"
    upgraded=1
  fi

  install -d -o garage -g garage -m 700 "$DATA_ROOT/garage" "$DATA_ROOT/garage/meta" \
    "$DATA_ROOT/garage/data" "$DATA_ROOT/garage/snapshots"
  install -d -o root -g garage -m 750 /etc/garage
  GARAGE_RPC_SECRET=
  [[ -f $GARAGE_CONF ]] && GARAGE_RPC_SECRET=$(sed -n 's/^rpc_secret = "\([0-9a-f]*\)"$/\1/p' "$GARAGE_CONF")
  [[ -n $GARAGE_RPC_SECRET ]] || GARAGE_RPC_SECRET=$(openssl rand -hex 32)
  [[ $GARAGE_RPC_SECRET =~ ^[0-9a-f]{64}$ ]] || die "invalid rpc_secret in $GARAGE_CONF"
  export GARAGE_RPC_SECRET

  CHANGED=$upgraded
  install_rendered "$SCRIPT_DIR/templates/garage.toml" "$GARAGE_CONF" 640 root garage
  install_rendered "$SCRIPT_DIR/systemd/garage.service" /etc/systemd/system/garage.service 644 root root
  systemctl daemon-reload
  systemctl enable garage >/dev/null
  if ((CHANGED)); then systemctl restart garage; else systemctl start garage; fi

  local i bucket_json bucket_id
  for i in $(seq 60); do
    gcli status >/dev/null 2>&1 && break
    sleep 1
  done
  gcli status >/dev/null || die "Garage did not start; see: journalctl -u garage"

  # Application key with read/write on one private bucket only (no owner, no bucket creation).
  bucket_json=$(jq -nc --arg b "$S3_BUCKET" '{globalAlias: $b}')
  for i in $(seq 30); do
    if gcli json-api GetKeyInfo "$(jq -nc --arg k "$S3_KEY_ID" '{id: $k}')" >/dev/null 2>&1 ||
      jq -nc --arg k "$S3_KEY_ID" --arg s "$S3_KEY_SECRET" \
        '{accessKeyId: $k, secretAccessKey: $s, name: "label-studio-app"}' | gcli json-api ImportKey - >/dev/null; then
      break
    fi
    sleep 1
  done
  gcli json-api GetBucketInfo "$bucket_json" >/dev/null 2>&1 || gcli json-api CreateBucket "$bucket_json" >/dev/null
  bucket_id=$(gcli json-api GetBucketInfo "$bucket_json" | jq -r .id)
  gcli json-api AllowBucketKey "$(jq -nc --arg b "$bucket_id" --arg k "$S3_KEY_ID" \
    '{bucketId: $b, accessKeyId: $k, permissions: {read: true, write: true, owner: false}}')" >/dev/null
}

setup_tls() {
  if [[ -n $TLS_CERT ]]; then
    [[ -r $TLS_CERT && -r $TLS_KEY ]] || die "cannot read TLS_CERT/TLS_KEY"
    [[ $(openssl x509 -in "$TLS_CERT" -noout -pubkey | openssl sha256) == $(openssl pkey -in "$TLS_KEY" -pubout | openssl sha256) ]] ||
      die "TLS_CERT and TLS_KEY do not belong together"
    return
  fi
  TLS_CERT=$TLS_DIR/selfsigned.crt
  TLS_KEY=$TLS_DIR/selfsigned.key
  export TLS_CERT TLS_KEY
  install -d -m 755 "$TLS_DIR"
  if [[ ! -s $TLS_CERT || $(cat "$TLS_DIR/selfsigned.name" 2>/dev/null) != "$SERVER_NAME" ]]; then
    log "Generating a self-signed certificate for $SERVER_NAME"
    local san="DNS:$SERVER_NAME"
    [[ $SERVER_NAME =~ ^[0-9.]+$ ]] && san="IP:$SERVER_NAME"
    (
      umask 077
      openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 825 -subj "/CN=$SERVER_NAME" \
        -addext "subjectAltName=$san" -keyout "$TLS_KEY" -out "$TLS_CERT" 2>/dev/null
    )
    chmod 644 "$TLS_CERT"
    echo "$SERVER_NAME" >"$TLS_DIR/selfsigned.name"
  fi
  warn "using a self-signed certificate: browsers will warn until it is replaced (TLS_CERT/TLS_KEY)"
}

install_tools() {
  local tools=$APP_ROOT/tools tmp
  install -d -m 755 "$tools"
  if ! "$tools/uv" --version 2>/dev/null | grep -qF "uv $UV_VERSION"; then
    tmp=$(mktemp -d)
    download_verified "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz" \
      "${UV_SHA256[$UV_TARGET]}" "$tmp/uv.tar.gz"
    tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
    install -m 755 "$tmp/uv-$UV_TARGET/uv" "$tools/uv"
    rm -rf "$tmp"
  fi
  if [[ $SKIP_FRONTEND_BUILD != yes ]] && ! "$tools/bun" --version 2>/dev/null | grep -qx "$BUN_VERSION"; then
    tmp=$(mktemp -d)
    download_verified "https://github.com/oven-sh/bun/releases/download/bun-v$BUN_VERSION/bun-linux-$BUN_TARGET.zip" \
      "${BUN_SHA256[$BUN_TARGET]}" "$tmp/bun.zip"
    unzip -q "$tmp/bun.zip" -d "$tmp"
    install -m 755 "$tmp/bun-linux-$BUN_TARGET/bun" "$tools/bun"
    rm -rf "$tmp"
  fi
  export PATH="$tools:$PATH"
}

build_app() {
  log "Installing Python $PYTHON_VERSION and Label Studio dependencies"
  export UV_PYTHON_INSTALL_DIR=$APP_ROOT/python UV_PROJECT_ENVIRONMENT=$APP_ROOT/venv UV_CACHE_DIR=$CACHE_DIR/uv
  export UV_PYTHON_PREFERENCE=only-managed UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_NO_PROGRESS=1
  uv python install "$PYTHON_VERSION"
  (cd "$SRC_DIR" && uv sync --frozen --no-dev --extra uwsgi --python "$PYTHON_VERSION")

  if [[ $SKIP_FRONTEND_BUILD == yes ]]; then
    [[ -d $SRC_DIR/web/dist/apps/labelstudio && -f $SRC_DIR/label_studio/core/static/js/manifest.json ]] ||
      die "SKIP_FRONTEND_BUILD=yes but no prebuilt web/dist and static/js/manifest.json were found"
  else
    log "Building the front end (several minutes)"
    install -d "$SRC_DIR/label_studio/core/static/js"
    (
      cd "$SRC_DIR/web"
      export BUN_INSTALL_CACHE_DIR=$CACHE_DIR/bun
      bun install --frozen-lockfile
      BUILD_NO_SERVER=true BUILD_NO_HASH=true BUILD_NO_CHUNKS=true BUILD_MODULE=true NODE_ENV=production \
        NODE_OPTIONS=--max-old-space-size=4096 bun run build
    )
  fi
}

write_app_config() {
  install -d -o root -g labelstudio -m 750 "$ETC_DIR"
  CHANGED=0
  install_rendered "$SCRIPT_DIR/templates/label-studio.env" "$ENV_FILE" 640 root labelstudio
  install_rendered "$SCRIPT_DIR/templates/uwsgi.ini" "$ETC_DIR/uwsgi.ini" 640 root labelstudio
  write_install_conf "$INSTALL_CONF"
  chmod 644 "$INSTALL_CONF"
  install -m 755 <(tr -d '\r' <"$SCRIPT_DIR/bin/label-studio-manage") /usr/local/sbin/label-studio-manage
}

django_as_root() {
  (
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    cd "$SRC_DIR/label_studio"
    "$APP_ROOT/venv/bin/python" manage.py "$@"
  )
}

prepare_app() {
  log "Collecting static files and applying database migrations"
  install -d -o labelstudio -g labelstudio -m 700 "$STATE_DIR"
  django_as_root collectstatic --noinput >/dev/null
  "$APP_ROOT/venv/bin/python" -m compileall -q -j 0 "$SRC_DIR/label_studio" >/dev/null || warn "some files could not be byte-compiled"
  chown -R labelstudio:labelstudio "$STATE_DIR"

  runuser -u labelstudio -- test -r "$SRC_DIR/label_studio/manage.py" ||
    die "user labelstudio cannot read $SRC_DIR; run: chmod -R a+rX $SRC_DIR"
  runuser -u www-data -- test -r "$SRC_DIR/web/dist/apps/labelstudio" ||
    die "nginx (www-data) cannot read $SRC_DIR/web/dist; run: chmod -R a+rX $SRC_DIR"

  if systemctl is-active --quiet label-studio && [[ -f /etc/systemd/system/label-studio-backup.service ]]; then
    log "Backing up before upgrading"
    systemctl start label-studio-backup.service || die "pre-upgrade backup failed; fix it before migrating"
  fi
  label-studio-manage locked_migrate --noinput
}

configure_nginx() {
  log "Configuring nginx"
  CHANGED=0
  install_rendered "$SCRIPT_DIR/templates/nginx-label-studio.conf" /etc/nginx/sites-available/label-studio.conf 644 root root
  install_rendered "$SCRIPT_DIR/templates/nginx-mjs.conf" /etc/nginx/conf.d/label-studio-mjs.conf 644 root root
  ln -sf /etc/nginx/sites-available/label-studio.conf /etc/nginx/sites-enabled/label-studio.conf
  if [[ -L /etc/nginx/sites-enabled/default ]]; then
    rm -f /etc/nginx/sites-enabled/default
    log "Disabled the nginx default site"
  fi
  nginx -t
  systemctl enable nginx >/dev/null
  systemctl reload-or-restart nginx
}

start_services() {
  log "Starting Label Studio and enabling backups"
  install_rendered "$SCRIPT_DIR/systemd/label-studio.service" /etc/systemd/system/label-studio.service 644 root root
  install_rendered "$SCRIPT_DIR/systemd/label-studio-backup.service" /etc/systemd/system/label-studio-backup.service 644 root root
  install_rendered "$SCRIPT_DIR/systemd/label-studio-backup.timer" /etc/systemd/system/label-studio-backup.timer 644 root root
  systemctl daemon-reload
  systemctl enable label-studio label-studio-backup.timer >/dev/null
  systemctl restart label-studio
  systemctl start label-studio-backup.timer

  local i
  for i in $(seq 60); do
    curl -fsS --max-time 5 -H "Host: $SERVER_NAME" http://127.0.0.1:8000/health/ 2>/dev/null | grep -q UP && return
    sleep 2
  done
  die "Label Studio did not answer on 127.0.0.1:8000; see: journalctl -u label-studio"
}

main() {
  if [[ ${1:-} == --render-only ]]; then
    [[ $# -eq 3 ]] || die "usage: $0 --render-only <school.conf> <out-dir>"
    render_only "$2" "$3"
    return
  fi
  [[ $# -eq 1 ]] || die "usage: sudo bash $0 <school.conf>"
  load_config "$1"
  preflight
  cd /
  install_packages
  create_users
  install_postgresql
  load_or_create_secrets
  install_garage
  setup_tls
  install_tools
  build_app
  write_app_config
  prepare_app
  configure_nginx
  start_services

  cat <<EOF

Installation finished.
  URL:           https://$SERVER_NAME/
  Health check:  sudo bash $SCRIPT_DIR/bin/check.sh
  First admin:   sudo bash $SCRIPT_DIR/bin/create-admin.sh admin@example.edu.cn
  Backup now:    sudo systemctl start label-studio-backup.service
EOF
}

main "$@"
