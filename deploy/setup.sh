#!/usr/bin/env bash
# First-time setup of the droplet (Ubuntu 24.04 LTS), run as root. Safe to run again: each step
# checks what is already there. DEPLOY.md walks through it.
#
#     scp deploy/setup.sh root@<droplet>:      (from the Mac; then on the droplet:)
#     bash setup.sh
#
# It stops twice on a first run, saying what to do, then picks up where it left off when run again:
# once for the GitHub deploy key, once for the keys in /etc/write-listen/env.
# Optional: REPO=... BRANCH=... bash setup.sh
set -euo pipefail

REPO="${REPO:-git@github.com:IanDuffy/write-listen.git}"
BRANCH="${BRANCH:-feature/tester-demo}"
APP=/srv/write-listen
USER_NAME=writelisten
HOME_DIR=/home/$USER_NAME
ENV_DIR=/etc/write-listen
ENV_FILE=$ENV_DIR/env

say() { printf '\n== %s\n' "$*"; }
stop() { printf '\n>> %s\n' "$*"; exit 0; }
die() { printf '\nsetup.sh: %s\n' "$*" >&2; exit 1; }
as_app() { sudo -u "$USER_NAME" -H "$@"; }
# A value from the env file (systemd's format: KEY=value, the last one wins, no shell expansion).
envval() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }

[ "$(id -u)" = 0 ] || die "run as root (sudo bash setup.sh)"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || die "this is written for Ubuntu (found ${ID:-unknown})"
cd /  # commands run as writelisten can't read root's home, and git stops in a folder it can't read

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# python3: Ubuntu 24.04's is 3.12, what the app needs (uv uses it for the venv). ffmpeg: audio;
# poppler-utils: PDF text; jq is only for reading the JSON files by hand.
apt-get install -y -q python3 python3-venv git curl ffmpeg poppler-utils ufw ca-certificates gnupg \
  debian-keyring debian-archive-keyring apt-transport-https jq

say "swap (2 GB)"
# Droplets come without swap; three chapter builds and Whisper at once can pass 4 GB, and swap makes
# that slow instead of killing a build.
if [ -z "$(swapon --show --noheadings)" ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon --show

say "caddy (from Caddy's own apt repository)"
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi
caddy version

say "uv"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
uv --version

say "the app's user ($USER_NAME)"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" --shell /bin/bash "$USER_NAME"

say "GitHub deploy key"
key=$HOME_DIR/.ssh/id_ed25519
if [ ! -f "$key" ]; then
  as_app mkdir -p -m 700 "$HOME_DIR/.ssh"
  as_app ssh-keygen -q -t ed25519 -N "" -C "write-listen droplet" -f "$key"
fi
if ! grep -q '^github.com ' "$HOME_DIR/.ssh/known_hosts" 2>/dev/null; then
  ssh-keyscan -t ed25519 github.com 2>/dev/null | as_app tee -a "$HOME_DIR/.ssh/known_hosts" >/dev/null
fi

say "the code ($APP, branch $BRANCH)"
if [ ! -d "$APP/.git" ]; then
  mkdir -p "$APP"
  chown "$USER_NAME:$USER_NAME" "$APP"
  if ! as_app git clone --branch "$BRANCH" "$REPO" "$APP"; then
    echo
    echo "This key can't read the repository yet (or $BRANCH isn't pushed). On GitHub: the repository >"
    echo "Settings > Deploy keys > Add deploy key, title 'droplet', read access only, and paste:"
    echo
    cat "$key.pub"
    stop "Then run setup.sh again."
  fi
fi
cd "$APP"
as_app uv sync --no-dev --frozen
as_app mkdir -p listeners shared

say "the env file ($ENV_FILE)"
mkdir -p "$ENV_DIR"
chmod 755 "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" deploy/env.example "$ENV_FILE"
  stop "Fill in $ENV_FILE (nano $ENV_FILE): the OpenAI and Anthropic keys, ADMIN_PASSWORD, WL_BASE_URL, TZ. Then run setup.sh again."
fi
chown "$USER_NAME:$USER_NAME" "$ENV_FILE"
chmod 600 "$ENV_FILE"
missing=""
[ -n "$(envval OPENAI_API_KEY)" ] || missing="$missing OPENAI_API_KEY"
[ -n "$(envval WRITE_LISTEN_ANTHROPIC_API_KEY)$(envval ANTHROPIC_API_KEY)" ] || missing="$missing ANTHROPIC_API_KEY"
[ -n "$(envval ADMIN_USER)" ] || missing="$missing ADMIN_USER"
[ -n "$(envval ADMIN_PASSWORD)" ] || missing="$missing ADMIN_PASSWORD"
case "$(envval WL_BASE_URL)" in https://?*) ;; *) missing="$missing WL_BASE_URL(https://...)";; esac
[ -z "$missing" ] || stop "Still empty in $ENV_FILE:$missing. Fill them in, then run setup.sh again."
[ "$(envval WL_DEMO)" = 1 ] || echo "  note: WL_DEMO is not 1, so the app will refuse to run open (deploy/check.sh)"
# The code reads .env from the project folder for `wl` commands; the service gets the same file.
ln -sfn "$ENV_FILE" "$APP/.env"

tz=$(envval TZ)
if [ -n "$tz" ]; then
  say "time zone ($tz)"
  timedatectl set-timezone "$tz"
fi

say "Whisper model (for the archive's timing; downloaded once, about 500 MB)"
as_app .venv/bin/python -c "
from faster_whisper.utils import download_model
from overnight.record import whisper_model
from stages.common import load_config
print(download_model(whisper_model(load_config())))"

say "services"
mkdir -p /var/log/write-listen
for unit in write-listen.service write-listen-log.service write-listen-log.timer; do
  install -m 644 "deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable -q write-listen.service write-listen-log.timer
systemctl restart write-listen.service
systemctl start write-listen-log.timer

say "wl"
ln -sfn "$APP/deploy/wl-droplet" /usr/local/bin/wl

say "caddy config"
domain=$(envval WL_BASE_URL | sed -e 's#^https://##' -e 's#[/:].*$##')
admin_hash=$(envval ADMIN_PASSWORD | caddy hash-password) \
  || die "caddy hash-password failed: hash it by hand (DEPLOY.md, Troubleshooting)"
WL_DOMAIN="$domain" WL_ADMIN_USER="$(envval ADMIN_USER)" WL_ADMIN_HASH="$admin_hash" \
  python3 deploy/render_caddy.py deploy/Caddyfile > /etc/caddy/Caddyfile.new
# It holds the admin hash: readable by Caddy, not by everyone. Validated as Caddy's own user, since
# validating opens the log file and a root-owned one would stop Caddy writing it.
chown root:caddy /etc/caddy/Caddyfile.new
chmod 640 /etc/caddy/Caddyfile.new
sudo -u caddy caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile.new
mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
systemctl enable -q caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy

say "firewall (ssh, http, https)"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443 >/dev/null  # tcp, and udp for HTTP/3
ufw --force enable >/dev/null
ufw status

say "check"
deploy/check.sh

echo
echo "Done. Open https://$domain/admin (user $(envval ADMIN_USER)) to make a tester link."
echo "Later updates: wl deploy"
