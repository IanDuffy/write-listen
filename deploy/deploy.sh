#!/usr/bin/env bash
# wl deploy: bring the droplet up to date with its branch (DEPLOY.md). Run on the droplet as root:
#
#     wl deploy            pull the branch, install, prepare folders, check, restart, check again
#
# Chapters being made carry on across the restart (they are their own processes; see
# write-listen.service). The conversations on open pages lose the server for a few seconds; their
# voices go to OpenAI directly and keep talking.
set -euo pipefail
app="$(cd "$(dirname "$0")/.." && pwd)"
user=writelisten

# git and uv run as the app's user (the repository and the venv are theirs); systemctl as root.
as_app() { if [ "$(id -u)" = 0 ]; then sudo -u "$user" -H "$@"; else "$@"; fi; }
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

cd "$app"
branch=$(as_app git rev-parse --abbrev-ref HEAD)
before=$(as_app git rev-parse HEAD)
echo "== pulling $branch"
as_app git pull --ff-only origin "$branch"
after=$(as_app git rev-parse HEAD)
if [ "$before" = "$after" ]; then echo "  already up to date"; else as_app git log --oneline "$before..$after"; fi

echo "== installing (uv sync)"
as_app uv sync --no-dev --frozen

# No data migration on this box: it started fresh with listeners/ and shared/ (a laptop moving from
# the old listener/ folder would run `wl listener migrate`; the droplet never had one).
echo "== folders"
as_app mkdir -p listeners shared

echo "== import check"
as_app .venv/bin/python -c "import server, cli" >/dev/null

# Units changed in this pull are installed before the restart. The Caddyfile is not: setup.sh fills
# in its domain and admin login, so it says when to run setup.sh again.
for unit in write-listen.service write-listen-log.service write-listen-log.timer; do
  if ! cmp -s "deploy/$unit" "/etc/systemd/system/$unit"; then
    echo "  installing $unit"
    as_root install -m 644 "deploy/$unit" "/etc/systemd/system/$unit"
    as_root systemctl daemon-reload
  fi
done
if [ "$before" != "$after" ] && [ -n "$(as_app git diff --name-only "$before" "$after" -- deploy/Caddyfile)" ]; then
  echo "  deploy/Caddyfile changed: run deploy/setup.sh again to install it"
fi

# Running `wl deploy` as root ran python as root here; keep every file the app's.
if [ "$(id -u)" = 0 ]; then chown -R "$user:$user" "$app"; fi

building=$(pgrep -u "$user" -f "cli.py new" || true)
if [ -n "$building" ]; then echo "  chapters being made carry on: pids $(echo "$building" | tr '\n' ' ')"; fi

echo "== restarting"
as_root systemctl restart write-listen
"$app/deploy/check.sh"
as_root systemctl --no-pager --lines=10 status write-listen || true
echo "== deployed $(as_app git log --oneline -1)"
