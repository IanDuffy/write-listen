"""The Tester Demo's routes, mounted by server.py's create_app before its own GET / (docs/tester-demo-design.md,
"Testers (B provides)").

    GET  /t/{token}                 the tester's link: sets the cookie, makes the folder on first open, 302 to /
    GET  /                          demo.enabled only: blank dot (no tester) / gate (intro not done) / the
                                    product page with class="tester" on <body>. With demo off this route is not
                                    registered, so server.py's own GET / answers as before.
    GET  /app                       {style, body, body_class}: the product page's parts for the gate's handoff
    POST /api/demo/consent          the gate's tap
    GET  /admin                     page/admin.html
    GET  /api/admin/testers         every tester's record and state, and the cost totals
    POST /api/admin/testers         {label, name?}: a new link (name: what the interviewer greets them by)
    POST /api/admin/greeting        {label, name?}: {name} it would greet them as, or null (it asks); nothing made
    POST /api/admin/testers/{lid}/revoke | reset | delete
    POST /api/admin/testers/{lid}/card    {value: auto|on|off}
    GET  /api/admin/testers/{lid}/study/{name}   the debrief summary, or a transcript as plain text

The admin routes answer on loopback (no forwarding headers: behind Caddy every request comes from
127.0.0.1) or with HTTP basic auth matching ADMIN_USER / ADMIN_PASSWORD from the environment; otherwise 401
when those are set (so a browser asks) and 403 when they are not.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import secrets as _secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from stages import common
from stages.common import ROOT, StageError

from . import state, testers

BLANK = ROOT / "page" / "blank.html"
GATE = ROOT / "page" / "gate.html"   # part C's
ADMIN = ROOT / "page" / "admin.html"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
FORWARDED = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded", "x-real-ip")
STUDY_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(md|jsonl|json)")
NO_STORE = {"Cache-Control": "no-store"}


class NewTester(BaseModel):
    label: str
    name: str | None = None   # what the interviewer greets them by (optional)


class Card(BaseModel):
    value: str


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
        for r in rows:
            r["link"] = testers.link(cfg, r["token"], base) if r.get("token") else None
        return {"testers": rows, "totals": state.totals(rows)}

    @app.post("/api/admin/testers")
    def admin_create(req: NewTester, request: Request) -> dict:
        guard(request)
        try:
            return testers.create(cfg, req.label, base=base_of(request), name=req.name)
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

    @app.get("/api/admin/testers/{lid}/study/{name}")
    def admin_study(lid: str, name: str, request: Request) -> Response:
        guard(request)
        _tester_or_404(lid)
        p = testers.folder(lid) / "study" / name
        if not STUDY_FILE.fullmatch(name) or not p.is_file():
            raise HTTPException(404, "No such file.")
        text = transcript_text(p) if name.endswith(".jsonl") else p.read_text(encoding="utf-8")
        return PlainTextResponse(text, headers=NO_STORE)
