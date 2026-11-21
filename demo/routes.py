"""The Tester Demo's routes, mounted by server.py's create_app before its own GET / (docs/tester-demo-design.md,
"Testers (B provides)").

    GET  /t/{token}                 the tester's link: sets the cookie, makes the folder on first open, 302 to /
    GET  /                          demo.enabled only: blank dot (no tester) / gate (intro not done) / the
                                    product page with class="tester" on <body>. With demo off this route is not
                                    registered, so server.py's own GET / answers as before.
    GET  /app                       {style, body, body_class}: the product page's parts for the gate's handoff
    POST /api/demo/consent          the gate's tap
    POST /api/demo/event            {event, phase?, chapter_t?, detail?}: a button press on the tester's page
                                    (end_demo, play, pause, seek, ask, tap_talk), appended to their
                                    study/events.jsonl for the debrief's account of what they did
                                    (demo/debrief.py). Tester mode only; 404 without a tester, 400 for an
                                    event it doesn't know. Also {event: "audio_route", data}: page/speaker.js's
                                    record of the phone's audio route (not a press; the admin transcript shows it)
    GET  /admin                     page/admin.html
    GET  /api/admin/testers         every tester's record and state, and the cost totals
    POST /api/admin/testers         {label, name?, rehearsal?}: a new link (name: what the interviewer greets them
                                    by; rehearsal: their chapters replay a finished one, faststart/rehearsal.py)
    POST /api/admin/greeting        {label, name?}: {name} it would greet them as, or null (it asks); nothing made
    POST /api/admin/testers/{lid}/revoke | reset | delete
    POST /api/admin/testers/{lid}/card    {value: auto|on|off}: tester.json's debrief_card. Since Ian's notes
                                    of 2026-11-18 it governs the End demo button for a tester who comes back
                                    with the debrief not done (it was the library's debrief card): auto shows
                                    it once a chapter was started, on always, off never
    POST /api/admin/testers/{lid}/chapter {value: real|rehearsal}: tester.json's chapter (400 when there is no
                                    rehearsal chapter to replay). GET rows carry chapter_kind (the effective value:
                                    tester.json's, else demo.chapter), rehearsal (their newest chapter was one)
                                    and chapter_default (demo.chapter)
    GET  /api/admin/testers/{lid}/study/{name}   the debrief summary, or a transcript as plain text
    POST /api/admin/testers/{lid}/earpiece {value: on|off}: tester.json's earpiece_view: their page shows the
                                    earpiece panel (page/earpiece.js; Ian, 2026-11-20). GET rows carry earpiece_view,
                                    and the list `demo` (whether tester mode is on)
    GET  /admin/earpiece            page/admin_earpiece.html (?lid=<lid or own>&session=<id>): the earpiece history
    GET  /api/admin/earpiece/{lid}  a listener's conversations with their earpiece's counts, newest first
                                    (voice/gpt_live/earpiece_history.py); lid "own" is the active listener
                                    (Ian's own when demo mode is off)
    GET  /api/admin/earpiece/{lid}/{session}   one conversation's timeline: lines, looks, notes and their fates
    GET  /admin/transcript          page/admin_transcript.html (?lid=<lid or own>): the whole visit as one transcript
                                    (Ian, 2026-11-21): intro, every conversation with the guide, the chapter and
                                    questions asked in it, presses, debrief (demo/visit.py; no earpiece notes)
    GET  /api/admin/transcript/{lid}        that timeline as JSON {lid, label, name, started, ended, items}
    GET  /api/admin/transcript/{lid}/text   the same as a plain-text download

The admin routes answer on loopback (no forwarding headers: behind Caddy every request comes from
127.0.0.1) or with HTTP basic auth matching ADMIN_USER / ADMIN_PASSWORD from the environment; otherwise 401
when those are set (so a browser asks) and 403 when they are not.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets as _secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from stages import common
from stages.common import ROOT, StageError

from . import debrief, state, testers

BLANK = ROOT / "page" / "blank.html"
GATE = ROOT / "page" / "gate.html"   # part C's
ADMIN = ROOT / "page" / "admin.html"
ADMIN_EARPIECE = ROOT / "page" / "admin_earpiece.html"
ADMIN_TRANSCRIPT = ROOT / "page" / "admin_transcript.html"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
FORWARDED = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded", "x-real-ip")
STUDY_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(md|jsonl|json)")
NO_STORE = {"Cache-Control": "no-store"}


class NewTester(BaseModel):
    label: str
    name: str | None = None   # what the interviewer greets them by (optional)
    rehearsal: bool = False   # their chapters are rehearsals (faststart/rehearsal.py): replayed, nothing paid
    earpiece_view: bool = False   # their page shows the earpiece panel (page/earpiece.js)


class Card(BaseModel):
    value: str


class Event(BaseModel):
    """A button press (demo/debrief.py EVENTS, PHASES); the lengths bound what a page can write."""
    event: str = Field(max_length=40)
    phase: str | None = Field(default=None, max_length=40)
    chapter_t: float | None = None
    detail: str | None = Field(default=None, max_length=2000)
    data: dict | None = None   # audio_route only (page/speaker.js), bounded by debrief.audio_fields


def demo_on(cfg: dict) -> bool:
    return bool((cfg.get("demo") or {}).get("enabled"))


def blank() -> HTMLResponse:
    return HTMLResponse(BLANK.read_text(encoding="utf-8"), headers=NO_STORE)


def live_page() -> str:
    from voice.gpt_live import routes as live_routes  # the product page as part D leaves it
    return live_routes.LIVE_PAGE.read_text(encoding="utf-8")


def tester_page(html: str) -> str:
    """The product page with "tester" added to <body>'s classes (part D's tester mode hides by it)."""
    m = re.search(r"<body\b([^>]*)>", html)
    if not m:
        return html
    attrs = m.group(1)
    cm = re.search(r'class="([^"]*)"', attrs)
    attrs = (attrs[:cm.start()] + f'class="{cm.group(1)} tester"' + attrs[cm.end():]) if cm \
        else f'{attrs} class="tester"'
    return html[:m.start()] + f"<body{attrs}>" + html[m.end():]


def app_parts(html: str) -> dict:
    """The product page's <style> text and its body markup without <script> tags, for the gate to inject."""
    style = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    m = re.search(r"<body\b([^>]*)>(.*)</body>", html, re.S)
    body = m.group(2) if m else ""
    body = re.sub(r"<script\b[^>]*>.*?</script>", "", body, flags=re.S | re.I)
    cm = re.search(r'class="([^"]*)"', m.group(1)) if m else None
    return {"style": style, "body": body.strip(), "body_class": ((cm.group(1) if cm else "") + " tester").strip()}


def secure(cfg: dict, request: Request) -> bool:
    want = (cfg.get("demo") or {}).get("secure_cookie", "auto")
    if want is True or str(want).lower() == "true":
        return True
    if want is False or str(want).lower() == "false":
        return False
    return request.headers.get("x-forwarded-proto", "").lower() == "https" or request.url.scheme == "https"


def tester_of(request: Request) -> dict | None:
    """The tester whose cookie this request carries (part A's middleware may have put it on request.state)."""
    got = getattr(request.state, "tester", None)
    return got if isinstance(got, dict) else testers.tester_for_request(request.scope)


# --- the admin's guard --------------------------------------------------------------------

def loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK and not any(h in request.headers for h in FORWARDED)


def basic_ok(request: Request) -> bool:
    user, pw = os.environ.get("ADMIN_USER", ""), os.environ.get("ADMIN_PASSWORD", "")
    if not (user and pw):
        return False
    scheme, _, got = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        u, _, p = base64.b64decode(got.strip()).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    ok_u = _secrets.compare_digest(u.encode(), user.encode())
    ok_p = _secrets.compare_digest(p.encode(), pw.encode())
    return ok_u and ok_p


def guard(request: Request) -> None:
    """Loopback or basic auth, else refuse. Also leaves any tester's listener scope (a cookie Ian's own
    browser carries from trying a link): the admin reads every folder by its own path."""
    if not (loopback(request) or basic_ok(request)):
        if os.environ.get("ADMIN_USER") and os.environ.get("ADMIN_PASSWORD"):
            raise HTTPException(401, "Sign in.", headers={"WWW-Authenticate": 'Basic realm="admin"'})
        raise HTTPException(403, "Forbidden.")
    var = getattr(common, "REQUEST_LISTENER", None)  # part A's ContextVar, when it is there
    if var is not None:
        var.set(None)


def _tester_or_404(lid: str) -> dict:
    rec = testers.record(lid)
    if rec is None:
        raise HTTPException(404, "No such tester.")
    return rec


def transcript_text(path) -> str:
    """A transcript as plain lines, for reading on a phone."""
    out = []
    for rec in state.read_jsonl(path):
        text = str(rec.get("text") or "").strip()
        if text:
            out.append(f"[{str(rec.get('t') or '')[11:19]}] {rec.get('who', '?')}: {text}")
    return "\n".join(out) + "\n"


# --- mounting ---------------------------------------------------------------------------

def mount(app: FastAPI, cfg: dict) -> None:
    dcfg = cfg.get("demo") or {}
    testers.COOKIE = dcfg.get("cookie") or testers.COOKIE

    @app.get("/t/{token}")
    def open_link(token: str, request: Request) -> Response:
        rec = testers.open_link(cfg, token)
        if rec is None:
            return blank()
        resp = RedirectResponse("/", status_code=302, headers={**NO_STORE, "Referrer-Policy": "no-referrer"})
        resp.set_cookie(testers.COOKIE, token, max_age=int(dcfg.get("cookie_days", 365)) * 86400, path="/",
                        httponly=True, samesite="lax", secure=secure(cfg, request))
        return resp

    if demo_on(cfg):  # registered first, so it wins over server.py's GET /; with demo off it isn't there at all
        @app.get("/", response_class=HTMLResponse)
        def demo_page(request: Request) -> Response:
            rec = tester_of(request)
            if rec is None:
                return blank()
            if not rec.get("opened_at"):  # back after a reset: the same link, a fresh start
                rec = testers.mark_opened(rec["lid"])
            if not rec.get("intro_done"):
                return HTMLResponse(GATE.read_text(encoding="utf-8"), headers=NO_STORE) if GATE.exists() else blank()
            return HTMLResponse(tester_page(live_page()), headers=NO_STORE)

    @app.get("/app")
    def app_markup(request: Request) -> Response:
        if tester_of(request) is None:
            return Response(status_code=404)
        return JSONResponse(app_parts(live_page()), headers=NO_STORE)

    @app.post("/api/demo/consent")
    def consent(request: Request) -> dict:
        rec = tester_of(request)
        if rec is None:
            raise HTTPException(404)
        return {"consent_at": testers.record_consent(rec["lid"])["consent_at"]}

    @app.post("/api/demo/event")
    def event(req: Event, request: Request) -> dict:
        """What the tester pressed, for the debrief (study/, never sessions/: memory reads those)."""
        rec = tester_of(request) if demo_on(cfg) else None
        if rec is None:
            raise HTTPException(404)
        if req.event not in debrief.EVENTS:
            raise HTTPException(400, "Unknown event.")
        if req.event == debrief.AUDIO_ROUTE:   # page/speaker.js: the phone's audio route, not a press
            if req.data is not None and len(json.dumps(req.data, default=str)) > 8000:
                raise HTTPException(413, "Too large.")
            saved = debrief.add_event(testers.folder(rec["lid"]), req.event, data=req.data)
            return {"ok": True, "saved": saved is not None}
        saved = debrief.add_event(testers.folder(rec["lid"]), req.event, req.phase, req.chapter_t, req.detail)
        return {"ok": True, "saved": saved is not None}

    # --- admin -----------------------------------------------------------------------

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Response:
        guard(request)
        return HTMLResponse(ADMIN.read_text(encoding="utf-8"), headers=NO_STORE)

    def base_of(request: Request) -> str:
        """The link's origin: WL_BASE_URL or demo.base_url, else the address the admin page was opened at."""
        if os.environ.get("WL_BASE_URL") or dcfg.get("base_url"):
            return testers.base_url(cfg)
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        return f"{proto}://{host}"

    @app.get("/api/admin/testers")
    def admin_list(request: Request) -> dict:
        guard(request)
        rows = state.all_states(cfg)
        base = base_of(request)
        default = testers.chapter_of(cfg, None)
        for r in rows:
            r["link"] = testers.link(cfg, r["token"], base) if r.get("token") else None
            r["chapter_default"] = default
        return {"testers": rows, "totals": state.totals(rows), "demo": demo_on(cfg)}

    @app.post("/api/admin/testers")
    def admin_create(req: NewTester, request: Request) -> dict:
        guard(request)
        try:
            return testers.create(cfg, req.label, base=base_of(request), name=req.name,
                                  chapter="rehearsal" if req.rehearsal else None, earpiece_view=req.earpiece_view)
        except StageError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/admin/greeting")
    def admin_greeting(req: NewTester, request: Request) -> dict:
        """What the interviewer would greet a tester made with this label and name as (null: it asks)."""
        guard(request)
        return {"name": testers.greeting_name(req.label, req.name)}

    @app.post("/api/admin/testers/{lid}/revoke")
    def admin_revoke(lid: str, request: Request) -> dict:
        guard(request)
        _tester_or_404(lid)
        testers.revoke(lid)
        return {"ok": True}

    @app.post("/api/admin/testers/{lid}/reset")
    def admin_reset(lid: str, request: Request) -> dict:
        guard(request)
        _tester_or_404(lid)
        moved = testers.reset(lid, cfg)
        return {"ok": True, "moved_to": str(moved.relative_to(common.LISTENERS_DIR))}

    @app.post("/api/admin/testers/{lid}/delete")
    def admin_delete(lid: str, request: Request) -> dict:
        guard(request)
        if testers.record(lid) is None and lid not in {r["lid"] for r in testers.all_testers()}:
            raise HTTPException(404, "No such tester.")
        try:
            testers.delete(lid)
        except StageError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}

    @app.post("/api/admin/testers/{lid}/card")
    def admin_card(lid: str, req: Card, request: Request) -> dict:
        guard(request)
        _tester_or_404(lid)
        try:
            return {"debrief_card": testers.set_debrief_card(lid, req.value)["debrief_card"]}
        except StageError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/admin/testers/{lid}/chapter")
    def admin_chapter(lid: str, req: Card, request: Request) -> dict:
        guard(request)
        _tester_or_404(lid)
        try:
            return {"chapter": testers.set_chapter(cfg, lid, req.value)["chapter"]}
        except StageError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/admin/testers/{lid}/earpiece")
    def admin_earpiece_view(lid: str, req: Card, request: Request) -> dict:
        guard(request)
        _tester_or_404(lid)
        if req.value not in ("on", "off"):
            raise HTTPException(400, "earpiece must be on or off.")
        return {"earpiece_view": testers.earpiece_view(testers.set_earpiece_view(lid, req.value == "on"))}

    @app.get("/admin/earpiece", response_class=HTMLResponse)
    def admin_earpiece_page(request: Request) -> Response:
        guard(request)
        return HTMLResponse(ADMIN_EARPIECE.read_text(encoding="utf-8"), headers=NO_STORE)

    def earpiece_folder(lid: str) -> tuple:
        """(folder, label) for a tester's id, or "own": the active listener (after guard(), so never a tester's
        cookie scope). The Transcript view's too."""
        if lid == "own":
            return common.listener_dir(cfg), "Your own"
        rec = _tester_or_404(lid)
        return testers.folder(lid), rec.get("label") or lid

    def visit_of(lid: str) -> dict:
        from . import visit
        d, label = earpiece_folder(lid)
        rec = testers.record(lid) if lid != "own" else None
        name = (rec or {}).get("name") or (rec or {}).get("given_name") or (None if rec else "You")
        return {"lid": lid, "label": label, **visit.read(d, name)}

    @app.get("/admin/transcript", response_class=HTMLResponse)
    def admin_transcript_page(request: Request) -> Response:
        guard(request)
        return HTMLResponse(ADMIN_TRANSCRIPT.read_text(encoding="utf-8"), headers=NO_STORE)

    @app.get("/api/admin/transcript/{lid}")
    def admin_transcript(lid: str, request: Request) -> JSONResponse:
        guard(request)
        return JSONResponse(visit_of(lid), headers=NO_STORE)

    @app.get("/api/admin/transcript/{lid}/text")
    def admin_transcript_text(lid: str, request: Request) -> Response:
        guard(request)
        from . import visit
        got = visit_of(lid)
        title = got["label"] + (f" ({got['name']})" if got.get("name") and got["name"] not in (got["label"], "You")
                                else "")
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", lid)
        return PlainTextResponse(visit.text(got, title), headers={
            **NO_STORE, "Content-Disposition": f'attachment; filename="{safe}-transcript.txt"'})

    @app.get("/api/admin/earpiece/{lid}")
    def admin_earpiece_list(lid: str, request: Request) -> JSONResponse:
        guard(request)
        from voice.gpt_live import earpiece_history
        d, label = earpiece_folder(lid)
        return JSONResponse({"lid": lid, "label": label, "conversations": earpiece_history.conversations(d)},
                            headers=NO_STORE)

    @app.get("/api/admin/earpiece/{lid}/{session}")
    def admin_earpiece_timeline(lid: str, session: str, request: Request) -> JSONResponse:
        guard(request)
        from voice.gpt_live import earpiece_history
        d, label = earpiece_folder(lid)
        try:
            got = earpiece_history.timeline(d, session)
        except FileNotFoundError:
            raise HTTPException(404, "No such conversation.") from None
        return JSONResponse({**got, "lid": lid, "label": label}, headers=NO_STORE)

    @app.get("/api/admin/testers/{lid}/study/{name}")
    def admin_study(lid: str, name: str, request: Request) -> Response:
        guard(request)
        _tester_or_404(lid)
        p = testers.folder(lid) / "study" / name
        if not STUDY_FILE.fullmatch(name) or not p.is_file():
            raise HTTPException(404, "No such file.")
        text = transcript_text(p) if name.endswith(".jsonl") else p.read_text(encoding="utf-8")
        return PlainTextResponse(text, headers=NO_STORE)
