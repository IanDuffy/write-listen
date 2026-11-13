"""Fast Start steps 0 and 1: the scout, the early scout, and adopting one (faststart/scout.py)."""

import json
import os

import pytest
from fakes import FakeAnthropic, Responder, all_text
from fast_outline_fakes import (MATCH_PHRASE, NOT_ON_PAGE, ON_PAGE, SCOUT_PHRASE, TOPIC, VITRUVIUS_URL,
                                WRITEUP_PHRASE, AreaA, dossiers, make_dossier, offline, seed_run)  # noqa: F401

from faststart import base, scout
from stages import common, llm, research
from stages.common import StageError, dossier_entries, entry_tags


@pytest.fixture
def fake(monkeypatch):
    a = AreaA()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(a))
    return a


@pytest.fixture(params=dossiers())
def kind(request):
    return request.param


# --- step 1 -------------------------------------------------------------------------------

def test_scout_passes_its_budget_to_the_web_tools(tmp_path, cfg, fake, kind):
    r = seed_run(tmp_path, cfg)
    scout.run(r, cfg, make_dossier(kind, r, cfg))
    call, = fake.calls
    tools = {t["name"]: t for t in call["tools"]}
    assert tools["web_search"]["max_uses"] == 6 and tools["web_fetch"]["max_uses"] == 3
    assert call["model"] == "claude-opus-5-5"
    prompt = all_text(call)
    assert "You have 6 searches and 3 page fetches" in prompt
    assert TOPIC in prompt and "I pour concrete for a living" in prompt
    assert "{{" not in prompt and "episode" not in prompt and "podcast" not in prompt
    # compact: the long map's sections are not asked for
    assert "Twelve to eighteen" in prompt and "Two or three" in prompt and "At most 150 words" in prompt
    assert "## Threads" not in prompt and "## Candidate sources" not in prompt
    # Regression (prompt review): the opening's facts are never re-sourced, so the scene it opens on is
    # fetched from a source the opening may state as fact, not from whichever page tells it best.
    assert "Fetch the page that tells the best one" not in prompt
    assert "only what a tier 1 to 3 source gives it" in prompt and "a tier 4 page is color only" in prompt
    assert "the primary text or an academic source before a serious magazine" in prompt
    rec, = r.read_log()
    assert rec["stage"] == "fast_scout" and rec["web_searches"] == 5 and rec["cost_usd"] > 0


def test_scout_files_its_entries_as_row_L00(tmp_path, cfg, fake, kind):
    r = seed_run(tmp_path, cfg)
    d = make_dossier(kind, r, cfg)
    out = scout.run(r, cfg, d)
    assert out == r.root / "01_scout.md" and scout.text(r) == out.read_text()

    md, src = research.row_paths(r, "L00")
    block = md.read_text()
    assert block.startswith("## Territory") and "<scout>" not in block
    entries = research.parse_entries(block)
    assert [e["n"] for e in entries] == [1, 2, 3, 4, 5, 6]
    assert [e["tags"]["kind"] for e in entries] == ["claim", "material", "quote", "quote", "claim", "tension"]
    assert entries[2]["tags"] == {"rows": [], "kind": "quote", "tier": 1, "verbatim": "yes", "verdict": None,
                                  "links": []}
    assert ON_PAGE in entries[2]["body"] and VITRUVIUS_URL in entries[2]["body"]

    row = json.loads(src.read_text())  # the same shape as a research row's sources file
    assert row["row"] == "L00" and row["followup"] is False and row["written_up"] is False
    assert row["budget"] == {"searches": 6, "fetches": 3} and row["searches"] == 5 and row["fetches"] == 1
    assert row["fetched"][0]["url"] == VITRUVIUS_URL and "astonishing results" in row["fetched"][0]["text"]
    assert VITRUVIUS_URL in row["urls"] and row["failed"] == []
    whole = json.loads(r.path("scout_sources").read_text())
    assert whole["fetched"] == row["fetched"] and whole["budget"] == row["budget"] and whole["cost_usd"] > 0
    assert not (r.root / "01_scout.pending.json").exists()

    assert d.has("L00") and d.numbers_of("L00") == [1, 2, 3, 4, 5, 6]


def test_scout_md_cites_d_numbers_only_and_shows_the_checked_entries(tmp_path, cfg, fake, kind):
    r = seed_run(tmp_path, cfg)
    d = make_dossier(kind, r, cfg)
    text = scout.run(r, cfg, d).read_text()
    assert "L00." not in text
    assert [ln for ln in text.splitlines() if ln.startswith("#")] == [
        "# Scout", "## Territory", "## Entries", "## Scenes", "### Forms sunk in the open sea",
        "### The lumps that heal"]
    assert "Entries: D01, D02, D03" in text and "Entries: D05, D06" in text  # the scenes' references
    found = dossier_entries(text)  # the entries parse like a dossier's
    assert sorted(found) == [1, 2, 3, 4, 5, 6] and found == {k: v for k, v in d.entries().items() if k <= 6}
    assert entry_tags(found[3])["verbatim"] == "yes" and ON_PAGE in found[3]
    # the quote that is on no fetched page was downgraded by the check, and the scout file says so too
    assert NOT_ON_PAGE in found[4] and entry_tags(found[4])["verbatim"] == "no"
    assert "conflicts with D05" in found[6]
    assert entry_tags(found[1])["rows"] == ["L00"] and entry_tags(found[1])["tier"] == 2


def test_scout_resumes_without_a_paid_call(tmp_path, cfg, fake, kind):
    r = seed_run(tmp_path, cfg)
    d = make_dossier(kind, r, cfg)
    out = scout.run(r, cfg, d)
    first = out.read_text()
    assert scout.run(r, cfg, d) == out and len(fake.calls) == 1  # everything is there: nothing happens

    # a new process: the dossier is made again, 01_scout.md is there
    assert scout.run(r, cfg, make_dossier(kind, r, cfg)) == out
    assert len(fake.calls) == 1 and out.read_text() == first

    # stopped after the search was paid for but before 01_scout.md: only the filing is left
    out.unlink()
    d2 = make_dossier(kind, r, cfg)
    assert scout.run(r, cfg, d2).read_text() == first
    assert len(fake.calls) == 1 and d2.numbers_of("L00") == [1, 2, 3, 4, 5, 6]


def test_scout_md_without_its_findings_is_an_error_not_a_second_search(tmp_path, cfg, fake):
    r = seed_run(tmp_path, cfg)
    scout.run(r, cfg, make_dossier("stub", r, cfg))
    research.row_paths(r, "L00")[0].unlink()
    with pytest.raises(StageError, match="L00.md is gone"):
        scout.run(r, cfg, make_dossier("stub", r, cfg))
    assert len(fake.calls) == 1


def test_a_scout_paused_at_its_budget_is_written_up_not_resumed(tmp_path, cfg, fake, kind):
    fake.script.scout_pause = True
    r = seed_run(tmp_path, cfg)
    d = make_dossier(kind, r, cfg)
    text = scout.run(r, cfg, d).read_text()
    loop, writeup = fake.calls
    assert "tools" in loop and "tools" not in writeup  # the write-up has no tools: the budget holds
    sent = all_text(writeup)
    assert WRITEUP_PHRASE in sent and SCOUT_PHRASE in sent  # its original instructions
    assert "Vitruvius has the powder." in sent  # its notes
    assert "astonishing results" in sent and VITRUVIUS_URL in sent  # the page it fetched, in full
    assert "Write the <scout> block now" in sent and "<map>" not in sent
    assert [rec["stage"] for rec in r.read_log()] == ["fast_scout", "fast_scout_writeup"]
    src = json.loads(research.row_paths(r, "L00")[1].read_text())
    assert src["written_up"] is True and src["searches"] == 6 and src["fetches"] == 3
    assert src["cost_usd"] == pytest.approx(sum(rec["cost_usd"] for rec in r.read_log()), abs=1e-4)
    assert not (r.root / "01_scout.pending.json").exists()
    assert "Entries: D01, D02, D03" in text and d.numbers_of("L00") == [1, 2, 3, 4, 5, 6]


def test_a_failed_write_up_is_retried_alone(tmp_path, cfg, fake):
    fake.script.scout_pause, fake.script.writeup_text = True, "I have nothing to add."
    r = seed_run(tmp_path, cfg)
    with pytest.raises(StageError, match="no <scout> block.*writes it up again"):
        scout.run(r, cfg, make_dossier("stub", r, cfg))
    pending = json.loads((r.root / "01_scout.pending.json").read_text())
    assert pending["searches"] == 6 and pending["fetched"][0]["url"] == VITRUVIUS_URL
    assert (r.root / "_partial_fast_scout.md").read_text() == "I have nothing to add."
    assert not research.row_paths(r, "L00")[0].exists() and not (r.root / "01_scout.md").exists()

    fake.script.writeup_text = None
    scout.run(r, cfg, make_dossier("stub", r, cfg))
    assert len(fake.with_phrase(WRITEUP_PHRASE)) == 2
    assert len([c for c in fake.calls if "tools" in c]) == 1  # the search was not paid for twice
    assert json.loads(research.row_paths(r, "L00")[1].read_text())["written_up"] is True


def test_a_scout_with_no_entries_stops(tmp_path, cfg, fake):
    fake.script.scout_text = "<scout>\n## Territory\nLittle is known.\n\n## Entries\n\n## Scenes\n</scout>"
    r = seed_run(tmp_path, cfg)
    with pytest.raises(StageError, match="no numbered entries"):
        scout.run(r, cfg, make_dossier("stub", r, cfg))
    assert not research.row_paths(r, "L00")[0].exists()


    r2 = seed_run(tmp_path, cfg, name="second")
    fake.script.scout_text = "No block at all."
    with pytest.raises(StageError, match="no <scout> block"):
        scout.run(r2, cfg, make_dossier("stub", r2, cfg))


@pytest.mark.parametrize("reply", ["My notes, with no tags round them.",
                                   "<scout>\n## Territory\nA sketch.\n\n## Entries\n[1] A fact.\n\n## Scenes\n</scout>"])
def test_a_scout_reply_that_cannot_be_filed_keeps_the_search_it_paid_for(tmp_path, cfg, fake, reply):
    """Regression: the loop ended normally but its text had no usable <scout> block, and the pages it
    had searched and fetched were dropped, so the retry paid for the whole tool loop again."""
    fake.script.scout_text = reply
    r = seed_run(tmp_path, cfg)
    with pytest.raises(StageError, match="the next try writes it up again from the saved search"):
        scout.run(r, cfg, make_dossier("stub", r, cfg))
    pending = json.loads((r.root / "01_scout.pending.json").read_text())
    assert pending["searches"] == 5 and pending["fetched"][0]["url"] == VITRUVIUS_URL and pending["notes"] == reply
    assert (r.root / "_partial_fast_scout.md").read_text() == reply
    assert not research.row_paths(r, "L00")[0].exists()

    d = make_dossier("stub", r, cfg)
    scout.run(r, cfg, d)  # the retry: one write-up without tools, from the saved pages
    assert len([c for c in fake.calls if "tools" in c]) == 1 and sum(rec["web_searches"] for rec in r.read_log()) == 5
    writeup, = fake.with_phrase(WRITEUP_PHRASE)
    assert "tools" not in writeup and "astonishing results" in all_text(writeup) and reply in all_text(writeup)
    assert d.numbers_of("L00") == [1, 2, 3, 4, 5, 6] and not (r.root / "01_scout.pending.json").exists()
    src = json.loads(research.row_paths(r, "L00")[1].read_text())
    assert src["written_up"] is True and src["searches"] == 5
    assert src["cost_usd"] == pytest.approx(sum(rec["cost_usd"] for rec in r.read_log()), abs=1e-4)


def test_a_number_the_scout_used_twice_keeps_its_first_entry(tmp_path, cfg, fake, kind):
    fake.script.scout_text = ("<scout>\n## Territory\nA sketch.\n\n## Entries\n"
                              "[L00.1] (claim, tier 2) The first fact. Source: https://a.example.org/1\n"
                              "[L00.1] (claim, tier 2) A second fact under the same number. Source: https://a.example.org/2\n"
                              "[L00.3] (material, tier 3) A scene. Source: https://a.example.org/3\n\n"
                              "## Scenes\n### One\nIt happened.\nEntries: L00.1, L00.3, L00.9\n</scout>")
    r = seed_run(tmp_path, cfg)
    d = make_dossier(kind, r, cfg)
    text = scout.run(r, cfg, d).read_text()
    assert d.numbers_of("L00") == [1, 2]
    assert "Entries: D01, D02, L00.9" in text  # a reference to an entry that does not exist is left to be seen
    assert "The first fact." in text and "A second fact" not in text


def test_a_scout_that_numbers_its_entries_under_another_row_still_cites_d_numbers(tmp_path, cfg, fake, kind):
    # Live, 2026-11-13: the scout wrote [L01.n] for its own entries; the scenes of 01_scout.md then
    # pointed at no entry ("Entries: L01.11, L01.9"), so nobody could trace a scene's claim to its source.
    fake.script.scout_text = ("<scout>\n## Territory\nA sketch, as L01.1 has it.\n\n## Entries\n"
                              "[L01.1] (claim, tier 2) The first fact. Source: https://a.example.org/1\n"
                              "[L01.2] (material, tier 3) A scene. Source: https://a.example.org/2\n\n"
                              "## Scenes\n### One\nIt happened.\nEntries: L01.1, L01.2\n</scout>")
    r = seed_run(tmp_path, cfg)
    d = make_dossier(kind, r, cfg)
    text = scout.run(r, cfg, d).read_text()
    assert d.numbers_of("L00") == [1, 2]
    assert "Entries: D01, D02" in text and "as D01 has it" in text and "L01." not in text


# --- step 0: the early scout, and adopting it -------------------------------------------------

def early_dir(tmp_path):
    return tmp_path / "runs"


def test_early_scout_runs_the_search_in_a_folder_of_its_own(tmp_path, cfg, fake):
    cfg["listener_dir"] = str(tmp_path / "listener")
    root = scout.early(cfg, early_dir(tmp_path), "  Roman   concrete ")
    assert root.parent == early_dir(tmp_path) / "_scouts" and root.name.startswith("roman-concrete-")
    assert (root / "04_research" / "L00.md").exists() and (root / "04_research" / "L00.sources.json").exists()
    assert (root / "01_scout.sources.json").exists()
    assert not (root / "01_scout.md").exists() and not (root / "04_dossier.md").exists()  # D numbers belong to a run
    info = json.loads((root / "early.json").read_text())
    assert info["topic"] == "Roman concrete" and info["state"] == "done" and info["pid"] == os.getpid()
    call, = fake.calls
    assert call["tools"][0]["max_uses"] == 6 and "Roman concrete" in all_text(call)
    assert common.Run(name=root.name, root=root).spent() > 0

    # the same topic again, however it is typed: nothing more is paid
    assert scout.early(cfg, early_dir(tmp_path), "roman concrete!") == root and len(fake.calls) == 1
    # the server's run list does not see it
    assert not list(early_dir(tmp_path).glob("*/status.json")) and not list(early_dir(tmp_path).glob("*/00_source/seed.md"))
    with pytest.raises(StageError, match="needs a topic"):
        scout.early(cfg, early_dir(tmp_path), " ?! ")


def test_early_scout_is_capped_and_a_failure_is_recorded(tmp_path, cfg, monkeypatch):
    cfg["listener_dir"] = str(tmp_path / "listener")
    seen = {}

    def responder(kwargs):
        raise AssertionError("no request should be sent past the cap")

    monkeypatch.setattr(llm, "_client", FakeAnthropic(responder))
    real = llm.call

    def spy(run, *a, **kw):
        seen["cap"], seen["unattended"] = run.max_usd, run.unattended
        run.log({"stage": "earlier", "cost_usd": 2.0})  # as if the cap were already spent
        return real(run, *a, **kw)

    monkeypatch.setattr(llm, "call", spy)
    with pytest.raises(StageError, match="cap"):
        scout.early(cfg, early_dir(tmp_path), "Roman concrete")
    assert seen == {"cap": 1.50, "unattended": True}
    root, = (early_dir(tmp_path) / "_scouts").iterdir()
    info = json.loads((root / "early.json").read_text())
    assert info["state"] == "failed" and "StageError" in info["error"]
    assert scout._early_scouts(early_dir(tmp_path)) == []  # a failed scout is nothing to adopt


def test_adopt_matches_by_normalised_text_without_a_model_call(tmp_path, cfg, fake, kind):
    cfg["listener_dir"] = str(tmp_path / "listener")
    root = scout.early(cfg, early_dir(tmp_path), "why roman concrete has lasted 2000 years?")
    other = scout.early(cfg, early_dir(tmp_path), "The Antikythera mechanism")
    assert len(fake.calls) == 2
    r = seed_run(tmp_path, cfg, topic="Why Roman concrete has lasted 2000 years")
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is True
    assert len(fake.calls) == 2 and not fake.with_phrase(MATCH_PHRASE)  # equal after normalising: no model call

    for name in ("04_research/L00.md", "04_research/L00.sources.json", "01_scout.sources.json"):
        assert (r.root / name).read_text() == (root / name).read_text()
    log = r.read_log()  # its cost is counted in this run
    assert [rec["stage"] for rec in log] == ["fast_scout"] and log[0]["early_scout"] == root.name
    assert r.spent() == pytest.approx(common.Run(name=root.name, root=root).spent()) and r.spent() > 0
    assert json.loads((r.root / "fast" / "early_scout.json").read_text())["from"] == root.name
    assert "Reused the scout made during the conversation" in r.path("decisions").read_text()
    assert (other / "04_research" / "L00.md").exists()  # the unmatched one is left alone

    # the run then files it without a paid call
    d = make_dossier(kind, r, cfg)
    text = scout.run(r, cfg, d).read_text()
    assert len(fake.calls) == 2 and "Entries: D01, D02, D03" in text and d.numbers_of("L00") == [1, 2, 3, 4, 5, 6]
    # asked again (a resumed run): still True, and the cost is not counted twice
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is True and len(r.read_log()) == 1


def test_adopt_matches_by_the_cheap_call(tmp_path, cfg, fake):
    cfg["listener_dir"] = str(tmp_path / "listener")
    scout.early(cfg, early_dir(tmp_path), "The Antikythera mechanism")
    root = scout.early(cfg, early_dir(tmp_path), "Roman sea concrete and why it lasts")
    fake.script.match = " 2.\n"  # the number only, however it is set down
    r = seed_run(tmp_path, cfg)
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is True
    call, = fake.with_phrase(MATCH_PHRASE)
    assert call["model"] == "claude-haiku-5-5" and "tools" not in call
    sent = all_text(call)
    assert "1. The Antikythera mechanism\n2. Roman sea concrete and why it lasts" in sent
    assert TOPIC in sent and "I pour concrete for a living" in sent and "{{" not in sent
    assert (r.root / "04_research" / "L00.md").read_text() == (root / "04_research" / "L00.md").read_text()
    assert [rec["stage"] for rec in r.read_log()] == ["fast_scout_match", "fast_scout"]
    assert r.read_log()[0]["cost_usd"] < 0.01  # the cheap model


# Regression: the first digits anywhere in the reply were taken as the answer, so prose that mentioned a
# number adopted a scout of another subject. Only a reply that is a number and nothing else counts.
@pytest.mark.parametrize("answer", ["0", "None of them.", "7", "", "None of the 1 topics is the same subject.",
                                    "Topic 1? No: 0.", "Answer: 1", "1 or 0", "0, though topic 1 is close", "1 1"])
def test_adopt_finds_no_match(tmp_path, cfg, fake, answer):
    cfg["listener_dir"] = str(tmp_path / "listener")
    root = scout.early(cfg, early_dir(tmp_path), "The Antikythera mechanism")
    fake.script.match = answer or " "
    r = seed_run(tmp_path, cfg)
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is False
    assert len(fake.with_phrase(MATCH_PHRASE)) == 1
    assert not (r.root / "04_research").exists() and not (r.root / "fast" / "early_scout.json").exists()
    assert (root / "04_research" / "L00.md").exists()  # left for the caller to discard
    # the run then scouts for itself, and says it was not adopted
    scout.run(r, cfg, make_dossier("stub", r, cfg))
    assert len(fake.with_phrase(SCOUT_PHRASE)) == 2 and scout.adopt(r, cfg, early_dir(tmp_path)) is False


def test_adopt_considers_only_early_scouts_from_this_conversation(tmp_path, cfg, fake):
    """Regression: every early scout ever made was a candidate, so each later chapter paid the match
    call against a growing list and risked opening on a subject from some earlier conversation."""
    import time

    cfg["listener_dir"] = str(tmp_path / "listener")
    old = scout.early(cfg, early_dir(tmp_path), "Octopus intelligence")
    same = scout.early(cfg, early_dir(tmp_path), TOPIC)
    for root in (old, same):  # both made three hours ago, in some earlier conversation
        info = json.loads((root / "early.json").read_text())
        base.write_json(root / "early.json", {**info, "started": time.time() - 3 * 3600})
    assert scout._early_scouts(early_dir(tmp_path)) == []
    fake.script.match = "1"
    r = seed_run(tmp_path, cfg)
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is False
    assert not fake.with_phrase(MATCH_PHRASE) and not (r.root / "04_research").exists()  # nothing asked, nothing copied

    # one made within the window is a candidate, the stale one beside it still is not
    info = json.loads((same / "early.json").read_text())
    base.write_json(same / "early.json", {**info, "started": time.time() - 3600})
    assert [root for root, _ in scout._early_scouts(early_dir(tmp_path))] == [same]

    # the guide scouting an old topic again in a new conversation pays nothing and makes it current
    calls = len(fake.calls)
    assert scout.early(cfg, early_dir(tmp_path), "octopus intelligence") == old and len(fake.calls) == calls
    assert [root for root, _ in scout._early_scouts(early_dir(tmp_path))] == [old, same]  # oldest first, as before
    r2 = seed_run(tmp_path, cfg, name="second", topic="Octopus intelligence")
    assert scout.adopt(r2, cfg, early_dir(tmp_path)) is True and not fake.with_phrase(MATCH_PHRASE)


def test_adopt_with_no_early_scouts_asks_nothing(tmp_path, cfg, fake):
    r = seed_run(tmp_path, cfg)
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is False and fake.calls == []


def test_adopt_survives_a_failed_match_call(tmp_path, cfg, fake, monkeypatch):
    cfg["listener_dir"] = str(tmp_path / "listener")
    scout.early(cfg, early_dir(tmp_path), "The Antikythera mechanism")
    r = seed_run(tmp_path, cfg)
    monkeypatch.setattr(llm, "_client", FakeAnthropic(lambda kw: Responder.msg(kw, "", stop="refusal")))
    assert scout.adopt(r, cfg, early_dir(tmp_path)) is False


def test_adopt_waits_for_an_early_scout_that_is_still_searching(tmp_path, cfg, fake):
    cfg["listener_dir"] = str(tmp_path / "listener")
    root = scout.early(cfg, early_dir(tmp_path), TOPIC)
    md = root / "04_research" / "L00.md"
    block = md.read_text()
    md.unlink()  # as it stands while the search is still going, in this process
    info = json.loads((root / "early.json").read_text())
    base.write_json(root / "early.json", {**info, "state": "running", "started": __import__("time").time()})
    r = seed_run(tmp_path, cfg)
    naps = []

    def sleep(seconds):
        naps.append(seconds)
        if len(naps) == 3:  # the search finishes
            base.write_atomic(md, block)

    assert scout.adopt(r, cfg, early_dir(tmp_path), wait=30, sleep=sleep) is True
    assert naps == [1, 1, 1] and (r.root / "04_research" / "L00.md").read_text() == block

    # one that never finishes is given up on, and the run scouts for itself
    md.unlink()
    r2 = seed_run(tmp_path, cfg, name="second")
    assert scout.adopt(r2, cfg, early_dir(tmp_path), wait=0, sleep=sleep) is False
    assert not (r2.root / "04_research").exists()

    # one whose process is gone is not waited for at all
    base.write_json(root / "early.json", {**info, "state": "running", "pid": 2 ** 22 + 12345})
    naps.clear()
    assert scout.adopt(r2, cfg, early_dir(tmp_path), wait=30, sleep=sleep) is False and naps == []
