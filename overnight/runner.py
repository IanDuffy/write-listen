"""The overnight runner: `wl overnight run` builds tonight's chapter (docs/overnight-chapters.md,
"Overnight runner").

One command, run by the scheduler at the build start on build days, or by hand at any time
(`--now` skips the clock and day checks). The same command is what a cloud machine runs later: it
reads and writes only the listener's folder, and nothing here is laptop-specific.

Stages (each writes its files into the chapter folder; a stage whose done-file exists is skipped, so
a stopped build resumes from the last finished stage):

    1 lock      copy up_next.json into the chapter folder, card status building
    2 research  the full ledger path: scout, the card's angle, ledger, research, ledger update
    3 write     outline from the full dossier, then write and edit section by section (paired edit)
    4 promise   check the script against the card's summary; one revision if it drifted
    5 record    GPT-Live, server-side, one paragraph at a time, every take checked
    6 assemble  stitch, time, references, episode.json
    7 intro     intro_brief.md for the morning intro
    8 finish    library and seeds, history, card ready

status.json holds the stage, when it started, the cost so far and any error; cost.json the cost by
stage and provider. A failure (the runaway stop included) stops the build, marks the card failed
("Last night's chapter didn't finish"), and `wl overnight resume` restarts from the failed stage. If
nobody resumes it, the next build day retries the same plan before a new one is planned.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from stages import common
from stages.common import Run, StageError, run_config

from . import profiles

LOCK_FILE = ".overnight.lock"
CARD = "up_next.json"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --- stages -----------------------------------------------------------------------------

def lock(r: Run, cfg: dict, card: dict, listener: Path) -> None:
    """Copy the card into the chapter folder and write 00_source/seed.md from it, so the seed-run
    prompts and seed_inputs see tonight's plan. The card on the listener's side says building."""
    from . import planner
    seed = r.path("seed")
    if not seed.exists():
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(planner.seed_text(card), encoding="utf-8")
    locked = {**card, "status": "building", "locked_at": card.get("locked_at") or _now_iso(), "chapter": r.name}
    profiles.write_json(r.root / "00_source" / CARD, locked)  # last: it is this stage's done-file
    _update_card(listener, card, status="building", locked_at=locked["locked_at"], chapter=r.name, error=None)


def angle_text(card: dict) -> str:
    """03_angle.md from the card: the parser contract's first line, then the angle and the promise."""
    lines = [f"# Angle: {card['title'].strip()}", "", (card.get("angle") or "").strip(), "",
             "## The promise (the Up next card's summary)", "", card.get("summary", "").strip()]
    if card.get("research_questions"):
        lines += ["", "## Questions to research", ""] + [f"- {q}" for q in card["research_questions"]]
    return "\n".join(lines).strip() + "\n"


def research(r: Run, cfg: dict) -> None:
    """The full ledger path, research before the outline: scout, the card's angle (in place of
    angles and choose), ledger, research, ledger update. Each step skips when its file exists."""
    from stages import ledger, ledger_update, research as research_stage, scout
    if not r.path("map").exists():
        scout.run(r, cfg)
    if not r.path("angle").exists():
        card = json.loads((r.root / "00_source" / CARD).read_text(encoding="utf-8"))
        r.write("angle", angle_text(card))
        r.decide("angle", f"The Up next card's angle: {card['title']}.")
    if not r.path("ledger").exists():
        ledger.run(r, cfg)
    if not r.path("dossier").exists():
        research_stage.run(r, cfg)
    if not r.path("ledger_update").exists():
        ledger_update.run(r, cfg)


def write(r: Run, cfg: dict) -> None:
    from . import outline, write as write_mod
    if not (r.root / "05_outline.json").exists():
        outline.run(r, cfg)
    write_mod.run(r, cfg)


def promise(r: Run, cfg: dict) -> None:
    from . import promise as promise_mod
    promise_mod.run(r, cfg)


def record(r: Run, cfg: dict) -> None:
    from . import record as record_mod
    record_mod.run(r, cfg)


def assemble(r: Run, cfg: dict) -> None:
    from . import assemble as assemble_mod
    assemble_mod.run(r, cfg)


def intro(r: Run, cfg: dict) -> None:
    from . import intro as intro_mod
    intro_mod.run(r, cfg)


def finish(r: Run, cfg: dict) -> None:
    from stages import memory

    from . import library
    listener = common.owner_of(r.root)
    library.after_chapter(cfg, listener, r.root)
    memory.record_chapter(cfg, r)  # history.md, which the guide reads
    card = json.loads((r.root / "00_source" / CARD).read_text(encoding="utf-8"))
    rec = (r.root / "10_audio" / "recorded.json")
    done = {"t": _now_iso(), "minutes": _minutes(r), "cost_usd": round(r.spent(), 4),
            "recording": json.loads(rec.read_text(encoding="utf-8")) if rec.exists() else None}
    profiles.write_json(r.root / "finished.json", done)
    _update_card(listener, card, status="ready", error=None)


STAGES = [
    ("lock", "00_source/up_next.json", None, "Locking the plan"),
    ("research", "04_research/ledger_update.md", research, "Researching"),
    ("write", "07_script_draft.md", write, "Writing"),
    ("promise", "09_script_final.md", promise, "Checking it against the card"),
    ("record", "10_audio/recorded.json", record, "Recording"),
    ("assemble", "11_episode/episode.json", assemble, "Assembling"),
    ("intro", "intro_brief.md", intro, "Writing the intro brief"),
    ("finish", "finished.json", finish, "Finishing"),
]


def done(r: Run, key: str) -> bool:
    return (r.root / key).exists()


# --- the card ---------------------------------------------------------------------------

def card_path(listener: Path) -> Path:
    return listener / CARD


def read_card(listener: Path) -> dict | None:
    return profiles.read_json(card_path(listener))


def _update_card(listener: Path, card: dict, **fields) -> None:
    """Change the listener's card only if it is still this plan (a new plan may have replaced it)."""
    now = read_card(listener)
    if now is not None and now.get("id") == card.get("id"):
        profiles.write_json(card_path(listener), {**now, **fields})


def slug(title: str, words: int = 5) -> str:
    stop = {"a", "an", "the", "of", "and", "to", "in", "on", "for", "is", "are", "what", "why", "how", "it"}
    ws = [w for w in re.findall(r"[a-z0-9]+", title.lower().replace("'", "")) if w not in stop]
    return "-".join(ws[:words]) or "chapter"


def chapter_name(listener: Path, card: dict) -> str:
    """<for_morning>-<slug>; a folder already holding a different plan gets -2, -3..."""
    day = card.get("for_morning") or datetime.now().strftime("%Y-%m-%d")
    base = f"{day}-{slug(card['title'])}"
    name, n = base, 2
    while (listener / "chapters" / name).exists():
        other = profiles.read_json(listener / "chapters" / name / "00_source" / CARD, {}) or {}
        if other.get("id") == card.get("id"):
            break
        name, n = f"{base}-{n}", n + 1
    return name


# --- status and cost --------------------------------------------------------------------

def write_status(r: Run, state: str, card: dict, **fields) -> dict:
    """status.json, with the fields the server and page read (stages/new.write_status's) plus the
    stage start, the cost so far and any error."""
    old = profiles.read_json(r.path("status"), {}) or {}
    status = {
        "run": r.name, "mode": "overnight", "state": state, "stage": None, "label": None,
        "stages_done": sum(1 for _, key, _, _ in STAGES if done(r, key)), "stages_total": len(STAGES),
        "minutes_left": None, "title": card.get("title"), "audio": None, "message": None, "detail": None,
        "error": None, "stage_started": None, "cost_usd": round(r.spent(), 4),
        "started": old.get("started") or _now_iso(), "updated": _now_iso(),
        "history": old.get("history", []), "pid": os.getpid(), "retryable": True, **fields,
    }
    profiles.write_json(r.path("status"), status)
    return status


def build_stage(call_stage: str) -> str:
    """The runner stage a logged call belongs to (drafting and locking log as fast_draft/fast_lock)."""
    s = call_stage or "?"
    if s.startswith(("scout", "ledger", "research", "angles", "choose")):
        return "research"
    if s.startswith(("overnight_outline", "fast_draft", "fast_lock", "fast_")):
        return "write"
    if s.startswith(("overnight_promise", "overnight_revise")):
        return "promise"
    if s.startswith("record"):
        return "record"
    if s.startswith(("assemble", "episode")):
        return "assemble"
    if s.startswith("overnight_intro"):
        return "intro"
    if s.startswith("overnight_library"):
        return "finish"
    return s


def write_cost(r: Run, cfg_r: dict) -> dict:
    by_stage: dict[str, float] = {}
    by_call: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    for rec in r.read_log():
        usd = rec.get("cost_usd") or 0
        if not usd:
            continue
        st = rec.get("stage", "?")
        by_call[st] = round(by_call.get(st, 0) + usd, 4)
        b = build_stage(st)
        by_stage[b] = round(by_stage.get(b, 0) + usd, 4)
        prov = rec.get("provider") or "anthropic"
        by_provider[prov] = round(by_provider.get(prov, 0) + usd, 4)
    out = {"total_usd": round(sum(by_stage.values()), 4), "by_stage": by_stage, "by_call_stage": by_call,
           "by_provider": by_provider, "runaway_stop_usd": r.max_usd, "updated": _now_iso()}
    profiles.write_json(r.root / "cost.json", out)
    return out


def _minutes(r: Run) -> float | None:
    t = profiles.read_json(r.root / "11_episode" / "timing.json", {}) or {}
    return round(t["duration"] / 60, 2) if t.get("duration") else None


# --- one build ---------------------------------------------------------------------------

class Busy(StageError):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _take_lock(listener: Path) -> Path:
    p = listener / LOCK_FILE
    if p.exists():
        try:
            pid = int(p.read_text().split()[0])
        except (ValueError, IndexError):
            pid = 0
        if pid and pid != os.getpid() and _alive(pid):
            raise Busy(f"A build is already running for {listener.name} (pid {pid}).")
    p.write_text(f"{os.getpid()} {_now_iso()}\n")
    return p


def _keep_awake() -> None:
    """macOS: hold off idle sleep while this process runs (caffeinate -i -w <pid>). Nothing elsewhere."""
    import shutil
    import subprocess
    exe = shutil.which("caffeinate")
    if exe:
        subprocess.Popen([exe, "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sized(cfg: dict, settings: dict) -> dict:
    """The listener's chapter length (settings chapter.minutes) as the writing stages' target, with the
    acceptance window of two minutes either side (check 7: 18-22 for 20)."""
    m = float(settings["chapter"]["minutes"])
    o = cfg.get("overnight") or {}
    w = {**(o.get("write") or {}), "target_minutes": m}
    return {**cfg, "overnight": {**o, "write": w, "total_minutes": [m - 2, m + 2]}}


def build(cfg: dict, listener: Path, card: dict, *, keep_awake: bool = False) -> dict:
    """Build (or continue) the chapter for this card. Returns the final status."""
    lockfile = _take_lock(listener)
    try:
        if keep_awake:
            _keep_awake()
        name = card.get("chapter") or chapter_name(listener, card)
        r = Run.open(name, create=True, runs_dir=listener / "chapters")
        r.unattended = True
        settings = profiles.settings(cfg, listener=listener)
        r.max_usd = float(settings["overnight"]["runaway_stop_usd"])
        cfg = sized(cfg, settings)
        cfg_r = run_config(r, cfg)
        todo = [label for label, key, _, _ in STAGES if not done(r, key)]
        if not todo:
            _update_card(listener, card, status="ready", chapter=r.name, error=None)
            return write_status(r, "ready", card, audio="10_audio/chapter.mp3", cost_usd=round(r.spent(), 4))
        attempts = int(card.get("attempts") or 0) + 1  # schedule.build_due retries a failed card once
        card = {**card, "attempts": attempts}
        _update_card(listener, card, attempts=attempts)
        r.decide("start" if r.spent() == 0 else "continue",
                 f"Overnight build of '{card['title']}' ({card.get('id')}): {', '.join(todo)}. Runaway stop "
                 f"${r.max_usd:.2f}; ${r.spent():.2f} spent before this.")
        print(f"  overnight build {r.name}: {', '.join(todo)} (runaway stop ${r.max_usd:.0f})")
        for label, key, fn, plain in STAGES:
            if done(r, key):
                continue
            print(f"\n== {label} ==")
            started = _now_iso()
            write_status(r, "making", card, stage=label, label=plain, stage_started=started)
            t0 = time.monotonic()
            try:
                if label == "lock":
                    lock(r, cfg_r, card, listener)
                    cfg_r = run_config(r, cfg)  # now a seed run with the overnight settings
                else:
                    fn(r, cfg_r)
                if not done(r, key):
                    raise StageError(f"{label} finished without writing {key}.")
            except BaseException as e:  # Ctrl-C included: the card must not keep saying building
                err = "interrupted" if isinstance(e, KeyboardInterrupt) else f"{type(e).__name__}: {e}"
                r.decide("stopped", f"Stopped at {label}: {err}")
                write_cost(r, cfg_r)
                write_status(r, "problem", card, stage=label, label=plain, stage_started=started, error=err,
                             detail=err, message="Last night's chapter didn't finish.")
                _update_card(listener, card, status="failed", chapter=r.name, error=f"{label}: {err}")
                if not isinstance(e, StageError):
                    raise
                raise StageError(f"stopped at {label}: {e}\n  To continue from {label}: wl overnight resume") from None
            hist = (profiles.read_json(r.path("status"), {}) or {}).get("history", []) + [
                {"stage": label, "seconds": round(time.monotonic() - t0, 1)}]
            write_cost(r, cfg_r)
            write_status(r, "making", card, stage=label, label=plain, stage_started=started, history=hist)
        cost = write_cost(r, cfg_r)
        r.decide("done", f"Finished: {_minutes(r)} minutes, ${cost['total_usd']:.2f} in all.")
        return write_status(r, "ready", card, audio="10_audio/chapter.mp3")
    finally:
        try:
            lockfile.unlink()
        except FileNotFoundError:
            pass


def listener_folder(lid: str | None) -> Path:
    return profiles.folder(lid) if lid else common.listener_dir()


def run(cfg: dict, lid: str | None = None, *, now: bool = False, clock: datetime | None = None,
        keep_awake: bool = False) -> dict:
    """`wl overnight run`. Scheduled (now=False): builds only if overnight building is on, today is a
    build day, the build time has come and the card is for this morning. --now: builds at once; with
    no card (or only a finished one) it plans one first."""
    from . import planner, schedule
    listener = listener_folder(lid)
    if not listener.is_dir():
        raise StageError(f"No listener folder at {listener}.")
    settings = profiles.settings(cfg, listener=listener)
    card = read_card(listener)
    t = clock or datetime.now()
    if not now:
        if not schedule.build_due(card, settings, t):
            reason = ("overnight building is off" if not settings["overnight"]["enabled"] else
                      "no build is due now")
            print(f"  nothing to build: {reason}")
            return {"ran": False, "reason": reason}
    elif card is None or card.get("status") == "ready":
        print("  no plan waiting: planning one first")
        card = planner.plan(cfg, listener, now=t)
    return {"ran": True, **build(cfg, listener, card, keep_awake=keep_awake)}


def resume(cfg: dict, lid: str | None = None) -> dict:
    """`wl overnight resume`: continue the card's chapter from the stage that failed or stopped."""
    listener = listener_folder(lid)
    card = read_card(listener)
    if not card or card.get("status") not in ("failed", "building") or not card.get("chapter"):
        raise StageError("There is no stopped build to resume.")
    return {"ran": True, **build(cfg, listener, card)}


def state(cfg: dict, lid: str | None = None) -> dict:
    listener = listener_folder(lid)
    card = read_card(listener) or {}
    out = {"listener": listener.name, "card": card}
    if card.get("chapter"):
        out["status"] = profiles.read_json(listener / "chapters" / card["chapter"] / "status.json")
    return out
