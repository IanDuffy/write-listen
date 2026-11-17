"""Testers: the link is the tester (docs/tester-demo.md, "Tester links and consent").

    shared/testers.json            {"tokens": {token: {"lid", "label", "created", "given_name"}}}: the only way in
    listeners/<lid>/tester.json    {label, lid, token, created, opened_at, consent_at, name, given_name,
                                    debrief_card: "auto"|"on"|"off", stage_hint, intro_done, ...}

A tester may be made with a name (Ian's notes, 2026-11-16): the interviewer greets them by it ("Hi, is this
Rolando?"). With none given, a label that looks like a first name is used (first_name). It is saved as
set_name saves one, so the guide has it whatever the intro does, and kept as given_name so a reset or a
remade folder starts with it again; a correction in the intro replaces name, never given_name.
    listeners/<lid>/settings.json  the demo settings (chapter.minutes from demo.chapter_minutes, overnight off)

A tester's lid is t-<slug of the label>. The folder is made when the link is created and made again on
first open if it has gone missing (the spec says "created on first open"; making it at creation as well
lets the admin page read every tester the same way). Revoking deletes the token and keeps the folder;
reset moves the folder aside (listeners/_cleared_<time>/<lid>) and makes a fresh one with the same token;
delete removes both.

listener_for_request(scope) runs on every request (part A's middleware), so the registry is cached and
re-read only when its file changes.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from overnight import profiles
from stages import common
from stages.common import StageError

COOKIE = "wl_tester"   # demo.cookie; routes.mount sets it from config
CARD_VALUES = ("auto", "on", "off")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")
NAME_LINE = "Their name is {name} (they told the interviewer just now)."
GIVEN_LINE = "Their name is {name} (Ian gave it when he invited them)."  # the guide reads this: say what is true
# Labels that are not what you'd call someone: the interviewer asks the name instead of "Hi, is this Pilot?".
NOT_A_NAME = {"pilot", "test", "tester", "testing", "friend", "demo", "team", "qa", "mom", "mum", "dad", "design",
              "trial", "user", "guest", "sample", "beta", "alpha", "staff", "internal", "family", "colleague",
              "wife", "husband", "boss", "client", "customer", "group", "round", "batch", "study", "someone"}
_WORD = r"[A-Z](?=[A-Za-z'’-]*[a-z])[A-Za-z'’-]+"
FIRST_NAME = re.compile(_WORD + "(?: " + _WORD + ")?")  # "Rolando", "Mary Ann"; not "Pilot 1", "Ian test", "QA"

_LOCK = threading.RLock()
_cache: dict = {"key": None, "data": None}

# Called with (lid, action) before a reset, delete or revoke changes a tester's folder or token, so the
# server can drop that listener's conversation and stop a chapter being made (part A registers it).
ON_CHANGE: list = []


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --- the registry -------------------------------------------------------------------

def registry_path() -> Path:
    return common.shared_dir() / "testers.json"


def _registry() -> dict:
    p = registry_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return {"tokens": {}}
    with _LOCK:
        if _cache["key"] != key:
            data = profiles.read_json(p, {}) or {}
            data.setdefault("tokens", {})
            _cache.update(key=key, data=data)
        return _cache["data"]


def _save_registry(data: dict) -> None:
    profiles.write_json(registry_path(), data)
    _cache.update(key=None, data=None)  # an mtime can repeat within its resolution: never trust it after a write


# --- tester records -----------------------------------------------------------------

def folder(lid: str) -> Path:
    return common.LISTENERS_DIR / lid


def record(lid: str) -> dict | None:
    rec = profiles.read_json(folder(lid) / "tester.json")
    return rec if isinstance(rec, dict) else None


def _write_record(lid: str, rec: dict) -> None:
    profiles.write_json(folder(lid) / "tester.json", rec)


def _need(lid: str) -> dict:
    rec = record(lid)
    if rec is None:
        raise StageError(f"No tester {lid}.")
    return rec


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30].strip("-")
    return s or "tester"


def _new_lid(label: str) -> str:
    base = f"t-{slug(label)}"
    taken = {v.get("lid") for v in _registry()["tokens"].values()}
    lid, n = base, 2
    while lid in taken or folder(lid).exists():
        lid, n = f"{base}-{n}", n + 1
    return lid


def first_name(label: str) -> str | None:
    """The label, when it reads as a name: one or two capitalised words without digits, none of them a word
    for a kind of tester ("Pilot", "Design Team", "Mom")."""
    if not FIRST_NAME.fullmatch(label or "") or any(w.lower() in NOT_A_NAME for w in label.split()):
        return None
    return label


def greeting_name(label: str, name: str | None = None) -> str | None:
    """What the interviewer will greet a new tester as: the name given, else a label that reads as one, else
    None (it asks)."""
    label = " ".join(str(label or "").split())
    return " ".join(str(name or "").split())[:60] or first_name(label)


def _make_folder(cfg: dict, rec: dict) -> Path:
    """The listener folder with the demo settings and tester.json; a name given with the link is saved as
    set_name saves one (tester.json and the profile's line for the guide)."""
    d = profiles.create(rec["lid"])
    minutes = (cfg.get("demo") or {}).get("chapter_minutes", 10)
    profiles.save_settings(cfg, {"chapter": {"minutes": minutes}, "overnight": {"enabled": False}}, listener=d)
    _write_record(rec["lid"], rec)
    if rec.get("given_name"):
        set_name(rec["lid"], rec["given_name"], given=True)
    return d


def base_url(cfg: dict) -> str:
    return (os.environ.get("WL_BASE_URL") or (cfg.get("demo") or {}).get("base_url")
            or "http://127.0.0.1:8765").rstrip("/")


def link(cfg: dict, token: str, base: str | None = None) -> str:
    return f"{(base or base_url(cfg)).rstrip('/')}/t/{token}"


def create(cfg: dict, label: str, base: str | None = None, name: str | None = None) -> dict:
    """A new tester: a token, its registry entry and its folder. {token, lid, label, name, link}. `name` is
    what the interviewer greets them by; without one, a label that reads as a first name is used."""
    label = " ".join(str(label or "").split())
    if not label:
        raise StageError("A tester needs a label.")
    given = greeting_name(label, name)
    with _LOCK:
        lid = _new_lid(label)
        token = secrets.token_urlsafe(24)  # 32 characters
        created = now()
        rec = {"label": label, "lid": lid, "token": token, "created": created, "opened_at": None,
               "consent_at": None, "name": None, "given_name": given, "debrief_card": "auto", "stage_hint": None}
        _make_folder(cfg, rec)
        reg = _registry()
        tokens = {**reg["tokens"], token: {"lid": lid, "label": label, "created": created, "given_name": given}}
        _save_registry({**reg, "tokens": tokens})
    return {"token": token, "lid": lid, "label": label, "name": given, "link": link(cfg, token, base)}


def lookup(token: str | None) -> dict | None:
    """The tester a token opens: tester.json with the registry's fields under it, or None (unknown or revoked)."""
    if not token or not TOKEN_RE.fullmatch(token):
        return None
    entry = _registry()["tokens"].get(token)
    if not entry:
        return None
    return {**entry, "token": token, **(record(entry["lid"]) or {})}


def open_link(cfg: dict, token: str) -> dict | None:
    """/t/<token>: the tester, with the folder made if it is missing and opened_at set the first time."""
    with _LOCK:
        rec = lookup(token)
        if rec is None:
            return None
        if not folder(rec["lid"]).exists():
            _make_folder(cfg, {"label": rec["label"], "lid": rec["lid"], "token": token, "created": rec["created"],
                               "opened_at": None, "consent_at": None, "name": None,
                               "given_name": rec.get("given_name"), "debrief_card": "auto", "stage_hint": None})
        return mark_opened(rec["lid"])


def mark_opened(lid: str) -> dict:
    with _LOCK:
        rec = _need(lid)
        if not rec.get("opened_at"):
            rec["opened_at"] = now()
            _write_record(lid, rec)
        return rec


# --- requests ------------------------------------------------------------------------

def token_from_scope(scope) -> str | None:
    """The tester cookie from an ASGI scope's headers."""
    from starlette.requests import cookie_parser

    for k, v in scope.get("headers") or []:
        if k == b"cookie":
            got = cookie_parser(v.decode("latin-1")).get(COOKIE)
            if got:
                return got
    return None


def tester_for_request(scope) -> dict | None:
    rec = lookup(token_from_scope(scope))
    return rec if rec and folder(rec["lid"]).is_dir() else None


def listener_for_request(scope) -> str | None:
    """"t-<slug>" for a request carrying a live tester's cookie, else None."""
    entry = _registry()["tokens"].get(token_from_scope(scope) or "")
    return entry["lid"] if entry and folder(entry["lid"]).is_dir() else None


# --- admin actions --------------------------------------------------------------------

def _notify(lid: str, action: str) -> None:
    """Run the ON_CHANGE hooks. Never under _LOCK: a hook can take seconds (stopping a build, a memory pass
    finishing), and every request's middleware takes _LOCK to read the registry."""
    for fn in list(ON_CHANGE):
        try:
            fn(lid, action)
        except Exception as e:  # the admin's action goes ahead regardless
            print(f"  tester {lid}: {action} hook failed: {e}")


def _drop_tokens(lid: str) -> None:
    reg = _registry()
    tokens = {t: v for t, v in reg["tokens"].items() if v.get("lid") != lid}
    if len(tokens) != len(reg["tokens"]):
        _save_registry({**reg, "tokens": tokens})


def revoke(lid: str) -> None:
    """The link stops working; the folder stays until Ian deletes it."""
    _notify(lid, "revoke")
    with _LOCK:
        _drop_tokens(lid)
        rec = record(lid)
        if rec is not None:
            _write_record(lid, {**rec, "token": None, "revoked_at": now()})


def reset(lid: str, cfg: dict | None = None) -> Path:
    """A fresh start on the same link: the folder goes to listeners/_cleared_<time>/<lid> (recoverable by
    hand) and a new one is made with the same label, token and created time. Returns where the old one went."""
    cfg = cfg or common.load_config()
    _need(lid)
    _notify(lid, "reset")
    with _LOCK:
        rec = _need(lid)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = common.LISTENERS_DIR / f"_cleared_{stamp}" / lid
        n = 2
        while dest.exists():
            dest, n = common.LISTENERS_DIR / f"_cleared_{stamp}-{n}" / lid, n + 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder(lid)), str(dest))
        _make_folder(cfg, {"label": rec["label"], "lid": lid, "token": rec.get("token"), "created": rec["created"],
                           "opened_at": None, "consent_at": None, "name": None,
                           "given_name": rec.get("given_name"),
                           "debrief_card": rec.get("debrief_card") or "auto", "stage_hint": None,
                           **({"revoked_at": rec["revoked_at"]} if rec.get("revoked_at") else {}),
                           "reset_at": now()})
        return dest


def delete(lid: str) -> None:
    """Everything of this tester's, on their request: the folder and the token. Cannot be undone."""
    if not re.fullmatch(r"t-[a-z0-9][a-z0-9._-]*", lid):
        raise StageError(f"{lid!r} is not a tester.")  # never rmtree a listener that isn't a tester's
    _notify(lid, "delete")
    with _LOCK:
        _drop_tokens(lid)
        if folder(lid).exists():
            shutil.rmtree(folder(lid))


def update(lid: str, **fields) -> dict:
    with _LOCK:
        rec = {**_need(lid), **fields}
        _write_record(lid, rec)
        return rec


def set_name(lid: str, name: str, given: bool = False) -> dict:
    """The tester's name (given with the link: `given`; or heard by the interviewer): in tester.json, and one
    line in profile.md for the guide, saying which."""
    name = " ".join(str(name or "").split())
    if not name:
        raise StageError("No name given.")
    with _LOCK:
        rec = update(lid, name=name)
        p = folder(lid) / "profile.md"
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        lines = [x for x in lines if not x.startswith("Their name is ")]  # a corrected name replaces the old line
        lines.append((GIVEN_LINE if given else NAME_LINE).format(name=name))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return rec


def record_consent(lid: str) -> dict:
    """The gate's tap: the first consent's time is kept (the record of when they agreed)."""
    with _LOCK:
        rec = _need(lid)
        if rec.get("consent_at"):
            return rec
        return update(lid, consent_at=now())


def set_debrief_card(lid: str, value: str) -> dict:
    if value not in CARD_VALUES:
        raise StageError(f"debrief_card must be one of {', '.join(CARD_VALUES)}.")
    return update(lid, debrief_card=value)


def all_testers() -> list[dict]:
    """Every tester: each folder with a tester.json (revoked ones too), and any registered token whose
    folder has gone missing. Oldest first."""
    out: dict[str, dict] = {}
    d = common.LISTENERS_DIR
    for p in sorted(d.glob("t-*/tester.json")) if d.exists() else []:
        rec = profiles.read_json(p)
        if isinstance(rec, dict) and rec.get("lid"):
            out[rec["lid"]] = rec
    for token, entry in _registry()["tokens"].items():
        if entry.get("lid") not in out:
            out[entry["lid"]] = {**entry, "token": token, "opened_at": None, "missing": True}
    return sorted(out.values(), key=lambda r: (r.get("created") or "", r["lid"]))
