"""Fast Start steps 0 and 1: seed -> 01_scout.md (+ 04_research/L00.md, the scout's entries as a row)

One agent with a small budget (config.yaml fast.scout: 6 searches, 3 fetches) finds the way in:
a short sketch of the territory, twelve to eighteen numbered entries, and two or three tellable
scenes. The opening is written from this alone, so the scout's facts have to be citable and
checked like any other. They are therefore filed as a research row, L00, in the row format: the
dossier numbers them D01... at once and runs the word-for-word quote check on them.

The work has two parts, so that step 0 can pay for the first before a run exists:

- the search (paid): the tool loop and, if it pauses at its budget, one write-up call without
  tools (the budget holds the way stages/scout.py's does). It writes 04_research/L00.md,
  L00.sources.json and 01_scout.sources.json. Whatever the loop found is kept in
  01_scout.pending.json until then, so a reply that cannot be filed costs the next try one
  write-up, never a second search.
- the filing (code): the dossier takes L00, and 01_scout.md is written with every reference a
  D number and every entry as the dossier checked it (a quote the check downgraded reads
  `verbatim: no` here too, so nothing downstream plans to quote it).

Step 0 (`early`) runs the search for a topic the guide thinks likely, in a folder of its own
under runs/_scouts/. `adopt` copies a matching early scout into the real run, where `run` then
only has the filing left to do. Only an early scout from the same conversation can match: one
asked for within EARLY_WINDOW_SECONDS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from stages import llm, research
from stages import seed as seed_stage
from stages.common import Run, StageError, entry_id, format_tags, load_prompt, prompt_name, seed_inputs

from . import base

TAG = "scout"
EARLY_DIR = "_scouts"  # under the runs folder; not a run (no status.json), so the server never shows it
EARLY_INFO = "early.json"
ADOPTED = "early_scout.json"  # in the run's fast/ folder: which early scout it took
LOCAL = re.compile(r"\bL0*0\.0*(\d+)\b")  # the scout citing its own entry: 'L00.3'
STALE_SECONDS = 600  # an early scout still "running" after this was killed
EARLY_WINDOW_SECONDS = 2 * 3600  # an early scout older than a conversation can be is no candidate for a new chapter
_EARLY_LOCK = threading.Lock()


# --- the search (paid) ------------------------------------------------------------------

def _search(r: Run, cfg: dict) -> Path:
    """The scout's tool loop -> 04_research/L00.md. Returns at once if the file is there."""
    md, src = research.row_paths(r, base.SCOUT_ROW)
    if md.exists():
        return md
    r.need("seed")
    f = base.settings(cfg)
    searches, fetches = f["scout"]["searches"], f["scout"]["fetches"]
    prompt = load_prompt("fast_scout", **seed_inputs(r, cfg), searches=searches, fetches=fetches,
                         source_tiers=load_prompt(prompt_name(r, "source_tiers")),
                         target_minutes=f["target_minutes"])
    pending = r.root / "01_scout.pending.json"
    if pending.exists():  # an earlier try paid for the tool loop, then its write-up failed: write up only
        saved = json.loads(pending.read_text(encoding="utf-8"))
        print("  fast scout: writing up the search an earlier try paid for")
        got = {"urls": saved.get("urls", {}), "fetched": saved.get("fetched", []), "failed": saved.get("failed", [])}
        n_search, n_fetch, text, cost, paused = (saved.get("searches", 0), saved.get("fetches", 0),
                                                 saved.get("notes", ""), saved.get("cost_usd", 0.0), True)
    else:
        # A paused loop is resumed only if it has used none of its budget: max_uses applies per
        # request, so a resumed request could spend the whole budget again.
        res = llm.call(r, cfg, "fast_scout", role="researcher", label="fast scout",
                       messages=[{"role": "user", "content": prompt}],
                       tools=research.web_tools(cfg, searches, fetches), expected_searches=searches,
                       preapproved=True, resume_if=lambda u: u.web_searches == 0 and u.web_fetches == 0)
        got = research.retrieved(res.content)
        n_search, n_fetch, text, cost, paused = (res.usage.web_searches, res.usage.web_fetches, res.text,
                                                 res.usage.cost_usd, res.paused)
    if paused:  # save what the loop found first, so a failed write-up costs only itself
        base.write_json(pending, {**got, "searches": n_search, "fetches": n_fetch, "notes": text, "cost_usd": cost})
        w = llm.call(r, cfg, "fast_scout_writeup", role="researcher", label="fast scout write-up", preapproved=True,
                     messages=[{"role": "user", "content": load_prompt(
                         "fast_scout_writeup", instructions=prompt, notes=text or "(none)",
                         material=research.material_text(got))}])
        text, cost = w.text, cost + w.usage.cost_usd

    def unusable(why: str) -> StageError:
        """The reply cannot be filed. The search is paid for, so what it found is kept (the write-up
        path saved it before its call): the next try is one write-up without tools, not a second search."""
        if not paused:
            base.write_json(pending, {**got, "searches": n_search, "fetches": n_fetch, "notes": text,
                                      "cost_usd": cost})
        partial = r.root / "_partial_fast_scout.md"
        partial.write_text(text, encoding="utf-8")
        return StageError(f"fast scout: {why}; raw output saved to {partial.name}; the next try writes it up again "
                          "from the saved search.")

    try:
        block = research.extract_tagged(text, TAG)
    except StageError:
        raise unusable(f"no <{TAG}> block") from None
    entries = research.parse_entries(block)
    if not entries:
        raise unusable("the block has no numbered entries, so there is nothing the opening could cite")
    record = {**got, "searches": n_search, "fetches": n_fetch, "budget": {"searches": searches, "fetches": fetches},
              "written_up": paused, "cost_usd": round(cost, 4)}
    base.write_json(r.path("scout_sources"), record)
    # The same pages in a row's sources file: the dossier checks L00's quotes against them.
    base.write_json(src, {**record, "row": base.SCOUT_ROW, "followup": False, "fingerprint": "scout"})
    base.write_atomic(md, block)  # last: this file is what says the search is paid for and done
    pending.unlink(missing_ok=True)
    scenes = len(re.findall(r"^###\s", research._section(block, "Scenes"), re.M))
    print(f"  fast scout: {len(entries)} entries and {scenes} scene(s) from {n_search} of {searches} searches, "
          f"{len(got['fetched'])} page(s) read" + (", written up when its tool loop paused" if paused else "")
          + f", ${cost:.2f}")
    return md


# --- the filing (code) ------------------------------------------------------------------

def _numbering(block: str, numbers: list[int], dossier) -> dict[int, int]:
    """The scout's own entry numbers -> D numbers (L00.3 -> 3 -> D03). The dossier knows; a
    stand-in without refs() numbers in file order, a number used twice keeping its first entry."""
    if hasattr(dossier, "refs"):
        return dict(dossier.refs(base.SCOUT_ROW))
    local = [e["n"] for e in research.parse_entries(block)]
    first = list(dict.fromkeys(local))
    for order in (first, local):
        if len(order) == len(numbers):
            out: dict[int, int] = {}
            for n, d in zip(order, numbers):
                out.setdefault(n, d)
            return out
    raise StageError(f"fast scout: the dossier filed {len(numbers)} entries for {base.SCOUT_ROW}, "
                     f"but 04_research/{base.SCOUT_ROW}.md has {len(first)}.")


def render(block: str, refs: dict[int, int], checked: dict[int, str]) -> str:
    """01_scout.md from the scout's block: the same three sections, citing D numbers only. An
    entry reads as the dossier has it (`checked`: number -> entry line), which is the scout's
    entry after the quote check."""
    def ref(m: re.Match) -> str:
        n = int(m.group(1))
        return entry_id(refs[n]) if n in refs else m.group(0)

    lines, done = [], set()
    for e in research.parse_entries(block):
        d = refs.get(e["n"])
        if d is None or d in done:
            continue
        done.add(d)
        lines.append(checked.get(d) or f"[{entry_id(d)}] {format_tags({**e['tags'], 'rows': [base.SCOUT_ROW]})} "
                                       f"{' '.join(LOCAL.sub(ref, e['body']).split())}")
    part = lambda name: LOCAL.sub(ref, research._section(block, name)).strip()
    return "\n\n".join(["# Scout", "## Territory", part("Territory") or "(the scout wrote no sketch)",
                        "## Entries", *lines,
                        "## Scenes", part("Scenes") or "(the scout found no scene)"]) + "\n"


def run(r: Run, cfg: dict, dossier) -> Path:
    """Seed -> 01_scout.md, with the scout's entries in the dossier as row L00 (D01...). The paid
    search is skipped when 04_research/L00.md is there (an earlier try, or an adopted early
    scout); if 01_scout.md is there and the dossier has L00, nothing is done at all."""
    out = base.path(r, "scout")
    if out.exists() and dossier.has(base.SCOUT_ROW):
        return out
    md, _ = research.row_paths(r, base.SCOUT_ROW)
    if out.exists() and not md.exists():
        raise StageError(f"01_scout.md is there but 04_research/{md.name} is gone, so the scout's entries cannot "
                         "be filed in the dossier. Delete 01_scout.md to scout again.")
    block = _search(r, cfg).read_text(encoding="utf-8")
    numbers = dossier.add_row(base.SCOUT_ROW)
    if not numbers:
        raise StageError(f"fast scout: the dossier filed no entries from 04_research/{md.name}.")
    base.write_atomic(out, render(block, _numbering(block, numbers, dossier), dossier.entries()))
    print(f"  01_scout.md: {entry_id(numbers[0])}-{entry_id(numbers[-1])}")
    return out


def text(r: Run) -> str:
    """01_scout.md: what the outline and the opening are written from."""
    return r.read(base.path(r, "scout"))


# --- step 0: a topic scouted before it is confirmed -------------------------------------

def _norm(topic: str) -> str:
    """A topic for comparing: lower case, words only."""
    return " ".join(re.findall(r"[a-z0-9]+", str(topic).lower().replace("'", "").replace("’", "")))


def _slug(topic: str) -> str:
    """A folder name for a topic: its first words, and a hash of all of it so two topics that
    start alike do not share a folder while the same topic always finds its own."""
    norm = _norm(topic)
    return f"{'-'.join(norm.split()[:6])[:48].strip('-')}-{hashlib.sha256(norm.encode()).hexdigest()[:8]}"


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _running(info: dict | None) -> bool:
    return bool(info) and info.get("state") == "running" and _alive(info.get("pid")) \
        and time.time() - info.get("started", 0) < STALE_SECONDS


def _mark(root: Path, topic: str, state: str, **more) -> None:
    old = base.read_json(root / EARLY_INFO) or {}
    base.write_json(root / EARLY_INFO, {"topic": topic, "started": time.time(), "started_at": base.now_iso(),
                                        **old, "state": state, "pid": os.getpid(), **more})


def early(cfg: dict, runs_dir: Path, topic: str) -> Path:
    """scout_topic (step 0): run the scout's search for a likely topic before it is confirmed, in
    runs_dir/_scouts/<slug>/ (a run folder of its own, unattended, capped at fast.scout_max_usd
    or $1.50). Returns the folder. A topic already scouted, or being scouted by another call,
    costs nothing more. Only the search is done here: the D numbers belong to the run that
    adopts it."""
    topic = " ".join(str(topic).split())
    if not _norm(topic):
        raise StageError("scout_topic needs a topic.")
    r = Run.open(_slug(topic), create=True, runs_dir=Path(runs_dir) / EARLY_DIR)
    r.unattended, r.max_usd = True, base.settings(cfg).get("scout_max_usd", 1.50)
    md, _ = research.row_paths(r, base.SCOUT_ROW)
    t0 = time.time()
    with _EARLY_LOCK:
        if md.exists():  # scouted already, perhaps in an earlier conversation: asked for again now, so current
            _mark(r.root, topic, "done", asked=time.time())
            return r.root
        if _running(base.read_json(r.root / EARLY_INFO)):
            return r.root
        if not r.path("seed").exists():
            base.write_atomic(r.path("seed"), seed_stage.seed_text(
                topic, said="(not recorded: the guide scouted this topic before the listener settled on one)"))
        _mark(r.root, topic, "running", started=t0, started_at=base.now_iso(), error=None)
    try:
        _search(r, cfg)
    except BaseException as e:
        _mark(r.root, topic, "failed", error=f"{type(e).__name__}: {e}")
        raise
    _mark(r.root, topic, "done", seconds=round(time.time() - t0, 1))
    return r.root


def _topic(seed: str) -> str:
    """The topic line of a seed file (prompts/seed_file.md), or the whole text of a hand-written one."""
    m = re.search(r"^##\s+Topic\s*$(.*?)(?=^##\s|\Z)", seed, re.M | re.S)
    return (m.group(1) if m else re.sub(r"^#.*$", "", seed, flags=re.M)).strip()


def _early_scouts(runs_dir: Path) -> list[tuple[Path, dict]]:
    """Early scouts that are done or still going, oldest first. Only those from this conversation:
    started, or asked for again, within EARLY_WINDOW_SECONDS. Nothing here deletes the folders of
    older ones (the caller discards them), so without the window every scout ever made would be
    offered to every later chapter."""
    found = []
    for info_path in sorted((Path(runs_dir) / EARLY_DIR).glob(f"*/{EARLY_INFO}")):
        info, root = base.read_json(info_path) or {}, info_path.parent
        asked = max(info.get("started") or 0, info.get("asked") or 0)
        if info.get("topic") and time.time() - asked <= EARLY_WINDOW_SECONDS \
                and ((root / "04_research" / f"{base.SCOUT_ROW}.md").exists() or _running(info)):
            found.append((root, info))
    return sorted(found, key=lambda x: x[1].get("started", 0))


def _ask_match(r: Run, cfg: dict, seed: str, found: list[tuple[Path, dict]]) -> tuple[Path, dict] | None:
    """One cheap call: which scouted topic, if any, is this chapter's subject in other words?"""
    topics = "\n".join(f"{i}. {info['topic']}" for i, (_, info) in enumerate(found, 1))
    try:
        res = llm.call(r, cfg, "fast_scout_match", role="cheap", label="fast scout match", preapproved=True,
                       messages=[{"role": "user", "content": load_prompt("fast_scout_match", seed=seed.strip(),
                                                                         topics=topics)}])
    except StageError as e:  # reuse is a saving, never a reason to stop the run
        print(f"  fast scout: could not compare with the early scouts ({e}); scouting afresh")
        return None
    # The number only, as the prompt asks. Prose that mentions a number ("None of the 2 topics...",
    # "Topic 2? No: 0.") is no answer: a wrong yes opens the chapter on another subject, a wrong no
    # costs one scout.
    m = re.fullmatch(r"\s*(\d+)\s*\.?\s*", res.text or "")
    k = int(m.group(1)) if m else 0
    return found[k - 1] if 1 <= k <= len(found) else None


def _wait(root: Path, limit: float, sleep) -> bool:
    """Whether an early scout has its findings, waiting for one that is still searching: it is
    already part of the way there, and starting again would cost more time and pay twice."""
    md = root / "04_research" / f"{base.SCOUT_ROW}.md"
    deadline = time.monotonic() + limit
    while not md.exists():
        if not _running(base.read_json(root / EARLY_INFO)) or time.monotonic() >= deadline:
            return md.exists()
        sleep(1)
    return True


def adopt(r: Run, cfg: dict, runs_dir: Path, wait: float | None = None, sleep=time.sleep) -> bool:
    """If an early scout matches this run's seed, copy its search into the run (the L00 files, the
    scout's sources, and its log lines so the cost is counted) and return True; `run` then files
    it without a paid call. Match: the topics are equal after normalising, else one cheap model
    call (stage "fast_scout_match") that answers with the matching topic's number or 0; a reply
    that is anything but a number counts as 0. Only early scouts from this conversation are
    considered (asked for within EARLY_WINDOW_SECONDS). An early scout that matches but is still
    searching is waited for, up to `wait` seconds (fast.scout_wait_seconds, else 150). Unmatched
    early scouts are left alone."""
    marker = base.path(r, "work") / ADOPTED
    md, src = research.row_paths(r, base.SCOUT_ROW)
    if md.exists() or base.path(r, "scout").exists():  # this run has its scout: say where it came from
        return marker.exists()
    found = _early_scouts(Path(runs_dir))
    if not found:
        return False
    seed = r.read("seed")
    topic = _norm(_topic(seed))
    pick = next((x for x in found if _norm(x[1]["topic"]) == topic), None) or _ask_match(r, cfg, seed, found)
    if pick is None:
        return False
    root, info = pick
    if not _wait(root, base.settings(cfg).get("scout_wait_seconds", 150) if wait is None else wait, sleep):
        print(f"  fast scout: the early scout of \"{info['topic']}\" did not finish; scouting afresh")
        return False
    scouted = Run(name=root.name, root=root)
    if not any(rec.get("early_scout") == root.name for rec in r.read_log()):
        for rec in scouted.read_log():
            r.log({**rec, "early_scout": root.name})
    for p in (scouted.path("scout_sources"), research.row_paths(scouted, base.SCOUT_ROW)[1]):
        if p.exists():
            base.write_atomic(r.root / p.relative_to(root), p.read_text(encoding="utf-8"))
    base.write_json(marker, {"from": root.name, "topic": info["topic"], "adopted_at": base.now_iso()})
    base.write_atomic(md, research.row_paths(scouted, base.SCOUT_ROW)[0].read_text(encoding="utf-8"))
    cost = sum(rec.get("cost_usd", 0) or 0 for rec in scouted.read_log())
    r.decide("scout", f"Reused the scout made during the conversation for \"{info['topic']}\" "
                      f"(runs/{EARLY_DIR}/{root.name}, ${cost:.2f}) instead of scouting again.")
    print(f"  fast scout: reusing the early scout of \"{info['topic']}\"")
    return True
