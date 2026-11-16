"""The chapter card for a fast run (Tester Demo, step 4): title, overview and summary, on screen while the guide
and the listener are still talking.

The outline names the chapter about three and a half minutes after start_chapter, too late for a card shown
during the conversation, so the card has its own call at the start: one Sonnet call (prompts/fast_card.md,
modelled on the overnight Up next card) from the seed alone, in a thread of the server beside the run's own
process. It writes <run>/fast/card.json and logs its cost in the run's log.jsonl, so it counts against the
run's cap like any other call. Once 05_outline.json exists, `read` gives the outline's title in place of the
card's (the chapter is called what the outline called it); the overview and summary stay the card's.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from stages import common, llm
from stages.common import Run, StageError, load_prompt, parse_json

from . import base

CARD = "fast/card.json"
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "overview", "summary"],
    "properties": {"title": {"type": "string"}, "overview": {"type": "string"}, "summary": {"type": "string"}},
}
_LOCK = threading.Lock()  # one card call per run at a time (a retried start_chapter must not pay twice)
_BUSY: set[str] = set()


def _topic(seed: str) -> str:
    """The seed's topic line (prompts/seed_file.md), or the whole text of a hand-written seed."""
    m = re.search(r"^##\s+Topic\s*$(.*?)(?=^##\s|\Z)", seed, re.M | re.S)
    return " ".join((m.group(1) if m else re.sub(r"^#.*$", "", seed, flags=re.M)).split())


def write(run_root: Path, cfg: dict) -> dict:
    """The call, synchronously: <run>/fast/card.json, returned."""
    root = Path(run_root)
    r = Run(name=root.name, root=root)
    rcfg = common.run_config(r, cfg)  # the run's own length (a short chapter's config.yaml) and its listener
    r.unattended, r.max_usd = True, base.settings(rcfg).get("max_run_usd")
    seed = r.need("seed").read_text(encoding="utf-8")
    prompt = load_prompt("fast_card", seed=seed.strip(), topic=_topic(seed),
                         minutes=str(base.settings(rcfg)["target_minutes"]))
    res = llm.call(r, rcfg, "fast_card", label="fast card", json_schema=SCHEMA, preapproved=True,
                   messages=[{"role": "user", "content": prompt}])
    card = parse_json(r, res.text, "fast_card")
    out = {k: " ".join(str(card.get(k) or "").split()) for k in ("title", "overview", "summary")}
    missing = [k for k, v in out.items() if not v]
    if missing:
        raise StageError(f"fast card: the model left out the {', '.join(missing)}.")
    out = {**out, "run": root.name, "made_at": base.now_iso()}
    base.write_json(root / CARD, out)
    return out


def _write_quietly(root: Path, cfg: dict) -> None:
    try:
        write(root, cfg)
    except BaseException as e:  # a card that fails costs the page its card, never the chapter or the conversation
        print(f"  chapter card for {root.name} failed: {type(e).__name__}: {e}")
    finally:
        with _LOCK:
            _BUSY.discard(str(root))


def start(run_root: Path, cfg: dict) -> threading.Thread | None:
    """Write the card in a thread and return at once (start_chapter runs under the chat lock). None when the
    run has a card already (a continued run) or one is being written."""
    root = Path(run_root)
    with _LOCK:
        if (root / CARD).exists() or str(root) in _BUSY:
            return None
        _BUSY.add(str(root))
    t = threading.Thread(target=_write_quietly, args=(root, cfg), daemon=True, name="fast-card")
    t.start()
    return t


def read(run_root: Path) -> dict | None:
    """The card as the page shows it, or None before it is written: the outline's title once there is one
    (`outlined` says so)."""
    root = Path(run_root)
    card = base.read_json(root / CARD)
    if not isinstance(card, dict):
        return None
    outline = base.read_json(root / base.FILES["outline_json"]) or {}
    named = " ".join(str(outline.get("title") or "").split()) if isinstance(outline, dict) else ""
    return {"run": root.name, "title": named or card.get("title", ""), "card_title": card.get("title", ""),
            "overview": card.get("overview", ""), "summary": card.get("summary", ""), "outlined": bool(named)}
