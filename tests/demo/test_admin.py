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
                                                                            "estimated": 0}}


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

    page.select_option("tr[data-lid='t-pilot-1'] select[data-do='card']", "off")
    deadline = time.monotonic() + 5
    while testers.record("t-pilot-1")["debrief_card"] != "off" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert testers.record("t-pilot-1")["debrief_card"] == "off"

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
