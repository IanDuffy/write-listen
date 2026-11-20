"""The admin page and its API (docs/tester-demo.md, "Admin page"; acceptance check 10): the guard (loopback,
basic auth), the list, the actions, the study files, and the page itself in headless Chromium."""

import base64
import socket
import threading
import time

import pytest

import server
from demo import testers
from stages import common

from tester_fakes import client, demo_cfg, seed


@pytest.fixture
def dcfg(cfg):
    return demo_cfg(cfg)


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


@pytest.fixture
def no_admin_env(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("WL_BASE_URL", raising=False)


def test_loopback_is_allowed_without_a_password(dcfg, tmp_path, no_admin_env):
    c = client(dcfg, tmp_path)
    assert c.get("/admin").status_code == 200 and "Testers" in c.get("/admin").text
    assert c.get("/api/admin/testers").json() == {"testers": [], "totals": {"claude": 0, "gpt_live": 0, "total": 0,
                                                                            "estimated": 0}, "demo": True}


def test_elsewhere_without_credentials_configured_is_refused(dcfg, tmp_path, no_admin_env):
    c = client(dcfg, tmp_path, host="203.0.113.5")
    assert c.get("/admin").status_code == 403
    assert c.post("/api/admin/testers", json={"label": "X"}).status_code == 403
    assert c.get("/api/admin/testers", headers=basic("ian", "pw")).status_code == 403


def test_a_proxied_request_is_not_loopback(dcfg, tmp_path, no_admin_env):
    """Behind Caddy every request arrives from 127.0.0.1: the forwarding headers say it came from outside."""
    c = client(dcfg, tmp_path)
    assert c.get("/admin", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 403
    assert c.get("/admin", headers={"X-Forwarded-Proto": "https"}).status_code == 403


def test_basic_auth_against_the_environment(dcfg, tmp_path, no_admin_env, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "ian")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    c = client(dcfg, tmp_path, host="203.0.113.5")
    r = c.get("/admin")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert c.get("/admin", headers=basic("ian", "wrong")).status_code == 401
    assert c.get("/admin", headers=basic("eve", "s3cret")).status_code == 401
    assert c.get("/admin", headers={"Authorization": "Basic !!!"}).status_code == 401
    assert c.get("/admin", headers={"Authorization": "Bearer s3cret"}).status_code == 401
    assert c.get("/admin", headers=basic("ian", "s3cret")).status_code == 200
    assert c.get("/api/admin/testers", headers=basic("ian", "s3cret")).status_code == 200


def test_admin_answers_with_demo_on_without_a_tester_cookie(dcfg, tmp_path, no_admin_env):
    c = client(dcfg, tmp_path)
    assert c.get("/").status_code == 200 and "Testers" not in c.get("/").text  # the dot
    assert c.get("/admin").status_code == 200


def test_create_list_and_the_links_origin(dcfg, tmp_path, no_admin_env, monkeypatch):
    c = client(dcfg, tmp_path)
    got = c.post("/api/admin/testers", json={"label": "Pilot 1"}).json()
    assert got["lid"] == "t-pilot-1" and got["link"] == f"http://testserver/t/{got['token']}"
    proxied = c.post("/api/admin/testers", json={"label": "Pilot 2"},
                     headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "listen.example.com",
                              **basic("ian", "pw")})
    assert proxied.status_code == 403  # proxied with no credentials configured
    monkeypatch.setenv("WL_BASE_URL", "https://listen.example.com")
    rows = c.get("/api/admin/testers").json()["testers"]
    assert rows[0]["link"] == f"https://listen.example.com/t/{got['token']}" and rows[0]["stage"] == "not opened"
    assert c.post("/api/admin/testers", json={"label": "  "}).status_code == 400
    # Ian's notes (2026-11-16): a name for the interviewer to greet them by, optional
    named = c.post("/api/admin/testers", json={"label": "Pilot 3", "name": "Rolando"}).json()
    assert named["name"] == "Rolando" and testers.record("t-pilot-3")["name"] == "Rolando"
    rows = {r["lid"]: r for r in c.get("/api/admin/testers").json()["testers"]}
    assert rows["t-pilot-3"]["name"] == "Rolando" and rows["t-pilot-1"]["name"] is None
    # what the interviewer would greet a new tester as, before anything is made
    greet = lambda **body: c.post("/api/admin/greeting", json=body).json()["name"]  # noqa: E731
    assert greet(label="Rolando") == "Rolando" and greet(label="Design Team") is None and greet(label="Mom") is None
    assert greet(label="Design Team", name="Purnima") == "Purnima" and greet(label="Pilot 9") is None
    assert len(c.get("/api/admin/testers").json()["testers"]) == 2  # asking made no tester
    assert c.post("/api/admin/greeting", json={"label": "Rolando"}, headers={"X-Forwarded-Proto": "https"}).status_code == 403


def test_a_full_run_shows_stage_minutes_questions_debrief_and_cost(dcfg, tmp_path, no_admin_env):
    seed(dcfg, "Pilot 1", "done", name="Ada")
    seed(dcfg, "Pilot 2", "chapter", quiet_for=3 * 3600)
    got = client(dcfg, tmp_path).get("/api/admin/testers").json()
    a, b = got["testers"]
    assert (a["stage"], a["name"], a["title"], a["minutes_heard"], a["chapter_minutes"], a["questions"]) == \
        ("done", "Ada", "How Bridges Stand", 2.5, 10, 1)
    assert a["debrief"]["done"] is True and a["cost"]["total"] == 2.07
    assert b["stage"] == "chapter" and b["left_early"] is True and b["debrief"]["done"] is False
    assert got["totals"]["total"] == round(2.07 + b["cost"]["total"], 4)


def test_revoke_reset_card_and_delete_through_the_api(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg, "Pilot 1", "chapter")
    token = testers.record(lid)["token"]
    c = client(dcfg, tmp_path)
    assert c.post(f"/api/admin/testers/{lid}/card", json={"value": "off"}).json() == {"debrief_card": "off"}
    assert c.post(f"/api/admin/testers/{lid}/card", json={"value": "nah"}).status_code == 400
    moved = c.post(f"/api/admin/testers/{lid}/reset").json()["moved_to"]
    assert moved.startswith("_cleared_") and (common.LISTENERS_DIR / moved / "chapters").is_dir()
    row = c.get("/api/admin/testers").json()["testers"][0]
    assert row["stage"] == "not opened" and row["token"] == token and row["debrief_card"] == "off"
    c.post(f"/api/admin/testers/{lid}/revoke")
    row = c.get("/api/admin/testers").json()["testers"][0]
    assert row["revoked"] is True and row["link"] is None
    assert c.post(f"/api/admin/testers/{lid}/delete").json() == {"ok": True}
    assert c.get("/api/admin/testers").json()["testers"] == []
    assert not (common.LISTENERS_DIR / lid).exists()
    assert c.post("/api/admin/testers/t-nobody/reset").status_code == 404
    assert c.post("/api/admin/testers/t-nobody/delete").status_code == 404


def test_the_study_files_summary_and_a_readable_transcript(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg, "Pilot 1", "done")
    c = client(dcfg, tmp_path)
    assert "Like someone talking just to me" in c.get(f"/api/admin/testers/{lid}/study/debrief_summary.md").text
    t = c.get(f"/api/admin/testers/{lid}/study/debrief-s2.jsonl").text
    assert "[10:20:00] interviewer: What's one thing that stood out to you?" in t and "{" not in t
    assert c.get(f"/api/admin/testers/{lid}/study/missing.md").status_code == 404
    assert c.get(f"/api/admin/testers/{lid}/study/..debrief.json").status_code == 404
    assert c.get(f"/api/admin/testers/{lid}/study/%2e%2e%2ftester.json").status_code == 404
    assert client(dcfg, tmp_path, host="203.0.113.5").get(
        f"/api/admin/testers/{lid}/study/debrief_summary.md").status_code == 403


# --- the page, in headless Chromium -----------------------------------------------------------------

@pytest.fixture
def site(dcfg, tmp_path, no_admin_env):
    import uvicorn

    sync_api = pytest.importorskip("playwright.sync_api")  # the browser tests only: the rest run without it

    runs = tmp_path / "runs"
    runs.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(server.Pipeline(runs_dir=runs), dcfg,
                                                          remember_on_start=False),
                                        host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not srv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    pw = sync_api.sync_playwright().start()
    browser = pw.chromium.launch()
    yield f"http://127.0.0.1:{port}", browser
    browser.close()
    pw.stop()
    srv.should_exit = True


def test_the_admin_page_lists_creates_and_acts_with_confirms(dcfg, site):
    base, browser = site
    seed(dcfg, "Pilot 1", "done", name="Ada")
    page = browser.new_page()
    dialogs = []

    def answer(d):
        dialogs.append(d.message)
        d.accept() if d.type == "confirm" else d.dismiss()
    page.on("dialog", answer)
    page.goto(base + "/admin")
    page.wait_for_selector("tr[data-lid='t-pilot-1']")
    row = page.inner_text("tr[data-lid='t-pilot-1']")
    for want in ("Pilot 1", "Ada", "done", "How Bridges Stand", "2.5 of 10 min heard", "Done", "$2.07"):
        assert want in row
    assert page.inner_text("#total").startswith("Total $2.07")
    assert page.get_attribute("tr[data-lid='t-pilot-1'] a[href$='debrief_summary.md']", "href")

    # Beside the form: what the interviewer will greet them as, or that it will ask.
    page.fill("#label", "Design Team")
    page.wait_for_function("document.getElementById('greet').textContent.includes('will ask their name')")
    page.fill("#label", "Rolando")
    page.wait_for_function("document.getElementById('greet').textContent.includes('Hi, is this Rolando?')")
    page.fill("#label", "Pilot 2")
    page.wait_for_function("document.getElementById('greet').textContent.includes('will ask their name')")
    page.fill("#name", "Rolando")
    page.wait_for_function("document.getElementById('greet').textContent.includes('Hi, is this Rolando?')")
    page.click("#new button")
    page.wait_for_selector("tr[data-lid='t-pilot-2']")
    assert testers.record("t-pilot-2")["name"] == "Rolando" and page.input_value("#name") == ""
    assert "Rolando" in page.inner_text("tr[data-lid='t-pilot-2']")
    # The new link shows in a box on the page, never in a prompt() (refused by some browsers: it said
    # "Couldn't create" though the tester was made).
    page.wait_for_selector("#linkbox")
    assert page.input_value("#linkbox").endswith("/t/" + testers.record("t-pilot-2")["token"])
    assert "Created Pilot 2, greeted as Rolando." in page.inner_text("#msg") and "Couldn't" not in page.inner_text("#msg")
    assert not any("Copy the link" in m for m in dialogs)
    assert page.inner_text("#greet") == ""

    page.click("tr[data-lid='t-pilot-2'] button[data-do='revoke']")
    page.wait_for_function("document.querySelector(\"tr[data-lid='t-pilot-2']\").innerText.includes('revoked')")
    assert any("Revoke Pilot 2" in m for m in dialogs) and testers.record("t-pilot-2")["token"] is None

    # The End demo button for a tester who comes back (it replaced the library's debrief card, 2026-11-18): the
    # control reads so; the stored field is still debrief_card with its values.
    card = "tr[data-lid='t-pilot-1'] select[data-do='card']"
    assert page.get_attribute(card, "aria-label") == "End demo button"
    assert page.eval_on_selector_all(card + " option", "(os) => os.map((o) => [o.value, o.textContent])") == [
        ["auto", "End demo: auto"], ["on", "End demo: show"], ["off", "End demo: hide"]]
    assert "card" not in page.inner_text("tr[data-lid='t-pilot-1']").lower()
    page.select_option(card, "off")
    deadline = time.monotonic() + 5
    while testers.record("t-pilot-1")["debrief_card"] != "off" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert testers.record("t-pilot-1")["debrief_card"] == "off"
    page.wait_for_function("document.getElementById('msg').innerText.includes('End demo button hidden')")

    page.click("tr[data-lid='t-pilot-2'] button[data-do='delete']")
    page.wait_for_function("!document.querySelector(\"tr[data-lid='t-pilot-2']\")")
    assert any("cannot be undone" in m for m in dialogs) and not (common.LISTENERS_DIR / "t-pilot-2").exists()


def test_the_admin_page_fits_a_phone(dcfg, site):
    base, browser = site
    seed(dcfg, "Pilot 1", "done", name="Ada")
    page = browser.new_page(viewport={"width": 375, "height": 812})
    page.goto(base + "/admin")
    page.wait_for_selector("tr[data-lid='t-pilot-1']")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_rehearsal_through_the_api(dcfg, tmp_path, no_admin_env):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "faststart"))
    from rehearsal_fakes import make_source
    dcfg["rehearsal"] = {"source": str(make_source(tmp_path / "source-chapter"))}
    c = client(dcfg, tmp_path)
    made = c.post("/api/admin/testers", json={"label": "Voice Test", "rehearsal": True}).json()
    assert made["chapter"] == "rehearsal" and testers.record(made["lid"])["chapter"] == "rehearsal"
    plain = c.post("/api/admin/testers", json={"label": "Pilot 1"}).json()
    assert "chapter" not in testers.record(plain["lid"])
    rows = {r["lid"]: r for r in c.get("/api/admin/testers").json()["testers"]}
    assert rows[made["lid"]]["chapter_kind"] == "rehearsal" and rows[plain["lid"]]["chapter_kind"] == "real"
    assert rows[plain["lid"]]["rehearsal"] is False and rows[plain["lid"]]["chapter_default"] == "real"
    url = f"/api/admin/testers/{plain['lid']}/chapter"
    assert c.post(url, json={"value": "rehearsal"}).json() == {"chapter": "rehearsal"}
    assert c.post(url, json={"value": "maybe"}).status_code == 400
    assert c.post("/api/admin/testers/t-nobody/chapter", json={"value": "real"}).status_code == 404
    dcfg["rehearsal"]["source"] = str(tmp_path / "gone")
    res = c.post(url, json={"value": "rehearsal"})
    assert res.status_code == 400 and "not there" in res.json()["detail"]
    assert c.post("/api/admin/testers", json={"label": "X", "rehearsal": True}).status_code == 400
    assert c.post(url, json={"value": "real"}).json() == {"chapter": "real"}
    page = c.get("/admin").text
    assert 'data-do="chapter"' in page and 'id="rehearsal"' in page and 'id="default-chapter"' in page



# --- the earpiece view (Ian, 2026-11-20) ------------------------------------------------------------

def test_earpiece_view_through_the_api(dcfg, tmp_path, no_admin_env):
    c = client(dcfg, tmp_path)
    on = c.post("/api/admin/testers", json={"label": "Ian Run", "earpiece_view": True}).json()
    off = c.post("/api/admin/testers", json={"label": "Pilot 1"}).json()
    assert on["earpiece_view"] is True and testers.record(on["lid"])["earpiece_view"] is True
    assert "earpiece_view" not in testers.record(off["lid"])
    got = c.get("/api/admin/testers").json()
    rows = {r["lid"]: r for r in got["testers"]}
    assert got["demo"] is True
    assert rows[on["lid"]]["earpiece_view"] is True and rows[off["lid"]]["earpiece_view"] is False
    url = f"/api/admin/testers/{off['lid']}/earpiece"
    assert c.post(url, json={"value": "on"}).json() == {"earpiece_view": True}
    assert testers.record(off["lid"])["earpiece_view"] is True
    assert c.post(url, json={"value": "off"}).json() == {"earpiece_view": False}
    assert c.post(url, json={"value": "yes"}).status_code == 400
    assert c.post("/api/admin/testers/t-nobody/earpiece", json={"value": "on"}).status_code == 404
    page = c.get("/admin").text
    assert 'data-do="earpiece"' in page and 'id="earpiece-view"' in page and "/admin/earpiece?lid=" in page


def test_earpiece_history_is_guarded(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg, "Pilot 1", "chapter")
    c = client(dcfg, tmp_path, host="203.0.113.5")
    for path in ("/admin/earpiece", f"/api/admin/earpiece/{lid}", f"/api/admin/earpiece/{lid}/20261120-100000",
                 "/api/admin/earpiece/own"):
        assert c.get(path).status_code in (401, 403), path


def _conversation(folder, sid="20261120-100000", fates=True):
    """A conversation with lines, two looks (plan: one done, one new, one removed), a held then sent note and a
    note dropped for the gap."""
    from voice.gpt_live import session as live_session
    direct = live_session.notes()["direct"]
    plan1 = [{"item": "Say what it does", "state": "open"}, {"item": "Find the spark", "state": "open"},
             {"item": "Ask about the weekend", "state": "open"}]
    plan2 = [{"item": "say  what it does", "state": "done"}, {"item": "Find the spark", "state": "open"},
             {"item": "Pitch a chapter", "state": "open"}]
    rows = [
        {"t": "2026-11-20T10:00:00", "who": "session", "live_id": "sess_1"},
        {"t": "2026-11-20T10:00:02", "who": "guide", "text": "Hi there.", "mode": "live"},
        {"t": "2026-11-20T10:00:06", "who": "listener", "text": "Hi, I just got back from a walk.", "mode": "live"},
        {"t": "2026-11-20T10:00:10", "who": "director", "id": 1, "trigger": "turn_end", "speaking": "none",
         "read": "Warm, early.", "plan": plan1, "next": "Say what it does", "changed": True, "kind": "now",
         "about": "offer", "note": "Offer to say what you do.", "ms": 2000, "cost_usd": 0.01},
        {"t": "2026-11-20T10:00:14", "who": "page", "text": direct.replace("{note}", "Offer to say what you do."),
         "mode": "live"},
        {"t": "2026-11-20T10:00:20", "who": "guide", "text": "Want to hear what I do?", "mode": "live"},
        {"t": "2026-11-20T10:00:25", "who": "tool", "name": "get_status"},
        {"t": "2026-11-20T10:00:30", "who": "director", "id": 2, "trigger": "cadence", "speaking": "guide",
         "read": "Explained.", "plan": plan2, "next": "Pitch a chapter", "changed": True, "kind": "when_it_fits",
         "about": "pitch", "note": "Pitch the walk.", "ms": 1500, "cost_usd": 0.02},
        {"t": "2026-11-20T10:00:31", "who": "director", "id": 3, "trigger": "silence", "speaking": "none",
         "error": "timeout", "ms": 6000, "cost_usd": 0.0},
    ]
    if fates:
        rows[4:4] = [{"t": "2026-11-20T10:00:11", "who": "director_fate", "id": 1, "fate": "held", "reason": None,
                      "detail": None, "page_secs": 11.0, "held_ms": None},
                     {"t": "2026-11-20T10:00:14", "who": "director_fate", "id": 1, "fate": "sent", "reason": None,
                      "detail": None, "page_secs": 14.2, "held_ms": 3200}]
        rows.append({"t": "2026-11-20T10:00:32", "who": "director_fate", "id": 2, "fate": "dropped", "reason": "gap",
                     "detail": "18 s after the last", "page_secs": 32.0, "held_ms": None})
    from tester_fakes import jsonl
    jsonl(folder / "sessions" / f"{sid}.jsonl", rows)


def test_earpiece_history_list_and_timeline(dcfg, tmp_path, no_admin_env):
    lid = seed(dcfg, "Pilot 1", "chapter")
    folder = testers.folder(lid)
    _conversation(folder)
    _conversation(folder, "20261119-090000-2", fates=False)
    (folder / "sessions" / "log.jsonl").write_text("", encoding="utf-8")   # not a conversation
    c = client(dcfg, tmp_path)

    got = c.get(f"/api/admin/earpiece/{lid}").json()
    assert got["label"] == "Pilot 1"
    sessions = [x["session"] for x in got["conversations"]]
    assert sessions[:2] == ["20261120-100000", "20261119-090000-2"] and "log" not in sessions
    first = got["conversations"][0]
    assert (first["lines"], first["looks"], first["notes"], first["sent"], first["dropped"], first["errors"]) == \
        (3, 3, 2, 1, 1, 1)
    assert first["cost_usd"] == 0.03 and first["minutes"] == 0.5

    t = c.get(f"/api/admin/earpiece/{lid}/20261120-100000").json()
    kinds = [x["kind"] for x in t["items"]]
    assert kinds == ["session", "line", "line", "look", "note", "line", "tool", "look", "look"]
    assert t["director"] == {"looks": 3, "notes": 2, "sent": 1, "dropped": 1, "held": 1, "errors": 1,
                             "cost_usd": 0.03}
    look1, look2, look3 = [x for x in t["items"] if x["kind"] == "look"]
    assert all(p["change"] is None for p in look1["plan"]) and look1["removed"] == []
    assert [(p["item"], p["change"]) for p in look2["plan"]] == [
        ("say  what it does", "done"), ("Find the spark", None), ("Pitch a chapter", "new")]
    assert look2["removed"] == ["Ask about the weekend"]
    assert look1["asked_secs"] == 8 and look1["secs"] == 10 and look1["clock"] == "10:00:10"
    assert look1["fate"]["fate"] == "sent" and look1["fate"]["held_secs"] == 3.2 and look1["fate"]["inferred"] is False
    assert look1["delivered"]["secs"] == 14
    assert look1["reply"]["after_secs"] == 6 and look1["reply"]["text"] == "Want to hear what I do?"
    note = next(x for x in t["items"] if x["kind"] == "note")
    assert note["whisper"] is True and note["look"] == 1
    assert look2["fate"]["fate"] == "dropped" and look2["fate"]["reason"] == "gap"
    assert look2["fate"]["detail"] == "18 s after the last" and look2["delivered"] is None
    assert look3["error"] == "timeout" and look3["fate"] is None
    assert not any(x["kind"] == "director_fate" for x in t["items"])

    old = c.get(f"/api/admin/earpiece/{lid}/20261119-090000-2").json()
    o1 = next(x for x in old["items"] if x["kind"] == "look")
    assert o1["fate"]["fate"] == "sent" and o1["fate"]["inferred"] is True and o1["fate"]["secs"] == 14
    assert c.get(f"/api/admin/earpiece/{lid}/log").status_code == 404
    assert c.get(f"/api/admin/earpiece/{lid}/20261101-000000").status_code == 404
    assert c.get(f"/api/admin/earpiece/{lid}/..%2Ftester").status_code == 404
    assert c.get("/api/admin/earpiece/t-nobody").status_code == 404


def test_earpiece_history_of_your_own_with_demo_off(cfg, tmp_path, no_admin_env):
    from tester_fakes import demo_cfg
    ocfg = demo_cfg(cfg, enabled=False)
    own = common.listener_dir(ocfg)
    _conversation(own)
    c = client(ocfg, tmp_path)
    assert c.get("/api/admin/testers").json()["demo"] is False
    got = c.get("/api/admin/earpiece/own").json()
    assert got["label"] == "Your own" and got["conversations"][0]["session"] == "20261120-100000"
    assert c.get("/api/admin/earpiece/own/20261120-100000").json()["director"]["sent"] == 1


def test_the_admin_page_earpiece_controls_and_history(dcfg, site):
    base, browser = site
    lid = seed(dcfg, "Pilot 1", "chapter")
    _conversation(testers.folder(lid))
    page = browser.new_page()
    page.goto(base + "/admin")
    sel = f"tr[data-lid='{lid}'] select[data-do='earpiece']"
    page.wait_for_selector(sel)
    assert page.input_value(sel) == "off"
    page.select_option(sel, "on")
    deadline = time.monotonic() + 5
    while not testers.record(lid).get("earpiece_view") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert testers.record(lid)["earpiece_view"] is True
    page.wait_for_function("document.getElementById('msg').innerText.includes('earpiece view on')")
    assert page.get_attribute(f"tr[data-lid='{lid}'] a[href^='/admin/earpiece']", "href") == f"/admin/earpiece?lid={lid}"
    assert page.is_hidden("#own-earpiece")

    phone = browser.new_page(viewport={"width": 375, "height": 812})
    phone.goto(base + f"/admin/earpiece?lid={lid}")
    phone.wait_for_selector("a[href*='session=20261120-100000']")
    phone.click("a[href*='session=20261120-100000']")
    phone.wait_for_selector("#timeline li.look")
    text = phone.inner_text("#timeline")
    for want in ("Hi, I just got back from a walk.", "Offer to say what you do.", "Held 3.2 s, sent at 0:14",
                 "Dropped at 0:32: too soon after the last whisper", "ended +6 s", "Failed: timeout"):
        assert want in text, want
    assert phone.get_attribute("li.look[data-id='2'] li[data-change='new']", "data-state") == "open"
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
