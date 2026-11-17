"""Tester links, cookies, folders and consent (docs/tester-demo.md, "Tester links and consent"; acceptance
checks 1 and 2): demo/testers.py and the tester routes in demo/routes.py."""

import json
import re

import pytest

from demo import routes, testers
from overnight import profiles
from stages import common
from stages.common import StageError

from tester_fakes import client, demo_cfg


@pytest.fixture
def dcfg(cfg):
    return demo_cfg(cfg)


def scope_with(cookie: str) -> dict:
    return {"type": "http", "headers": [(b"host", b"x"), (b"cookie", cookie.encode())]}


def body_text(html: str) -> str:
    """The visible text of a page's body (tags stripped)."""
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S).group(1)
    return re.sub(r"<[^>]+>", "", body).strip()


def test_create_makes_a_token_a_registry_entry_and_a_folder_with_the_demo_settings(dcfg):
    got = testers.create(dcfg, "Pilot 1")
    assert got["lid"] == "t-pilot-1" and len(got["token"]) == 32
    assert got["link"] == f"http://127.0.0.1:8765/t/{got['token']}"
    reg = json.loads((common.SHARED_DIR / "testers.json").read_text())
    assert reg["tokens"][got["token"]]["lid"] == "t-pilot-1"
    d = common.LISTENERS_DIR / "t-pilot-1"
    s = profiles.settings(dcfg, listener=d)
    assert s["chapter"]["minutes"] == 10 and s["overnight"]["enabled"] is False
    rec = json.loads((d / "tester.json").read_text())
    assert rec["label"] == "Pilot 1" and rec["token"] == got["token"] and rec["opened_at"] is None
    assert rec["debrief_card"] == "auto" and rec["consent_at"] is None
    assert testers.create(dcfg, "Pilot 1")["lid"] == "t-pilot-1-2"  # unique
    assert testers.create(dcfg, "Purnima!")["lid"] == "t-purnima"


def test_base_url_comes_from_the_environment_then_config(dcfg, monkeypatch):
    dcfg["demo"]["base_url"] = "https://listen.example.com/"
    assert testers.create(dcfg, "A")["link"].startswith("https://listen.example.com/t/")
    monkeypatch.setenv("WL_BASE_URL", "https://other.example.com")
    assert testers.create(dcfg, "B")["link"].startswith("https://other.example.com/t/")


def test_lookup_and_listener_for_request(dcfg):
    got = testers.create(dcfg, "Pilot 1")
    assert testers.lookup(got["token"])["lid"] == "t-pilot-1"
    assert testers.lookup("nope") is None and testers.lookup(None) is None
    assert testers.lookup("x" * 32) is None
    assert testers.listener_for_request(scope_with(f"other=1; wl_tester={got['token']}")) == "t-pilot-1"
    assert testers.listener_for_request(scope_with("wl_tester=" + "y" * 32)) is None
    assert testers.listener_for_request({"type": "http", "headers": []}) is None


def test_the_registry_cache_sees_a_change_made_by_another_process(dcfg):
    got = testers.create(dcfg, "Pilot 1")
    assert testers.lookup(got["token"])
    p = common.SHARED_DIR / "testers.json"
    reg = json.loads(p.read_text())
    reg["tokens"]["z" * 32] = {"lid": "t-pilot-1", "label": "Pilot 1", "created": "x"}
    reg["tokens"].pop(got["token"])
    p.write_text(json.dumps(reg))  # as `wl study` on the same machine would
    assert testers.lookup(got["token"]) is None and testers.lookup("z" * 32)["lid"] == "t-pilot-1"


def test_opening_a_link_sets_the_cookie_and_redirects_without_the_token(dcfg, tmp_path):
    got = testers.create(dcfg, "Pilot 1")
    c = client(dcfg, tmp_path)
    r = c.get(f"/t/{got['token']}")
    assert r.status_code == 302 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert f"wl_tester={got['token']}" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie and "Max-Age=31536000" in cookie
    assert testers.record("t-pilot-1")["opened_at"]
    r = c.get(f"/t/{got['token']}", headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["set-cookie"]


def test_secure_cookie_true_always_marks_it(dcfg, tmp_path):
    dcfg["demo"]["secure_cookie"] = True
    got = testers.create(dcfg, "Pilot 1")
    assert "Secure" in client(dcfg, tmp_path).get(f"/t/{got['token']}").headers["set-cookie"]


def test_first_open_recreates_a_missing_folder(dcfg, tmp_path):
    import shutil
    got = testers.create(dcfg, "Pilot 1")
    shutil.rmtree(common.LISTENERS_DIR / "t-pilot-1")
    assert client(dcfg, tmp_path).get(f"/t/{got['token']}").status_code == 302
    d = common.LISTENERS_DIR / "t-pilot-1"
    assert profiles.settings(dcfg, listener=d)["chapter"]["minutes"] == 10
    assert testers.record("t-pilot-1")["opened_at"]


def test_unknown_link_and_no_cookie_show_only_the_dot(dcfg, tmp_path):
    c = client(dcfg, tmp_path)
    for path in ("/t/" + "q" * 32, "/t/short", "/"):
        r = c.get(path)
        assert r.status_code == 200 and "set-cookie" not in r.headers
        assert body_text(r.text) == "" and "#F5F2ED" in r.text
        assert "<title></title>" in r.text and "<script" not in r.text


def test_gate_until_the_intro_is_done_then_the_product_page_in_tester_mode(dcfg, tmp_path, monkeypatch):
    got = testers.create(dcfg, "Pilot 1")
    c = client(dcfg, tmp_path)
    c.get(f"/t/{got['token']}")
    monkeypatch.setattr(routes, "GATE", tmp_path / "no-gate.html")
    assert body_text(c.get("/").text) == ""  # no gate.html yet (part C's): the blank dot
    gate = tmp_path / "gate.html"
    gate.write_text("<!doctype html><html><body><p>Tap to begin</p></body></html>")
    monkeypatch.setattr(routes, "GATE", gate)
    assert "Tap to begin" in c.get("/").text
    testers.update("t-pilot-1", intro_done=True)
    page = c.get("/").text
    assert re.search(r'<body class="[^"]*\btester\b[^"]*">', page) and 'id="orb"' in page


def test_with_demo_off_the_existing_page_answers_at_root(cfg, tmp_path):
    c = client(demo_cfg(cfg, enabled=False), tmp_path)
    page = c.get("/").text
    assert 'id="orb"' in page and "tester" not in re.search(r"<body[^>]*>", page).group(0)


def test_app_gives_the_product_pages_style_and_body_without_scripts(dcfg, tmp_path):
    got = testers.create(dcfg, "Pilot 1")
    c = client(dcfg, tmp_path)
    assert c.get("/app").status_code == 404  # not a tester
    c.get(f"/t/{got['token']}")
    parts = c.get("/app").json()
    assert ":root" in parts["style"] and "<style" not in parts["style"]
    assert 'id="orb"' in parts["body"] and "<script" not in parts["body"] and "<body" not in parts["body"]
    assert parts["body_class"].split() == ["idle", "tester"]


def test_tester_page_adds_the_class_however_body_is_written():
    assert routes.tester_page("<html><body>x</body></html>") == '<html><body class="tester">x</body></html>'
    assert routes.tester_page('<body class="idle" id="b">') == '<body class="idle tester" id="b">'


def test_consent_is_recorded_once_with_its_time(dcfg, tmp_path):
    got = testers.create(dcfg, "Pilot 1")
    c = client(dcfg, tmp_path)
    assert c.post("/api/demo/consent").status_code == 404
    c.get(f"/t/{got['token']}")
    first = c.post("/api/demo/consent").json()["consent_at"]
    assert first and testers.record("t-pilot-1")["consent_at"] == first
    testers.update("t-pilot-1", consent_at="2020-01-01T00:00:00")
    assert c.post("/api/demo/consent").json()["consent_at"] == "2020-01-01T00:00:00"  # the first is kept


def test_revoke_stops_the_link_and_the_cookie_and_keeps_the_folder(dcfg, tmp_path):
    got = testers.create(dcfg, "Pilot 1")
    c = client(dcfg, tmp_path)
    c.get(f"/t/{got['token']}")
    testers.update("t-pilot-1", intro_done=True)
    testers.revoke("t-pilot-1")
    assert body_text(c.get("/").text) == ""  # the cookie no longer opens anything
    assert c.get(f"/t/{got['token']}").status_code == 200  # the link: the blank dot
    assert testers.listener_for_request(scope_with(f"wl_tester={got['token']}")) is None
    rec = testers.record("t-pilot-1")
    assert rec["token"] is None and rec["revoked_at"] and (common.LISTENERS_DIR / "t-pilot-1").is_dir()


def test_reset_moves_the_folder_aside_and_starts_fresh_on_the_same_link(dcfg, tmp_path, monkeypatch):
    got = testers.create(dcfg, "Pilot 1")
    c = client(dcfg, tmp_path)
    c.get(f"/t/{got['token']}")
    testers.set_name("t-pilot-1", "Ada")
    testers.update("t-pilot-1", intro_done=True)
    (common.LISTENERS_DIR / "t-pilot-1" / "sessions" / "s1.jsonl").write_text('{"who": "listener"}\n')
    seen = []
    monkeypatch.setattr(testers, "ON_CHANGE", [lambda lid, action: seen.append((lid, action))])
    moved = testers.reset("t-pilot-1", dcfg)
    assert seen == [("t-pilot-1", "reset")]
    assert moved.parent.name.startswith("_cleared_") and moved.parent.parent == common.LISTENERS_DIR
    assert (moved / "sessions" / "s1.jsonl").exists() and (moved / "profile.md").exists()
    d = common.LISTENERS_DIR / "t-pilot-1"
    assert not (d / "profile.md").exists() and not list((d / "sessions").iterdir())
    rec = testers.record("t-pilot-1")
    assert rec["token"] == got["token"] and rec["name"] is None and not rec.get("intro_done")
    assert rec["opened_at"] is None and profiles.settings(dcfg, listener=d)["chapter"]["minutes"] == 10
    monkeypatch.setattr(routes, "GATE", tmp_path / "gate.html")
    (tmp_path / "gate.html").write_text("<html><body>Tap to begin</body></html>")
    assert "Tap to begin" in c.get("/").text  # the same cookie, a fresh start
    assert testers.record("t-pilot-1")["opened_at"]
    assert testers.reset("t-pilot-1", dcfg) != moved  # twice in a second: two places


def test_delete_removes_folder_and_token_and_only_a_testers(dcfg, tmp_path):
    got = testers.create(dcfg, "Pilot 1")
    testers.delete("t-pilot-1")
    assert not (common.LISTENERS_DIR / "t-pilot-1").exists() and testers.lookup(got["token"]) is None
    assert testers.all_testers() == []
    profiles.create("ian")
    with pytest.raises(StageError):
        testers.delete("ian")
    assert (common.LISTENERS_DIR / "ian").is_dir()


def test_admin_hooks_run_outside_the_registry_lock(dcfg, monkeypatch):
    """A hook can take seconds (stopping a build); every request's middleware takes _LOCK (review)."""
    import threading
    free = []

    def hook(lid, action):  # another thread (a request) can take the lock while the hook runs
        got = []

        def take():
            if testers._LOCK.acquire(timeout=1):
                testers._LOCK.release()
                got.append(True)
        th = threading.Thread(target=take)
        th.start()
        th.join(5)
        free.append((action, got == [True]))
    monkeypatch.setattr(testers, "ON_CHANGE", [hook])
    testers.create(dcfg, "Pilot 1")
    testers.revoke("t-pilot-1")
    testers.reset("t-pilot-1", dcfg)
    testers.delete("t-pilot-1")
    assert free == [("revoke", True), ("reset", True), ("delete", True)]
    with pytest.raises(StageError):
        testers.reset("t-nobody", dcfg)  # no tester: no hook either
    assert len(free) == 3


def test_set_name_writes_one_line_in_the_profile(dcfg):
    testers.create(dcfg, "Pilot 1")
    p = common.LISTENERS_DIR / "t-pilot-1" / "profile.md"
    p.write_text("# What the listener has said\n- Builds cabinets.\n")
    testers.set_name("t-pilot-1", "  Ada  ")
    testers.set_name("t-pilot-1", "Ada Lovelace")  # corrected: one line still
    text = p.read_text()
    assert text.count("Their name is") == 1
    assert "Their name is Ada Lovelace (they told the interviewer just now)." in text and "Builds cabinets" in text
    assert testers.record("t-pilot-1")["name"] == "Ada Lovelace"


def test_debrief_card_and_update(dcfg):
    testers.create(dcfg, "Pilot 1")
    assert testers.set_debrief_card("t-pilot-1", "off")["debrief_card"] == "off"
    with pytest.raises(StageError):
        testers.set_debrief_card("t-pilot-1", "maybe")
    assert testers.update("t-pilot-1", stage_hint="chapter")["stage_hint"] == "chapter"
    with pytest.raises(StageError):
        testers.update("t-nobody", x=1)


def test_a_name_given_with_the_link_is_saved_for_the_interviewer_and_the_guide(dcfg):
    """Ian's notes (2026-11-16): the interviewer is given the tester's name ("Hi, is this Rolando?"). It is saved
    at creation as set_name saves one, so the guide has it even if the intro never calls a tool."""
    got = testers.create(dcfg, "Pilot 1", name="  Rolando ")
    assert got["name"] == "Rolando"
    rec = testers.record("t-pilot-1")
    assert rec["name"] == "Rolando" and rec["given_name"] == "Rolando"
    assert (common.LISTENERS_DIR / "t-pilot-1" / "profile.md").read_text().strip() == testers.NAME_LINE.format(name="Rolando")
    reg = json.loads((common.SHARED_DIR / "testers.json").read_text())
    assert reg["tokens"][got["token"]]["given_name"] == "Rolando"
    assert testers.lookup(got["token"])["name"] == "Rolando"
    # a correction in the intro replaces the name; a reset starts again from the one given
    testers.set_name("t-pilot-1", "Roland")
    assert testers.record("t-pilot-1")["name"] == "Roland" and testers.record("t-pilot-1")["given_name"] == "Rolando"
    testers.reset("t-pilot-1", dcfg)
    assert testers.record("t-pilot-1")["name"] == "Rolando"
    assert "Rolando" in (common.LISTENERS_DIR / "t-pilot-1" / "profile.md").read_text()
    # a folder gone missing is remade with it on first open
    import shutil
    shutil.rmtree(common.LISTENERS_DIR / "t-pilot-1")
    assert testers.open_link(dcfg, got["token"])["name"] == "Rolando"


def test_a_label_that_reads_as_a_first_name_is_the_name_when_none_is_given(dcfg):
    for label, want in (("Rolando", "Rolando"), ("Mary Ann", "Mary Ann"), ("O'Neil", "O'Neil"), ("Pilot 1", None),
                        ("Ian test", None), ("rolando", None), ("Friend Of Ian", None), ("QA", None), ("A", None)):
        got = testers.create(dcfg, label)
        assert got["name"] == want and testers.record(got["lid"])["name"] == want, label
    assert testers.create(dcfg, "Pilot 2", name="Rolando")["name"] == "Rolando"  # a given name wins over the label
    assert not (common.LISTENERS_DIR / "t-pilot-1" / "profile.md").exists()  # no name: nothing for the guide yet
