#!/usr/bin/env bash
# After a (re)start: is the app answering, and is tester mode on? Run by setup.sh and deploy.sh.
# With tester mode on, a visitor without a tester cookie gets only the blank page: /api/status answers
# 404. If it answers 200 the site is the full app, open to anyone with the address, so this stops it.
set -euo pipefail
url="http://127.0.0.1:${WL_PORT:-8765}"

code=000
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url/" || true)
  [ "$code" = 200 ] && break
  sleep 1
done
if [ "$code" != 200 ]; then
  echo "The app is not answering on $url (last answer: $code). See: journalctl -u write-listen -n 50" >&2
  exit 1
fi
echo "  the app answers on $url"

status=$(curl -s -o /dev/null -w '%{http_code}' "$url/api/status" || true)
if [ "$status" != 404 ]; then
  echo "Tester mode is OFF (/api/status answered $status to a visitor with no link)." >&2
  echo "Stopping the app so it isn't open to anyone. Set WL_DEMO=1 in /etc/write-listen/env, then wl deploy." >&2
  if [ "$(id -u)" = 0 ]; then systemctl stop write-listen; else sudo systemctl stop write-listen; fi
  exit 1
fi
echo "  tester mode is on (a visitor without a link sees only the dot)"
