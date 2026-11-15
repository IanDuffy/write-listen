#!/bin/sh
# wl: the write-listen command. `wl overnight run` builds tonight's chapter; `wl listener ...` manages
# listener profiles; anything else is `python cli.py ...` (e.g. `wl status <run>`). The same command runs
# on this laptop and, later, on the cloud machine.
here="$(cd "$(dirname "$0")" && pwd)"
py="$here/.venv/bin/python"
[ -x "$py" ] || py="$(command -v python3)"
cd "$here" && exec "$py" "$here/cli.py" "$@"
