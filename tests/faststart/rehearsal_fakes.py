"""A tiny complete live fast chapter to replay (faststart/rehearsal.py), shaped like a real one: the timeline's
events with their real keys, the scout, research per row, an append-only dossier with a tail, the final ledger,
the outline, sections with drafts and locks; and decoys a replay must never copy."""

from __future__ import annotations

import json
from pathlib import Path

T0 = 1_800_000_000.0

TITLES = {1: "The Still Bird", 2: "Water That Lies", 3: "The River Heron", 4: "A Fourth Part"}
ROWS = {"L01": 2, "L02": 2, "L03": 3}          # row -> section
LANDING = ["L02", "L01", "L03"]                  # research_done order (the dossier's blocks after L00)
ENTRIES = {"L00": [1, 2], "L02": [3, 4], "L01": [5], "L03": [6]}


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _j(p: Path, obj) -> None:
    _w(p, json.dumps(obj, indent=1))


def segment(n: int) -> str:
    return (f"Part {n} begins by a quiet river where a heron stands. [D0{min(n, 6)}] It waits a long time before "
            f"it moves at all.\n\nThen part {n} goes on: the water bends the light, and the bird must allow for "
            f"it. [D01] Nobody taught it that in so many words.")


def events(sections: int) -> list[dict]:
    """The source's timeline, as faststart/run.py writes it (key "event")."""
    rows = {k: v for k, v in ROWS.items() if v <= sections}
    ev = [(0.1, "seed_set", {"cap_usd": 4.0}),
          (10.0, "scout_done", {"seconds": 9.9, "adopted": False, "entries": 2}),
          (30.0, "outline_done", {"seconds": 20.0, "title": "What the Heron Does", "sections": sections,
                                  "rows": len(rows)})]
    ev += [(30.0, "research_start", {"row": k, "section": v}) for k, v in rows.items()]
    ev += [(30.0, "opening_start", {"section": 1}),
           (31.0, "listener_note", {"kind": "question", "section": 0, "text": "grey herons?", "said": "x"}),
           (50.0, "opening_done", {"section": 1, "seconds": 20.0, "words": 40}),
           (50.0, "narrate_start", {"section": 1}),
           (50.0, "piece_ready", {"section": 1, "index": 0, "seconds": 5.0, "after": 0.0, "last": False,
                                  "simulated": False}),
           (50.0, "first_audio", {"section": 1}),
           (50.0, "narrate_done", {"section": 1, "seconds": 0.0, "pieces": 2, "audio_seconds": 10.0,
                                   "resumed": False}),
           (55.0, "playback_start", {}),
           (55.0, "section_needed", {"section": 1, "state": "playing"})]
    t = 60.0
    for row in [x for x in LANDING if x in rows]:
        ev.append((t, "research_done", {"row": row, "section": rows[row], "seconds": t - 30, "entries": 2,
                                        "thin": None, "error": None, "searches": 3, "paid": True}))
        t += 2.0
    t = 66.0
    for n in range(2, sections + 1):
        ev += [(t, "draft_start", {"section": n}), (t + 10, "draft_done", {"section": n, "seconds": 10.0, "words": 40})]
        t += 10
        if n > 2:
            ev += [(t, "lock_start", {"section": n - 1, "mode": "paired"}),
                   (t + 10, "lock_done", {"section": n - 1, "mode": "paired", "seconds": 10.0, "words": 40,
                                          "moved": 0, "cut": 0, "problems": 0, "corrections": 0, "title": None}),
                   (t + 10, "narrate_start", {"section": n - 1}),
                   (t + 10, "narrate_done", {"section": n - 1, "seconds": 0.0, "pieces": 2, "audio_seconds": 10.0,
                                             "resumed": False})]
            t += 10
    last = sections
    if last >= 2:
        ev += [(t, "lock_start", {"section": last, "mode": "final"}),
               (t + 10, "lock_done", {"section": last, "mode": "final", "seconds": 10.0, "words": 40, "moved": 0,
                                      "cut": 0, "problems": 0, "corrections": 0, "title": None}),
               (t + 10, "narrate_start", {"section": last}),
               (t + 10, "narrate_done", {"section": last, "seconds": 0.0, "pieces": 2, "audio_seconds": 10.0,
                                         "resumed": False})]
        t += 10
    ev += [(t, "archive_start", {}),
           (t, "archive_done", {"seconds": 0.0, "audio_seconds": 60.0, "words": 120, "sections": sections,
                                "timing": None, "episode": False}),
           (t, "ready", {"words": 120, "audio_seconds": 60.0, "holds": 0, "held_seconds": 0, "solo": [],
                         "cost_usd": 1.23})]
    return [{"t": t_, "ts": "2026-11-14T00:00:00-05:00", "event": name, **f} for t_, name, f in ev]


def entry(d: int, row: str, kind: str = "claim", tier: int = 2) -> str:
    return (f"[D{d:02d}] ({row}, {kind}, tier {tier}) A grey heron in a river study waited on average twenty "
            f"seconds between strikes at fish in shallow water. Source: https://example.org/{row.lower()}/{d}")


def make_source(dirpath: Path, sections: int = 3) -> Path:
    root = Path(dirpath)
    root.mkdir(parents=True, exist_ok=True)
    rows = {k: v for k, v in ROWS.items() if v <= sections}
    _j(root / "fast.json", {"mode": "fast", "seed_set": T0 - 86400, "seed_set_at": "2026-11-14T00:00:00-05:00",
                            "voice": "live"})
    _w(root / "00_source" / "seed.md", "# Seed\n\n## Topic\n\nherons\n")
    _w(root / "fast_timeline.jsonl", "".join(json.dumps(e) + "\n" for e in events(sections)))
    _w(root / "01_scout.md", "# Scout\n\n[1] Herons bait fish.\n")
    _j(root / "01_scout.sources.json", [{"url": "https://example.org/scout"}])
    for row in ["L00", *rows]:
        _w(root / "04_research" / f"{row}.md", f"# {row}: findings\n\n<research>\n## Entries\n</research>\n")
        _j(root / "04_research" / f"{row}.sources.json", [{"url": f"https://example.org/{row.lower()}"}])
    blocks = []
    for row in ["L00", *[x for x in LANDING if x in rows]]:
        head = "what the scout found" if row == "L00" else f"To settle: what row {row} asks about herons."
        body = "\n\n".join(entry(d, row, tier=3 if row == "L00" else 2) for d in ENTRIES[row])
        blocks.append(f"### {row}: {head}\n\n{body}")
    _w(root / "04_dossier.md", "# Dossier: What the Heron Does\n\nBuilt by code as each row landed.\n\n## Entries\n\n"
       + "\n\n".join(blocks) + "\n\n## Not found\n\n- nothing on egrets\n\n## Sources cited\n\n- example.org\n")
    _j(root / "fast" / "dossier_index.json", {"rows": [{"id": r, "entries": ENTRIES[r]} for r in ["L00", *rows]]})
    outline = {"title": "What the Heron Does",
               "angle": "A heron's stillness is the work of the hunt. While it stands, it solves the water.",
               "why": "they watched herons", "through_line": "What is a heron doing while it stands so still?",
               "closing_idea": "SECRET ENDING the stillness was the strike all along",
               "listener_words": "herons by the river",
               "sections": [{"n": n, "title": TITLES[n], "lands": f"This section settles part {n} of the hunt.",
                             "sets_up": "next", "hand_off": f"SECRET HANDOFF {n}", "target_words": 300,
                             "notes": "moves", "scout_entries": [1],
                             "research_questions": []} for n in range(1, sections + 1)]}
    _j(root / "05_outline.json", outline)
    _w(root / "05_outline.md", "# Outline\n")
    _w(root / "03_angle.md", "# Angle: What the Heron Does\n")
    _w(root / "05_brief.md", "## Segment 1: The Still Bird\n")
    _j(root / "fast" / "outline_first.json", outline)
    _j(root / "fast" / "outline_edit.json", {"ok": True})
    led_rows = []
    for i, (row, sec) in enumerate(rows.items(), 1):
        led_rows.append({"id": row, "rank": i, "idea": f"Herons and water, part {sec}; to settle: what {row} found "
                                                       "about how herons correct for the bent light at the surface.",
                         "type": "deep-dive", "episode_has": "", "research_question": f"What did {row} measure?",
                         "tier_needed": 2, "status": "filled", "dossier_refs": [f"D{d:02d}" for d in ENTRIES[row]],
                         "best_tier": 2, "gap": "", "followup": False, "coverage": "covered", "segments": [sec],
                         "note": f"used in part {sec}", "section": sec})
    _j(root / "ledger.json", {"run": "source", "source": "seed", "inputs": "abc", "rows": led_rows, "parked": [],
                              "connections": []})
    for n in range(1, sections + 1):
        _w(root / "06_segments" / f"seg{n:02d}.md", segment(n))
        _w(root / "06_segments" / "drafts" / f"seg{n:02d}.d1.md", segment(n) + " A draft line.")
        _j(root / "fast" / "locks" / f"seg{n:02d}.json", {"n": n, "mode": "opening" if n == 1 else "paired",
                                                          "text": segment(n), "title": None})
    # decoys: never copied
    _w(root / "log.jsonl", json.dumps({"stage": "fast_research", "cost_usd": 0.5}) + "\n")
    _j(root / "cost.json", {"total": 1.23})
    _j(root / "status.json", {"state": "ready", "run": root.name})
    _j(root / "fast" / "player.json", {"at": T0, "state": "done"})
    _j(root / "fast" / "archive.json", {"live": True, "estimated": False})
    _w(root / "listener_notes.md", "- grey herons?\n")
    _w(root / "live" / "x.wav", "RIFF")
    _w(root / "12_asks" / "asides.jsonl", "{}\n")
    _w(root / "10_audio" / "chapter.mp3", "ID3")
    return root


def drop_event(root: Path, name: str, **match) -> None:
    """Remove the first matching event from a source's timeline (an incomplete source)."""
    p = Path(root) / "fast_timeline.jsonl"
    lines = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    i = next(i for i, e in enumerate(lines) if e["event"] == name and all(e.get(k) == v for k, v in match.items()))
    del lines[i]
    p.write_text("".join(json.dumps(e) + "\n" for e in lines))


class Clock:
    """A fake clock and its sleep: sleeping moves the clock. raise_on: the sleep call that raises Ctrl-C."""

    def __init__(self, t: float = T0, raise_on: int | None = None, until=None):
        self.t, self.calls, self.raise_on, self.until = t, 0, raise_on, until

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.calls += 1
        if self.raise_on is not None and self.calls >= self.raise_on:
            raise KeyboardInterrupt
        if self.until is not None and self.until():
            raise KeyboardInterrupt
        self.t += s


def make_run(runs: Path, name: str, src: Path, cfg: dict, t0: float = T0):
    """A run folder as the server's start_seed leaves it for a rehearsal: the seed, fast.json read live with
    its seed_set, the marker and the card."""
    from faststart import rehearsal
    from stages.common import Run

    r = Run.open(name, create=True, runs_dir=runs)
    _w(r.root / "00_source" / "seed.md", "# Seed\n\n## Topic\n\nsomething else entirely\n")
    _j(r.root / "fast.json", {"mode": "fast", "seed_set": t0, "seed_set_at": "2026-11-20T00:00:00-05:00",
                              "voice": "live"})
    rehearsal.mark(r, src, cfg)
    rehearsal.card(r, src)
    r.unattended = True
    return r
