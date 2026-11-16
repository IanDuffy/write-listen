#!/usr/bin/env bash
# Yesterday's app log, from the journal to /var/log/write-listen/app-YYYY-MM-DD.log (run as root by
# write-listen-log.timer just after midnight). The journal keeps everything too; these files are for
# reading or copying off a day at a time. Keeps 90 days.
set -euo pipefail
dir=/var/log/write-listen
day=$(date -d yesterday +%F)
mkdir -p "$dir"
journalctl -u write-listen.service --since "$day 00:00:00" --until "$(date +%F) 00:00:00" \
  -o short-iso --no-pager > "$dir/app-$day.log"
find "$dir" -name 'app-*.log' -mtime +90 -delete
