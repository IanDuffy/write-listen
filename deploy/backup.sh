#!/bin/bash
# Copy the droplet's listener folders (and shared/, which holds the tester links) to this Mac: one
# dated folder a night under ~/write-listen-backups/. Run from the Mac, by hand or nightly by launchd
# (deploy/local.write-listen.backup.plist; DEPLOY.md). Unchanged files are hard links to the night
# before, so a night costs only what changed.
#
#     WL_DROPLET=root@listen.ianduffy.com bash deploy/backup.sh
set -euo pipefail
host="${WL_DROPLET:-root@listen.ianduffy.com}"
dest="${WL_BACKUPS:-$HOME/write-listen-backups}"
day=$(date +%Y-%m-%d)

for dir in listeners shared; do
  mkdir -p "$dest/$dir"
  # The newest earlier night, if any, to link unchanged files against.
  prev=$(ls -1d "$dest/$dir"/20??-??-?? 2>/dev/null | grep -v "/$day\$" | tail -n 1 || true)
  # BatchMode: under launchd nobody can answer a prompt, so fail rather than hang.
  rsync -az --delete -e "ssh -o BatchMode=yes" ${prev:+--link-dest="$prev"} \
    "$host:/srv/write-listen/$dir/" "$dest/$dir/$day/"
done
echo "$(date '+%F %T') backed up to $dest/{listeners,shared}/$day"
