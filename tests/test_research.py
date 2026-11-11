"""Stage 04 research per ledger row, the merge into the dossier, and stage 04b."""

import builtins
import json

import pytest
from fakes import PAGE_TEXT, FakeAnthropic, Responder, all_text

from stages import ledger, ledger_update, llm, research
from stages.common import StageError, dossier_entries, entry_tags
from stages.research import _norm_text, build_dossier, parse_entries, quote_found, row_paths


@pytest.fixture
def ledgered(run, cfg, monkeypatch):
    """A run with its transcript, map, angle and ledger, and a fake API."""
    run.yes = True
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("Scarry: I think beauty makes us want to copy it, always and everywhere.\n")
    run.path("map").write_text("m")
    run.path("angle").write_text("a")
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    ledger.run(run, cfg)
    return run, resp


def write_findings(r, rid, entries, followup=False, fetched=None):
    md, src = row_paths(r, rid, followup)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(f"# {rid}: x\n\n## Entries\n" + "\n".join(entries) + "\n\n## Not found\nNothing.\n")
    src.write_text(json.dumps({"urls": {}, "fetched": fetched or [], "searches": 1, "budget": {"searches": 8}}))


def test_quote_found_exact_ellipsis_and_curly_quotes():
    page = [_norm_text(PAGE_TEXT)]
    assert quote_found("“He who has been instructed thus far in the things of love” Source: x", page)
    assert quote_found('"He who has been instructed ... will suddenly perceive a nature of wondrous beauty."', page)
    assert quote_found('"He who has been taught about love will see beauty suddenly appear"', page) is False
    assert quote_found("A claim with no quote. Source: https://x.org", page) is None


def test_parse_entries_tags_bullets_and_text_in_parentheses():
    e = parse_entries("## Summary\n[L01.9] in the summary is not an entry\n## Entries\n"
                      "- **[L1.1]** (quote, tier 1, verbatim: yes) \"Words here now.\" Source: https://a.org\n"
                      "  continued on a second line.\n"
                      "[L01.2] (In 1933) Weil went to work in a factory.\n"
                      "[L01.3] (connection, L4) A link.\n## Not found\nNothing.")
    assert [x["n"] for x in e] == [1, 2, 3]
    assert e[0]["tags"]["kind"] == "quote" and e[0]["tags"]["verbatim"] == "yes" and "second line" in e[0]["body"]
    assert not e[1]["tagged"] and e[1]["body"].startswith("(In 1933) Weil")
    assert e[2]["tags"]["links"] == ["L04"]


def test_merge_checks_quotes_tiers_and_episode(ledgered, cfg):
    r, _ = ledgered
    led = ledger.load(r, cfg)
    page = [{"url": "https://www.gutenberg.org/ebooks/1600", "title": "Symposium", "text": PAGE_TEXT}]
    write_findings(r, "L01", [
        '[L01.1] (quote, tier 1, verbatim: yes) "He who has been instructed thus far in the things of love" '
        "Source: https://gutenberg.org/ebooks/1600/",
        '[L01.2] (quote, tier episode, verbatim: yes) "beauty makes us want to copy it, always and everywhere" '
        "Scarry. Source: episode",
        '[L01.3] (quote, tier episode, verbatim: yes) "beauty makes us want to sing it from the rooftops" '
        "Scarry. Source: episode",
        "[L01.4] (claim, tier 1) Diotima taught Socrates. Source: https://en.wikipedia.org/wiki/Diotima",
        "[L01.5] (material, tier 3) A scene, see L01.4. Source: https://www.gutenberg.org/ebooks/1600",
    ], fetched=page)
    write_findings(r, "L02", ['[L02.1] (quote, tier 1, verbatim: yes) "words copied from a page nobody fetched" '
                              "Source: https://b.org"])
    write_findings(r, "L03", ["[L03.1] (claim, tier 2, confirmed) Fine. Source: https://plato.stanford.edu/x"])
    d = build_dossier(r, cfg, led, r.path("transcript").read_text())
    entries = dossier_entries(d)
    tags = {n: entry_tags(t) for n, t in entries.items()}
    assert tags[1]["verbatim"] == "yes"  # on the fetched page (URL matched despite www and the slash)
    assert tags[2]["verbatim"] == "yes"  # in the transcript
    assert tags[3]["verbatim"] == "no" and tags[6]["verbatim"] == "no"
    assert "see D04" in entries[5]
    check = d.split("## Source check")[1]
    assert "D03 (not in the transcript)" in check and "D06 (not found on the fetched page)" in check
    assert "D04 (tier 1 recorded; wikipedia.org is tier 4)" in check
    assert "D01 on the fetched page; D02 in the transcript" in check
    assert build_dossier(r, cfg, led, r.path("transcript").read_text()) == d  # code only: same in, same out


def test_rows_flag_and_failed_agent_retried_alone(ledgered, cfg, monkeypatch):
    r, resp = ledgered
    with pytest.raises(StageError, match="L09"):
        research.run(r, cfg, rows="L09")
    real = resp.__call__

    def flaky(kwargs):
        msg = real(kwargs)
        if "This idea is row L03" in all_text(kwargs) and resp.row_calls.get("L03") == 1:
            msg.content = [type(msg.content[-1])(type="text", text="I ran out of budget.", citations=None)]
        return msg

    monkeypatch.setattr(llm, "_client", FakeAnthropic(flaky))
    with pytest.raises(StageError, match="L03"):
        research.run(r, cfg)
    assert row_paths(r, "L01")[0].exists() and not row_paths(r, "L03")[0].exists()
    assert (r.path("research_dir") / "_partial_L03.md").exists()
    assert not r.path("dossier").exists()
    calls = dict(resp.row_calls)
    research.run(r, cfg)
    assert resp.row_calls["L01"] == calls["L01"] and resp.row_calls["L03"] == calls["L03"] + 1
    assert r.path("dossier").exists()


def test_search_cap_asks_before_going_past(ledgered, cfg, monkeypatch):
    r, resp = ledgered
    r.yes = False
    cfg["cost_guard_usd"] = 100
    cfg["research"]["max_searches"] = 10  # the ledger's budgets add up to 18
    monkeypatch.setattr(builtins, "input", lambda prompt: "n")
    with pytest.raises(StageError, match="search cap"):
        research.run(r, cfg)
    assert not resp.row_calls  # nothing ran
    monkeypatch.setattr(builtins, "input", lambda prompt: "y")
    research.run(r, cfg, rows="L03")  # 2 searches: under the cap, no question
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail("should not ask"))
    research.run(r, cfg, plan_only=True)


def test_changed_row_is_flagged_stale(ledgered, cfg, capsys):
    r, _ = ledgered
    research.run(r, cfg, rows="L01")
    led = json.loads(r.path("ledger").read_text())
    led["rows"][0]["research_question"] = "A different question."
    led["rows"][0]["rank"], led["rows"][1]["rank"] = 2, 1  # a re-rank alone is not stale
    r.path("ledger").write_text(json.dumps(led))
    research.run(r, cfg, plan_only=True)
    assert "L01 changed in ledger.json after being researched" in capsys.readouterr().out


def test_log_records_the_row(ledgered, cfg):
    r, _ = ledgered
    research.run(r, cfg, rows="L01")
    recs = [x for x in r.read_log() if x["stage"].startswith("research")]
    assert recs and all(x["row"] == "L01" for x in recs)
    assert {x["stage"] for x in recs} == {"research_row", "research_writeup"}


def _researched(r, cfg):
    research.run(r, cfg)
    return r


def test_filled_needs_material_and_tier(ledgered, cfg):
    r, resp = ledgered
    _researched(r, cfg)
    resp.judge_rounds = [{"L01": "filled", "L02": "filled", "L03": "filled"}]
    ledger_update.run(r, cfg)
    rows = ledger.by_id(ledger.load(r, cfg))
    # L02 has no material entry in round 1; the code check makes it partial and it gets a follow-up.
    assert resp.row_calls.get("L02 follow-up") == 1
    rd1 = json.loads((r.path("research_dir") / "ledger_update_round1.json").read_text())
    assert {x["id"]: x["status"] for x in rd1["rows"]}["L02"] == "filled"  # the judge's word, before the check
    assert rows["L02"]["status"] == "filled"  # the follow-up brought material
    assert rows["L01"]["best_tier"] == 1


def test_backstop_keeps_partial_and_flags_it(ledgered, cfg):
    r, resp = ledgered
    _researched(r, cfg)
    resp.judge_rounds = [{"L01": "filled", "L02": "open", "L03": "filled"},
                         {"L01": "filled", "L02": "open", "L03": "filled"}]
    ledger_update.run(r, cfg)
    led = ledger.load(r, cfg)
    assert ledger.by_id(led)["L02"]["status"] == "open" and ledger.by_id(led)["L02"]["followup"] is True
    md = r.path("ledger_md").read_text()
    assert "## Still open (for Ian)" in md and "L02 (open, after the follow-up round)" in md
    # --force judges again, but never repeats a follow-up.
    r.force = True
    ledger_update.run(r, cfg)
    assert resp.row_calls["L02 follow-up"] == 1


def test_hand_edited_dossier_blocks_the_follow_up_round(ledgered, cfg):
    r, resp = ledgered
    _researched(r, cfg)
    r.path("dossier").write_text(r.path("dossier").read_text() + "\nIan's note.\n")
    with pytest.raises(StageError, match="edited by"):
        ledger_update.run(r, cfg)
    assert "L02 follow-up" not in resp.row_calls
    assert "Ian's note." in r.path("dossier").read_text()


def test_followup_numbering_continues(ledgered, cfg):
    r, resp = ledgered
    _researched(r, cfg)
    ledger_update.run(r, cfg)
    first = parse_entries(row_paths(r, "L02")[0].read_text())
    more = parse_entries(row_paths(r, "L02", followup=True)[0].read_text())
    assert more[0]["n"] == max(e["n"] for e in first) + 1
    assert "Weil at Solesmes" in r.path("dossier").read_text()
