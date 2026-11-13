"""The server-side decisions of the check-in (docs/fast-start.md, "When a section isn't ready").

The page does the rest (the voice session, the mic, the silence timer). Pure code.
"""

from __future__ import annotations


def decide(eta: float | None, short_wait: float = 15) -> str:
    """What happens when the listener reaches a section that is not ready: "pause" or "check_in".

    A section expected within about 15 seconds (fast.short_wait_seconds) is just a pause: the
    circle shows the making state and nothing else happens. Otherwise the guide comes in, and
    also when there is no estimate: a guess that turned out wrong would leave the listener in
    silence, which is the one thing the check-in exists to prevent."""
    if eta is None:
        return "check_in"
    return "pause" if eta <= short_wait else "check_in"


def next_marker(current: int, last: int) -> int | None:
    """Skipping goes to the next section marker only (acceptance check 6): the section after
    the one the listener is in, or None on the last section, where there is nowhere to go."""
    return current + 1 if current < last else None
