"""One lock on the listener's Up next card, across processes (the launchd tick builds and plans; the
server's Change replans in a thread).

    with card_lock(listener): read up_next.json, decide, write it

listeners/<id>/.card.lock, held with fcntl.flock for the few milliseconds of a read-check-write: the
runner while it moves the card to building, the planner around its final write, the reconcile step
when it marks a dead build failed. Re-entrant within a thread (the planner's write reconciles first).
"""

from __future__ import annotations

import fcntl
import threading
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = ".card.lock"
_held = threading.local()


@contextmanager
def card_lock(listener: Path):
    key = str(Path(listener).resolve())
    depth = getattr(_held, "depth", None)
    if depth is None:
        depth = _held.depth = {}
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    with open(Path(listener) / LOCK_NAME, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        depth[key] = 1
        try:
            yield
        finally:
            depth[key] = 0
            fcntl.flock(f, fcntl.LOCK_UN)
