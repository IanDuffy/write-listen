"""Listener profiles and storage (docs/overnight-chapters.md, "Listener profiles and storage").

Each listener is a folder under listeners/; listeners/active.json names the one in use. Everything
the app makes for a listener lives inside its folder, and nothing is ever deleted: a reset makes a
new demo listener and switches to it, and the old folder (ian/ above all) is left byte for byte.

    listeners/
      active.json              {"id": "ian"}
      ian/
        profile.md, guide.md   the memory pass's files (stages/memory.py)
        settings.json          this listener's settings, over config.yaml listener_settings
        library.jsonl          works and topics met
        seeds.jsonl            questions and ideas for future chapters
        up_next.json           tonight's plan (the card)
        queue.jsonl            topics set for a later night
        plans/<time>/          each planning pass: its inputs, scout notes, log
        sessions/              every live conversation
        chapters/<date>-<slug>/  one folder per chapter
        archive/               what migration brought in that is not a chapter
    shared/                    what belongs to no listener (voice agent ids, caches)

The path resolver itself is stages/common.py (listener_dir, chapters_dir), so every older module
follows the active listener without knowing about this one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import date, datetime
from pathlib import Path

from stages import common
from stages.common import StageError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODES = ("surprise", "continue", "topic")
ID_RE = r"[a-z0-9][a-z0-9._-]*"
LISTENER_FOLDERS = ("sessions", "chapters", "plans")


# --- files ------------------------------------------------------------------------

def write_json(p: Path, data) -> None:
    """Whole or not at all: the page, the scheduler and the runner read these while they change."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def append_jsonl(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# --- listeners --------------------------------------------------------------------

def folder(lid: str | None = None) -> Path:
    return common.LISTENERS_DIR / lid if lid else common.listener_dir()


def ids() -> list[str]:
    d = common.LISTENERS_DIR
    return sorted(p.name for p in d.iterdir() if p.is_dir() and re.fullmatch(ID_RE, p.name)) if d.exists() else []


def create(lid: str) -> Path:
    """A new listener's folder, with its settings. Refuses an existing one."""
    if not re.fullmatch(ID_RE, lid):
        raise StageError(f"Listener id {lid!r}: lower-case letters, digits, '.', '_' or '-'.")
    d = common.LISTENERS_DIR / lid
    if d.exists():
        raise StageError(f"Listener {lid} already exists.")
    d.mkdir(parents=True)
    for name in LISTENER_FOLDERS:
        (d / name).mkdir()
    write_json(d / "settings.json", {"created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return d


def ensure(lid: str) -> Path:
    """The listener's folder, created if it is missing (never changed if it exists)."""
    d = common.LISTENERS_DIR / lid
    return d if d.exists() else create(lid)


def set_active(lid: str) -> None:
    if not (common.LISTENERS_DIR / lid).is_dir():
        raise StageError(f"No listener {lid}.")
    write_json(common.LISTENERS_DIR / "active.json", {"id": lid, "since": time.strftime("%Y-%m-%dT%H:%M:%S")})


def reset(today: date | None = None) -> str:
    """Create a new demo listener (demo-<date>-a, -b, ...) and make it active. Nothing else is
    touched: not ian/, not the listener that was active, not any chapter."""
    day = (today or date.today()).isoformat()
    for letter in "abcdefghijklmnopqrstuvwxyz":
        lid = f"demo-{day}-{letter}"
        if not (common.LISTENERS_DIR / lid).exists():
            break
    else:
        lid = f"demo-{day}-{time.strftime('%H%M%S')}"
    create(lid)
    set_active(lid)
    return lid


# --- settings ---------------------------------------------------------------------

def defaults(cfg: dict) -> dict:
    return json.loads(json.dumps(cfg.get("listener_settings") or {}))


def settings(cfg: dict, lid: str | None = None, *, listener: Path | None = None) -> dict:
    """config.yaml listener_settings, with the listener's settings.json over it, key by key."""
    p = (listener or folder(lid)) / "settings.json"
    own = read_json(p, {}) or {}
    return common._merge(defaults(cfg), {k: v for k, v in own.items() if isinstance(v, dict)})


def _hhmm(v) -> str:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise StageError(f"{v!r} is not a time like 02:00.")
    return f"{int(m[1]):02d}:{m[2]}"


def save_settings(cfg: dict, changes: dict, lid: str | None = None, *, listener: Path | None = None) -> dict:
    """Merge changes ({"overnight": {...}, "chapter": {...}, "playback": {...}}) into settings.json,
    checked. Returns the settings now in force."""
    d = listener or folder(lid)
    if not d.is_dir():
        raise StageError(f"No listener folder at {d}.")
    allowed = defaults(cfg)
    clean: dict = {}
    for group, vals in (changes or {}).items():
        if group not in allowed or not isinstance(vals, dict):
            raise StageError(f"Unknown setting group {group!r}.")
        for k, v in vals.items():
            if k not in allowed[group]:
                raise StageError(f"Unknown setting {group}.{k}.")
            if k in ("start", "plan_at"):
                v = _hhmm(v)
            elif k == "days":
                v = [str(x).lower()[:3] for x in (v or [])]
                if any(x not in DAYS for x in v):
                    raise StageError(f"Build days must be among {', '.join(DAYS)}.")
                v = [x for x in DAYS if x in v]
            elif k == "mode" and v not in MODES:
                raise StageError(f"Mode must be one of {', '.join(MODES)}.")
            elif isinstance(allowed[group][k], bool):
                v = bool(v)
            elif isinstance(allowed[group][k], (int, float)):
                try:
                    v = float(v)  # a whole number stays whole (minutes: 20, not 20.0)
                    v = int(v) if v.is_integer() and isinstance(allowed[group][k], int) else v
                except (TypeError, ValueError):
                    raise StageError(f"{group}.{k} must be a number.") from None
                if v <= 0:
                    raise StageError(f"{group}.{k} must be above zero.")
            clean.setdefault(group, {})[k] = v
    own = read_json(d / "settings.json", {}) or {}
    write_json(d / "settings.json", common._merge(own, clean))
    return settings(cfg, listener=d)


# --- migration --------------------------------------------------------------------

SKIP_RUNS = ("ping", "smoketest")


def _run_date(src: Path) -> str:
    """The day a chapter was finished (its chapter.mp3), else the -YYYYMMDD suffix of its name, else
    status.json's start, else its first log line."""
    mp3 = src / "10_audio" / "chapter.mp3"
    if mp3.exists():
        return datetime.fromtimestamp(mp3.stat().st_mtime).strftime("%Y-%m-%d")
    m = re.search(r"-(\d{4})(\d{2})(\d{2})(?:-\d+)?$", src.name)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}"
    st = read_json(src / "status.json", {}) or {}
    if isinstance(st.get("started"), str) and re.match(r"\d{4}-\d{2}-\d{2}", st["started"]):
        return st["started"][:10]
    log = src / "log.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                ts = json.loads(line).get("ts", "")
            except ValueError:
                continue
            if re.match(r"\d{4}-\d{2}-\d{2}", ts):
                return ts[:10]
    return datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")


def chapter_folder_name(src: Path) -> str:
    slug = re.sub(r"-\d{8}(?:-\d+)?$", "", src.name)
    return f"{_run_date(src)}-{slug}"


def _copy(src: Path, dest: Path) -> None:
    shutil.copytree(src, dest, symlinks=True, copy_function=shutil.copy2)


def _merge(src: Path, dest: Path) -> bool:
    """Copy into dest what it lacks, file by file (a folder that exists is merged into, never
    skipped whole; a file that exists is kept). Whether anything was copied."""
    if not dest.exists() and not dest.is_symlink():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dest)
        elif src.is_dir():
            _copy(src, dest)
        else:
            shutil.copy2(src, dest)
        return True
    if src.is_symlink() or not src.is_dir() or not dest.is_dir():
        return False
    return any([_merge(c, dest / c.name) for c in sorted(src.iterdir())])


def _repoint(dest: Path, src: Path) -> None:
    """ffmpeg lists (10_audio/**/concat.txt) hold absolute paths: point the copy's at the copy."""
    for p in dest.rglob("concat.txt"):
        text = p.read_text(encoding="utf-8")
        new = text.replace(str(src.resolve()), str(dest.resolve()))
        if new != text:
            p.write_text(new, encoding="utf-8")


RUN_NAME_FILES = ("status.json", "fast.json", "11_episode/episode.json")


def _fix_run_name(root: Path) -> list[str]:
    """A renamed chapter's files that name their run ("run") name its folder: the page builds the
    audio and episode addresses from status.json's run, so the old name would 404. Each file keeps
    its mtime (the server follows the newest status.json)."""
    out = []
    for rel in RUN_NAME_FILES:
        p = root / rel
        data = read_json(p)
        if isinstance(data, dict) and isinstance(data.get("run"), str) and data["run"] != root.name:
            st = p.stat()
            write_json(p, {**data, "run": root.name})
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            out.append(str(p))
    return out


def fix_run_names(listener: Path | str | None = None) -> list[str]:
    """Repair for a migration made before migrate() renamed run fields: every chapter folder's
    files name the folder as their run. Idempotent. Returns the files changed."""
    d = listener if isinstance(listener, Path) else folder(listener)
    return [p for ch in chapters(listener=d) for p in _fix_run_name(ch)]


# What the memory pass and the guide read (stages/memory.py): they go into the listener's own folder.
MEMORY_ITEMS = ("profile.md", "guide.md", "history.md", "seeds", "sessions")


def migrate(sources: list[Path], lid: str = common.DEFAULT_LISTENER, chapters: tuple[str, ...] = (),
            dry_run: bool = False) -> dict:
    """Copy each checkout's runs/ and listener/ into listeners/<lid>/: the named chapter runs into
    chapters/<date>-<slug>/ (their run fields renamed to match), the memory files (MEMORY_ITEMS)
    into the listener's folder itself, everything else (_cleared_*, _tests_*, voice_compare, other
    runs) into archive/<checkout>/. Copies, never moves: the sources are other checkouts' working
    folders. Idempotent: what is already there is kept, and a folder is merged into file by file."""
    d = common.LISTENERS_DIR / lid
    plan: list[tuple[str, Path, Path]] = []
    for src_root in sources:
        src_root = Path(src_root).expanduser().resolve()
        label = src_root.name
        runs = src_root / "runs"
        for run in sorted(runs.iterdir()) if runs.is_dir() else []:
            if not run.is_dir() or run.name in SKIP_RUNS:
                continue
            if run.name in chapters:
                plan.append(("chapter", run, d / "chapters" / chapter_folder_name(run)))
            else:
                plan.append(("archive", run, d / "archive" / label / "runs" / run.name))
        lst = src_root / "listener"
        for item in sorted(lst.iterdir()) if lst.is_dir() else []:
            if item.name.startswith("voice") and item.suffix == ".json":
                plan.append(("shared", item, common.SHARED_DIR / "voice" / item.name))
            elif item.name in MEMORY_ITEMS:
                plan.append(("memory", item, d / item.name))
            else:
                plan.append(("archive", item, d / "archive" / label / "listener" / item.name))
    found = {p[1].name for p in plan if p[0] == "chapter"}
    missing = [c for c in chapters if c not in found]
    done = {"listener": lid, "copied": [], "kept": [], "missing_chapters": missing}
    if dry_run:
        done["plan"] = [(k, str(s), str(t)) for k, s, t in plan]
        return done
    ensure(lid)
    for kind, src, dest in plan:
        done["copied" if _merge(src, dest) else "kept"].append(str(dest))
        if src.is_dir():
            _repoint(dest, src)
        if kind == "chapter":
            _fix_run_name(dest)
    if not (common.LISTENERS_DIR / "active.json").exists():
        set_active(lid)
    return done


# --- chapters ---------------------------------------------------------------------

def chapters(lid: str | None = None, *, listener: Path | None = None) -> list[Path]:
    """The listener's chapter folders, oldest first by folder name (YYYY-MM-DD-<slug>)."""
    d = (listener or folder(lid)) / "chapters"
    return sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))) if d.exists() else []


def finished(root: Path) -> bool:
    return (root / "10_audio" / "chapter.mp3").exists()


def finished_at(root: Path) -> float:
    """When a finished chapter finished: finished.json's t (an overnight build), else status.json's
    updated when it says ready (a seed or fast run), else its chapter.mp3's mtime (a migrated one)."""
    for p, key in ((root / "finished.json", "t"), (root / "status.json", "updated")):
        data = read_json(p)
        if not isinstance(data, dict) or (key == "updated" and data.get("state") != "ready"):
            continue
        try:
            return datetime.fromisoformat(str(data.get(key))).timestamp()
        except ValueError:
            pass
    mp3 = root / "10_audio" / "chapter.mp3"
    return mp3.stat().st_mtime if mp3.exists() else 0.0


def latest_chapter(lid: str | None = None, *, listener: Path | None = None) -> Path | None:
    """The newest playable chapter: of the folders with a chapter.mp3, the one that finished last.
    Not by name: seed and fast runs are named <slug>-YYYYMMDD, overnight ones YYYY-MM-DD-<slug>."""
    done = [p for p in chapters(lid, listener=listener) if finished(p)]
    return max(done, key=lambda p: (finished_at(p), p.name)) if done else None


def overnight_build(root: Path) -> bool:
    """A chapter folder the overnight runner makes (overnight/runner.py), not the seed pipeline:
    its locked plan is in 00_source/up_next.json, or its status says mode overnight."""
    if (root / "00_source" / "up_next.json").exists():
        return True
    st = read_json(root / "status.json")
    return isinstance(st, dict) and st.get("mode") == "overnight"
