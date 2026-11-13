"""The harness for the end-to-end checks of docs/fast-start.md (9, 10 and 12), and the report on a run.

    python -m faststart e2e --seed "..." [--why "..."] [--narrate real|first|sim] [--skip-at 120]
                            [--remarks remarks.json] [--max-usd N] [--name RUN] [--no-episode]
    python -m faststart overnight --seed "..." [--why "..."] [--max-usd N] [--name RUN]
    python -m faststart report <run> [--json]
    python -m faststart notes-use <run>
    python -m faststart spend
    python -m faststart record <run> [--what overnight]

`e2e` is one live run: real model calls, and a simulated listener (faststart/player.py) who
starts when the first audio is ready and plays in real time. With --skip-at the listener taps
skip that many seconds in, which forces a wait; with --remarks a scripted listener says a few
things at the first check-in, saved as listener notes the way the guide's add_note saves them.
Narration can be real, real for the first piece only, or simulated (silence of the right length,
returned after as long as ElevenLabs would take), so the timeline stays true while few or no
characters are spent. The listener's memory files go to a folder inside the run, never to
listener/. Both settings are written to the run's own config.yaml, which every command reads
(stages.common.run_config): a stopped run continued another way (cli.py, the server's Try
again) stays a build test, and `e2e --name RUN` continues it as it began. Before it starts,
the command prints the ElevenLabs balance and what the run will use, and refuses a run whose
real narration does not fit.

`overnight` makes the other script of check 11: the existing unattended pipeline through its
revise stage, with no narration (the comparison reads the script and the dossier), in a
folder of its own, under the build's cap.

Every live run is paid for out of the build's own cap (fast.build_cap_usd): the run is refused
if the build's spend so far plus the run's cap would pass it. faststart/build_costs.jsonl gets
a line when a run starts and one for each paid step; a step's line is what the run's log holds
that the build log does not yet, so what a killed try spent is counted by the next step, and
by `spend` and the cap even if there is none. A run made another way is added with `record`.

`report` turns a run's timeline into the tables the checks ask for; `notes-use` lists, for each
listener note, the lines of later sections that share its distinctive words.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path

import yaml

from stages import common, seed, tts
from stages.common import PATHS, Run, StageError, word_count
from stages.report import cost_table

from . import base, sections, status
from . import run as fast_run
from . import timings as T
from .player import SimPlayer

COSTS = Path(__file__).resolve().parent / "build_costs.jsonl"
NARRATE = ("real", "first", "sim")
MONEY = ("claude_usd", "elevenlabs_chars", "elevenlabs_usd", "cost_usd")
STARTED = "started"  # a line's state when a run begins: it names the run and its folder, and costs nothing


# --- the build's money ------------------------------------------------------------------

def run_costs(r: Run) -> dict:
    """What a run's log adds up to: Claude dollars, ElevenLabs characters and dollars."""
    log = r.read_log()
    voice = [x for x in log if x.get("provider") == "elevenlabs"]
    total = sum(x.get("cost_usd", 0) or 0 for x in log)
    el = sum(x.get("cost_usd", 0) or 0 for x in voice)
    return {"claude_usd": round(total - el, 4), "elevenlabs_chars": sum(x.get("characters", 0) or 0 for x in voice),
            "elevenlabs_usd": round(el, 4), "cost_usd": round(total, 4)}


def cost_lines() -> list[dict]:
    if not COSTS.exists():
        return []
    return [json.loads(x) for x in COSTS.read_text(encoding="utf-8").splitlines() if x.strip()]


def recorded(name: str, lines: list[dict] | None = None) -> dict:
    """What the build log already holds for one run, all its steps together."""
    mine = [x for x in (cost_lines() if lines is None else lines) if x.get("run") == name]
    return {k: round(sum(x.get(k, 0) or 0 for x in mine), 4) for k in MONEY}


def build_spend() -> dict:
    """The build's total so far: the lines of faststart/build_costs.jsonl, plus, for each run the
    file names whose folder is still there, what its log holds beyond its lines (a try that was
    killed before it could write its line, or continued by another command)."""
    lines = cost_lines()
    out = {k: sum(x.get(k, 0) or 0 for x in lines) for k in MONEY}
    folders: dict[str, Path] = {}
    for x in lines:
        if x.get("run"):
            folders[x["run"]] = Path(x["dir"]) if x.get("dir") else folders.get(x["run"], common.RUNS_DIR / x["run"])
    for name, root in folders.items():
        if (root / PATHS["log"]).exists():
            now, had = run_costs(Run(name=name, root=root)), recorded(name, lines)
            for k in MONEY:
                out[k] += max(0.0, now[k] - had[k])
    return {"lines": sum(1 for x in lines if x.get("state") != STARTED), **{k: round(v, 4) for k, v in out.items()}}


def check_build_cap(cfg: dict, ahead: float, what: str) -> None:
    """Refuse a paid step that could take the build past its cap."""
    cap, spent = base.settings(cfg)["build_cap_usd"], build_spend()["cost_usd"]
    if spent + ahead > cap:
        raise StageError(f"{what} could cost up to ${ahead:.2f}, and the build has spent ${spent:.2f} of its "
                         f"${cap:.2f} cap (fast.build_cap_usd; {COSTS.name}). Not started. A lower --max-usd may fit.")


def _append(line: dict) -> dict:
    COSTS.parent.mkdir(parents=True, exist_ok=True)
    with open(COSTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def record_start(what: str, r: Run, **more) -> None:
    """Name a run in the build log before it spends anything (once): from then on build_spend
    counts whatever its log holds, whether or not the command lives to write its own line."""
    if not any(x.get("run") == r.name for x in cost_lines()):
        _append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": what, "run": r.name, "dir": str(r.root), **more,
                 "state": STARTED, **{k: 0 for k in MONEY}, "seconds": 0})


def record_costs(what: str, r: Run, seconds: float, **more) -> dict:
    """One line for one paid step: what the run's log holds that the build log does not yet. So a
    run continued by a second command is not counted twice, and what a killed try, or a
    continuation by another command, spent without a line is caught up here."""
    after, had = run_costs(r), recorded(r.name)
    return _append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": what, "run": r.name, "dir": str(r.root),
                    **more, **{k: round(after[k] - had[k], 4) for k in MONEY}, "seconds": round(seconds)})


# --- the scripted listener --------------------------------------------------------------

class ScriptedListener:
    """Says its remarks at the first check-in, `gap` seconds apart, as listener notes (acceptance
    check 10). run() calls on_check_in from its loop, so the remarks after the first are said from
    a thread of this object's own; join() it when the run is over."""

    def __init__(self, remarks: list[dict], gap: float = 4.0, sleep=time.sleep):
        self.remarks, self.gap, self.sleep = list(remarks), gap, sleep
        self.said: list[dict] = []
        self.at: dict | None = None
        self.thread: threading.Thread | None = None

    def on_check_in(self, now: float, section: int, add_note) -> None:
        if self.at is not None or not self.remarks:
            return
        self.at = {"now": now, "waiting_for": section}

        def say() -> None:
            for i, remark in enumerate(self.remarks):
                if i and self.gap > 0:
                    self.sleep(self.gap)
                self.said.append(add_note(remark["text"], remark.get("kind")))

        if self.gap > 0 and len(self.remarks) > 1:
            self.thread = threading.Thread(target=say, name="fast-listener", daemon=True)
            self.thread.start()
        else:
            say()

    def join(self, timeout: float | None = None) -> None:
        if self.thread is not None:
            self.thread.join(timeout)


def load_remarks(path: str) -> list[dict]:
    """A JSON list of {"kind": "landed" | "unclear" | "connection" | "wants more" | "question", "text": "..."}."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise StageError(f"--remarks {path}: {e}") from None
    if not isinstance(data, list) or not all(isinstance(x, dict) and str(x.get("text", "")).strip() for x in data):
        raise StageError(f"--remarks {path}: a JSON list of {{\"kind\": ..., \"text\": ...}} is needed.")
    return [{"kind": x.get("kind"), "text": x["text"]} for x in data]


# --- one live run -----------------------------------------------------------------------

def _simulate_after(mode: str, cfg: dict) -> int | None:
    """fast.narration.simulate_after_chars for a narrate mode: how many real characters come first."""
    return {"real": None, "first": base.settings(cfg)["narration"]["first_piece_max_chars"], "sim": 0}[mode]


def _own_settings(r: Run) -> dict:
    p = r.root / "config.yaml"
    over = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    if not isinstance(over, dict):
        raise StageError(f"{p}: expected a mapping of settings.")
    return over


def narrate_mode(r: Run, asked: str | None) -> str:
    """The narrate mode a run to be continued keeps: the one it was started with (its config.yaml),
    else what its pieces show. Once it has pieces, a different one asked for is refused: the
    chapter would be part voice and part silence, and silence already narrated is never
    narrated again."""
    began = ((_own_settings(r).get("fast") or {}).get("narration") or {}).get("build_test")
    metas = [m for p in r.path("audio_dir").glob("sec*/p*.json") if (m := base.read_json(p))]
    if began not in NARRATE:
        began = "sim" if any(m.get("simulated") for m in metas) else "real" if metas else None
    if asked and began and asked != began and metas:
        raise StageError(f"{r.name} was started with --narrate {began}; continuing it with --narrate {asked} would "
                         "make a chapter that is part voice and part silence. Leave --narrate out to continue "
                         "it as it began.")
    return asked or began or "sim"


def keep_settings(r: Run, cfg: dict, mode: str, listener_dir: str | None = None) -> Path:
    """Write a build test's settings into its run folder (config.yaml, merged for every command by
    stages.common.run_config): the narrate mode, and a listener folder inside the run (a build
    run is not a chapter Ian asked for). They then hold however the run is continued."""
    over = _own_settings(r)
    narration = over.setdefault("fast", {}).setdefault("narration", {})
    narration["build_test"] = mode
    narration["simulate_after_chars"] = _simulate_after(mode, cfg)
    if listener_dir or not over.get("listener_dir"):
        over["listener_dir"] = str(Path(listener_dir).expanduser().resolve() if listener_dir else r.root / "listener")
    return base.write_atomic(r.root / "config.yaml",
                             "# A build test (python -m faststart e2e): kept with the run, so any command that continues "
                             "it narrates\n# the same way and keeps its listener files here.\n"
                             + yaml.safe_dump(over, sort_keys=False, allow_unicode=True))


def voice_room(cfg: dict, mode: str, r: Run | None = None) -> tuple[int, int | None]:
    """(real ElevenLabs characters the run may use at the upper end, characters left on the
    account this month, or None if the balance cannot be read), printed, and refused when the
    run does not fit: a chapter cut off for want of characters is paid for and cannot be heard."""
    view = copy.deepcopy(cfg)
    base.settings(view)["narration"]["simulate_after_chars"] = _simulate_after(mode, view)
    ahead, got = fast_run.chars_ahead(view, r), fast_run.read_balance()
    left = None if got is None else max(0, got[1] - got[0])
    have = "the balance could not be read" if got is None else f"{left:,} of {got[1]:,} characters left this month"
    use = f"up to {ahead:,}" if ahead else "none"
    print(f"  ElevenLabs: {have}; this run (narration {mode}) will use {use}.")
    if left is not None and ahead > left:
        raise StageError(f"Not enough narration characters this month for --narrate {mode}: the run may use up to "
                         f"{ahead:,} and {left:,} are left. Not started. --narrate sim uses none.")
    return ahead, left


def e2e(cfg: dict, *, seed_text: str | None = None, why: str = "", name: str | None = None,
        max_usd: float | None = None, narrate: str | None = None, skip_at: float | None = None,
        remarks: list[dict] | None = None, remark_gap: float = 4.0, listener_dir: str | None = None,
        episode: bool = True, clock=time.time) -> dict:
    """One end-to-end fast run with a simulated listener. Returns run()'s result with "run_dir",
    "costs" (the line added to build_costs.jsonl) and "report" (the tables, as Markdown).
    `narrate` None: sim for a new run, and for a run that is continued the mode it began with."""
    if narrate is not None and narrate not in NARRATE:
        raise StageError(f"--narrate is one of {', '.join(NARRATE)}.")
    runs_dir = common.RUNS_DIR
    cap = max_usd if max_usd is not None else base.settings(cfg)["max_run_usd"]
    if name and (runs_dir / name / "00_source" / "seed.md").exists():
        r = Run.open(name, runs_dir=runs_dir)
        fast_run.check_folder(r)  # before anything is written into it
        mode = narrate_mode(r, narrate)
        record_start("e2e", r)  # a run made another way: from here its whole log counts against the build
        check_build_cap(cfg, max(0.0, cap - r.spent()), f"Continuing {r.name}")
        voice_room(cfg, mode, r)
        print(f"  continuing {r.name}")
    elif seed_text:
        mode = narrate or "sim"
        check_build_cap(cfg, cap, "This run")  # before anything is created
        voice_room(cfg, mode)
        r = Run.open(name or seed.run_name(seed_text, runs_dir), create=True, runs_dir=runs_dir)
        seed.write(r, seed_text, why)
        record_start("e2e", r, seed=seed_text[:120], narrate=mode)
    else:
        raise StageError("e2e needs --seed \"...\", or --name of a run to continue.")
    keep_settings(r, cfg, mode, listener_dir)
    cfg = common.run_config(r, cfg)
    f = base.settings(cfg)
    r.unattended, r.max_usd = True, cap
    listener = ScriptedListener(remarks, remark_gap) if remarks else None
    player = SimPlayer(skips=[skip_at] if skip_at is not None else [])
    t0, state = time.monotonic(), "problem"
    print(f"  fast run {r.name}: narration {mode}, cap ${r.max_usd:.2f}"
          + (f", a skip {skip_at:.0f} s into listening" if skip_at is not None else "")
          + (f", {len(remarks)} remark(s) at the first check-in" if remarks else ""))
    try:
        result = fast_run.run(r, cfg, player=player, listener=listener, clock=clock, episode=episode,
                              again=f"python -m faststart e2e --name {r.name}")
        state = "ready"
    finally:
        if listener is not None:
            listener.join(remark_gap * len(listener.remarks) + 5)
        costs = record_costs("e2e", r, time.monotonic() - t0, seed=(seed_text or "")[:120], narrate=mode, state=state)
        print(f"  this run: ${costs['cost_usd']:.2f} (Claude ${costs['claude_usd']:.2f}, ElevenLabs "
              f"{costs['elevenlabs_chars']:,} characters ${costs['elevenlabs_usd']:.2f}); the build so far: "
              f"${build_spend()['cost_usd']:.2f} of ${f['build_cap_usd']:.2f}")
    return {**result, "run_dir": str(r.root), "costs": costs, "report": render(report(r, cfg))}


# --- the other script of check 11 -------------------------------------------------------

def overnight(cfg: dict, *, seed_text: str | None = None, why: str = "", name: str | None = None,
              max_usd: float | None = None) -> dict:
    """The overnight pipeline for one seed, as far as the script: every stage of the unattended
    run (stages.new.STEPS, unchanged) up to its narration, which the comparison does not read.
    Always in a folder of its own (never one a fast run for the same seed is using), unattended
    under the run's cap, refused if the build's cap could be passed, and recorded in the build
    log whatever happens. Returns {"run", "run_dir", "script", "costs"}."""
    from stages import new

    runs_dir = common.RUNS_DIR
    cap = max_usd if max_usd is not None else cfg["unattended"]["max_run_usd"]
    if name and (runs_dir / name / "00_source" / "seed.md").exists():
        r = Run.open(name, runs_dir=runs_dir)
        if base.is_fast(r):
            raise StageError(f"{r.name} is a fast run: the overnight run needs a folder of its own (another --name).")
        record_start("overnight", r)
        check_build_cap(cfg, max(0.0, cap - r.spent()), f"Continuing {r.name}")
        print(f"  continuing {r.name}")
    elif seed_text:
        check_build_cap(cfg, cap, "The overnight run")
        r = Run.open(name or seed.run_name(seed_text, runs_dir), create=True, runs_dir=runs_dir)
        seed.write(r, seed_text, why)
        record_start("overnight", r, seed=seed_text[:120])
    else:
        raise StageError("overnight needs --seed \"...\", or --name of a run to continue.")
    if not (r.root / "config.yaml").exists():  # the same empty listener a fast build test is written for
        base.write_atomic(r.root / "config.yaml", yaml.safe_dump({"listener_dir": str(r.root / "listener")}))
    cfg = common.run_config(r, cfg)
    r.unattended, r.max_usd = True, cap
    r.decide("start", f"The overnight pipeline as far as the script (no narration), for the comparison with a fast "
                      f"run (docs/fast-start.md, check 11). Cap ${cap:.2f}, ${r.spent():.2f} spent before this.")
    t0, state = time.monotonic(), "problem"
    try:
        for label, key, stage, _ in new.STEPS:
            if label == "narrate":
                break
            if r.path(key).exists():
                continue
            print(f"\n== {label} ==")
            try:
                stage(r, cfg)
            except StageError as e:
                raise StageError(f"stopped at {label}: {e}\n  To continue: python -m faststart overnight --name "
                                 f"{r.name} (raise --max-usd if the cap stopped it).") from None
        state = "script"
    finally:
        costs = record_costs("overnight", r, time.monotonic() - t0, seed=(seed_text or "")[:120], state=state)
        print(f"  this run: ${costs['cost_usd']:.2f}; the build so far: ${build_spend()['cost_usd']:.2f} of "
              f"${base.settings(cfg)['build_cap_usd']:.2f}")
    return {"run": r.name, "run_dir": str(r.root), "script": str(r.path("script_final")), "costs": costs}


# --- the report -------------------------------------------------------------------------

def _first(events: list[dict], name: str, **match) -> dict | None:
    return next((e for e in events if e["event"] == name and all(e.get(k) == v for k, v in match.items())), None)


def _t(e: dict | None) -> float | None:
    return None if e is None else e["t"]


def report(r: Run, cfg: dict) -> dict:
    """A run's timeline as data: the start (seed to scout, outline, first audio), each section
    (when it was ready against when playback needed it), each hold, the solo edits, the ledger's
    coverage, the cost by stage, words and minutes."""
    events = base.read_timeline(r)
    if not events:
        raise StageError(f"{r.name} has no {base.FILES['timeline']}: it is not a fast run, or it never started.")
    outline = base.read_json(base.path(r, "outline_json")) or {"sections": []}
    short = base.settings(cfg)["short_wait_seconds"]
    changed = status.titles(r, outline)
    one = lambda name, **m: _first(events, name, **m)

    start = {"scout": _t(one(T.SCOUT_DONE)), "outline": _t(one(T.OUTLINE_DONE)), "first_audio": _t(one(T.FIRST_AUDIO)),
             "playback": _t(one(T.PLAYBACK_START)),
             "scout_seconds": (one(T.SCOUT_DONE) or {}).get("seconds"),
             "scout_adopted": bool((one(T.SCOUT_DONE) or {}).get("adopted")),
             "outline_seconds": (one(T.OUTLINE_DONE) or {}).get("seconds"),
             "opening_seconds": (one(T.OPENING_DONE) or {}).get("seconds"),
             "continued": sum(1 for e in events if e["event"] == T.CONTINUED)}

    holds, open_holds = [], {}
    for e in events:
        if e["event"] == T.HOLD_START:
            holds.append({"section": e["section"], "start": e["t"], "seconds": None, "reason": e.get("reason"),
                          "decision": None, "solo": False, "covered_by": None})
            open_holds[e["section"]] = holds[-1]
        elif e["event"] == T.CHECK_IN and e["section"] in open_holds:
            open_holds[e["section"]]["decision"] = e["decision"]
        elif e["event"] == T.HOLD_END and e["section"] in open_holds:
            h = open_holds.pop(e["section"])
            h.update(seconds=e["seconds"], solo=bool(e.get("solo")), decision=e.get("decision") or h["decision"],
                     covered_by="check-in" if e["seconds"] > short else "pause")

    listed = []
    for s in outline["sections"]:
        n = s["n"]
        text = sections.locked_text(r, n)
        lock = one(T.OPENING_DONE) if n == 1 else one(T.LOCK_DONE, section=n)
        ready, needed = one(T.PIECE_READY, section=n, index=0), one(T.SECTION_NEEDED, section=n)
        narrated = one(T.NARRATE_DONE, section=n)
        margin = None if ready is None or needed is None else round(needed["t"] - ready["t"], 1)
        listed.append({
            "n": n, "title": changed.get(n) or s["title"], "words": word_count(text) if text else None,
            "drafted": _t(one(T.OPENING_DONE) if n == 1 else one(T.DRAFT_DONE, section=n)),
            "locked": _t(lock), "mode": "opening" if n == 1 else (lock or {}).get("mode"),
            "ready": _t(ready), "narrated": _t(narrated), "audio_seconds": (narrated or {}).get("audio_seconds"),
            "needed": _t(needed), "projected": bool((needed or {}).get("projected")), "margin": margin,
            "waited": round(sum(h["seconds"] or 0 for h in holds if h["section"] == n), 1)})

    led = base.read_json(r.path("ledger")) or {"rows": []}
    rows = [{"id": row["id"], "section": row.get("section"), "type": row.get("type"), "idea": row.get("idea"),
             "research": row.get("status"), "entries": len(row.get("dossier_refs") or []),
             "coverage": row.get("coverage"), "note": row.get("note") or row.get("gap") or "",
             "from_note": bool(row.get("from_note"))} for row in led.get("rows", []) if isinstance(row, dict)]

    by_stage, total = cost_table(r.read_log())
    episode_dir = r.root / "11_episode"
    ep_log = Run(name="episode", root=episode_dir).read_log() if (episode_dir / "log.jsonl").exists() else []
    ep_stages, ep_total = cost_table(ep_log)
    cost = [{"stage": st, "calls": calls, "usd": round(usd, 4)} for st, calls, usd in by_stage if st != "fast_archive"]
    # The archive's line in the run's log is the episode build's total; its calls are in 11_episode/log.jsonl.
    cost += [{"stage": f"{st} (episode build)", "calls": calls, "usd": round(usd, 4)} for st, calls, usd in ep_stages]
    left = sum(usd for st, _, usd in by_stage if st == "fast_archive") - ep_total
    if abs(left) > 0.00005:
        cost.append({"stage": "fast_archive", "calls": 0, "usd": round(left, 4)})

    # One solo fallback a section, and only where the solo lock is the one that stood: the event is
    # logged when the lock is issued, and a lock that fails may be decided again as a paired one.
    fell = {e["section"]: e for e in events if e["event"] == T.SOLO_FALLBACK}
    solo = [{"section": e["section"], "waited": (fell.get(e["section"]) or {}).get("waited"),
             "t": (fell.get(e["section"]) or e)["t"]}
            for e in events if e["event"] == T.LOCK_DONE and e.get("mode") == "solo"]
    solo = list({x["section"]: x for x in solo}.values())

    saved = base.read_json(base.path(r, "work") / "archive.json") or {}
    said = [{"t": e["t"], "kind": e["kind"], "section": e["section"], "text": e["text"]}
            for e in events if e["event"] == T.LISTENER_NOTE]
    return {"run": r.name, "title": outline.get("title"), "state": (overnight_status(r) or {}).get("state"),
            "start": start, "sections": listed, "holds": holds,
            "solo": solo,
            "ledger": rows, "cost": sorted(cost, key=lambda c: -c["usd"]), "cost_usd": round(total, 4),
            "words": saved.get("words"), "script_words": sum(s["words"] or 0 for s in listed),
            "audio_seconds": saved.get("seconds"), "notes": said,
            "note_rows": [{"row": e.get("row"), "section": e.get("section"), "researched": e.get("researched")}
                          for e in events if e["event"] == T.NOTE_ROW and e.get("row")],
            "problems": [e.get("error") for e in events if e["event"] == T.PROBLEM],
            "retries": [{"action": e.get("action"), "error": e.get("error")} for e in events
                        if e["event"] in (T.RETRY, T.ACTION_FAILED, T.STEP_TIMEOUT, T.EPISODE_FAILED)]}


def overnight_status(r: Run) -> dict | None:
    return base.read_json(r.path("status"))


def _clock(t: float | None) -> str:
    """Seconds since the seed was set, as m:ss."""
    if t is None:
        return "-"
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _secs(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f} s"


def _table(head: list[str], rows: list[list]) -> list[str]:
    cell = lambda v: "-" if v is None or v == "" else " ".join(str(v).replace("|", "/").split())
    return ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |",
            *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows), ""]


def render(rep: dict) -> str:
    """The report as Markdown tables, ready to paste. Times are minutes:seconds since the seed was set."""
    s = rep["start"]
    out = [f"### {rep['run']}: {rep['title'] or '(no outline)'}", ""]
    if rep["state"] != "ready":
        out += [f"The run is not finished (status: {rep['state'] or 'none'}).", ""]
    if s["continued"]:
        out += [f"The run was stopped and continued {s['continued']} time(s); times after a stop include it.", ""]
    out += _table(["Seed to", "At", "The step took"], [
        ["scout done" + (" (an early scout was reused)" if s["scout_adopted"] else ""), _clock(s["scout"]),
         _secs(s["scout_seconds"])],
        ["outline done (written and edited)", _clock(s["outline"]), _secs(s["outline_seconds"])],
        ["first audio", _clock(s["first_audio"]), f"{_secs(s['opening_seconds'])} to write and edit the opening"]])

    rows = []
    for x in rep["sections"]:
        needed = _clock(x["needed"]) + (" (projected)" if x["projected"] and x["needed"] is not None else "")
        if x["n"] == 1:
            verdict = "playback starts here"
        elif x["margin"] is None:
            verdict = "-"
        elif x["waited"] or x["margin"] < 0:
            verdict = f"the listener waited {x['waited']:.0f} s"
        else:
            verdict = f"ready {x['margin']:.0f} s early"
        rows.append([x["n"], x["title"], x["words"], x["mode"], _clock(x["locked"]), _clock(x["ready"]), needed,
                     verdict])
    out += _table(["Section", "Title", "Words", "Edit", "Locked", "Ready (first piece)", "Playback needed it",
                   "Result"], rows)

    if rep["holds"]:
        out += _table(["Hold", "Waiting for section", "Began", "Length", "Why", "A check-in would have covered it",
                       "Decided at the time", "Solo edit"],
                      [[i, h["section"], _clock(h["start"]),
                        _secs(h["seconds"]) if h["seconds"] is not None else "still open",
                        (h["reason"] or "").replace("_", " "),
                        {"check-in": "yes", "pause": "no need: a pause"}.get(h["covered_by"], "-"),
                        (h["decision"] or "").replace("_", "-"), "yes" if h["solo"] else "no"]
                       for i, h in enumerate(rep["holds"], 1)])
    held = sum(h["seconds"] or 0 for h in rep["holds"])
    out += [f"Holds: {len(rep['holds'])}, {held:.0f} s in all. Solo-edit fallbacks: {len(rep['solo'])}"
            + (f" (section {', '.join(str(x['section']) for x in rep['solo'])})" if rep["solo"] else "") + ".", ""]

    out += _table(["Row", "Section", "Research", "Entries", "Coverage in the script", "Note"],
                  [[row["id"] + (" (from a listener note)" if row["from_note"] else ""), row["section"],
                    row["research"], row["entries"], row["coverage"] or "not judged", row["note"]]
                   for row in rep["ledger"]])
    out += _table(["Stage", "Calls", "Cost"], [[c["stage"], c["calls"], f"${c['usd']:.2f}"] for c in rep["cost"]]
                  + [["**Total**", "", f"**${rep['cost_usd']:.2f}**"]])
    minutes = "-" if rep["audio_seconds"] is None else f"{rep['audio_seconds'] / 60:.1f}"
    out += [f"Words: {rep['words'] if rep['words'] is not None else rep['script_words']} as heard. "
            f"Audio: {minutes} minutes.", ""]
    if rep["notes"]:
        out += _table(["Listener note", "At", "Said during section", "Kind"],
                      [[n["text"], _clock(n["t"]), n["section"], n["kind"]] for n in rep["notes"]])
    if rep["retries"] or rep["problems"]:
        out += ["Trouble on the way: " + "; ".join(
            [f"{x['action'] or 'episode build'}: {x['error'] or 'timed out'}" for x in rep["retries"]]
            + [f"stopped: {p}" for p in rep["problems"]]), ""]
    return "\n".join(out).rstrip() + "\n"


# --- how the notes were used ------------------------------------------------------------

STOP = set("""about above after again against also among because been before being below between both
cannot could does doing down during each from further have having here himself herself into itself just
like more most much myself only other ourselves over really same should some such than that their theirs
them themselves then there these they thing things think this those through under until very were what when
where which while whom will with would your yours yourself something anything everything someone still never
always ever make made want wanted went going came come know knew kind sort part bit little lot""".split())


def _stems(text: str) -> set[str]:
    """A text's distinctive words, roughly stemmed: four letters or more, no common words."""
    out = set()
    for w in re.findall(r"[a-z']+", text.lower()):
        w = w.strip("'").replace("'", "")
        if len(w) < 4 or w in STOP:
            continue
        for end in ("ing", "ed", "es", "s"):
            if w.endswith(end) and len(w) - len(end) >= 4:
                w = w[:-len(end)]
                break
        out.add(w)
    return out


def _sentences(text: str) -> list[str]:
    """A section's sentences as the listener hears them: no markers, no held beats."""
    out = []
    for para in re.split(r"\n\s*\n", common.spoken_text(text)):
        out += [s.strip() for s in tts.split_sentences(" ".join(tts.PAUSE.sub(" ", para).split())) if s.strip()]
    return out


def notes_use(r: Run) -> list[dict]:
    """For each listener note: the sections whose lock began after it (the only ones that could
    use it), and the lines of those sections that share its distinctive words.

    This is word matching, not judgement: it finds a callback or an answer that reuses the
    listener's words, and misses one that paraphrases them. The lines are candidates for a
    person to read beside the note; a section listed with no line may still have used it.
    "After" goes by the order of the timeline's lines: the run notices a note within a tick of
    its loop (fast.tick_seconds), so a lock that began in that same half second may be misplaced."""
    events = base.read_timeline(r)
    outline = base.read_json(base.path(r, "outline_json")) or {"sections": []}
    at = lambda name, **m: next((i for i, x in enumerate(events) if x["event"] == name
                                 and all(x.get(k) == v for k, v in m.items())), None)
    out = []
    for i, e in enumerate(events):
        if e["event"] != T.LISTENER_NOTE:
            continue
        words = _stems(e["text"])
        later = []
        for s in outline["sections"]:
            began = at(T.LOCK_START, section=s["n"]) if s["n"] > 1 else at(T.OPENING_START)
            if began is not None and began > i and sections.locked_text(r, s["n"]) is not None:
                later.append(s["n"])
        lines = []
        for n in later:
            for sentence in _sentences(sections.locked_text(r, n)):
                shared = sorted(words & _stems(sentence))
                if len(shared) >= 2 or any(len(w) >= 7 for w in shared):
                    lines.append({"section": n, "line": sentence, "shared": shared})
        lines.sort(key=lambda x: (-len(x["shared"]), x["section"]))
        out.append({"t": e["t"], "kind": e["kind"], "section": e["section"], "text": e["text"],
                    "sections_after": later, "lines": lines[:6]})
    return out


def render_notes_use(r: Run, found: list[dict]) -> str:
    if not found:
        return f"{r.name}: no listener notes.\n"
    out = [f"### {r.name}: what the later sections did with the listener's notes", "",
           "Found by matching words, so a paraphrase is missed: read the sections named beside each note.", ""]
    for x in found:
        where = "before the chapter began" if not x["section"] else f"during section {x['section']}"
        out += [f"**({x['kind']}, {where}, at {_clock(x['t'])})** {x['text']}", ""]
        if not x["sections_after"]:
            out += ["Every section was already locked when this was said: nothing could use it.", ""]
            continue
        out.append(f"Sections locked after it: {', '.join(map(str, x['sections_after']))}.")
        out += [f"- Section {ln['section']}: \"{ln['line']}\" (shares: {', '.join(ln['shared'])})" for ln in x["lines"]]
        if not x["lines"]:
            out.append("- No line in them shares its distinctive words.")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
