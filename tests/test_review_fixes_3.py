"""Regression tests for defects found in the review of the ledger add-on."""

import json

import pytest
from fakes import PAGE_TEXT, FakeAnthropic, Responder, all_text

from stages import brief, coverage, ledger, ledger_update, llm, research
from stages.common import StageError, dossier_entries, entry_tags, parse_tags, spoken_text
from stages.report import claim_sentences
from stages.research import _norm_text, build_dossier, parse_rows, quote_found, row_paths


@pytest.fixture
def ledgered(run, cfg, monkeypatch):
    run.yes = True
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("Scarry: beauty makes us want to copy it.\n")
    run.path("map").write_text("m")
    run.path("angle").write_text("a")
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    ledger.run(run, cfg)
    return run, resp


def test_partly_spent_pause_is_written_up_not_resumed(ledgered, cfg):
    """A resumed request would get the whole max_uses budget again."""
    r, resp = ledgered
    resp.pause_at = {"L03": (1, 0)}  # a covered row (budget 2 searches) pauses after one
    research.run(r, cfg, rows="L03")
    calls = [c for c in resp.calls if "This idea is row L03" in all_text(c)]
    assert len(calls) == 2 and "tools" in calls[0] and "tools" not in calls[1]  # agent, then write-up
    src = json.loads(row_paths(r, "L03")[1].read_text())
    assert src["searches"] == 1 and src["written_up"] is True and "pending_writeup" not in src


def test_failed_write_up_is_retried_without_buying_the_tool_loop_again(ledgered, cfg, monkeypatch):
    r, resp = ledgered
    real = resp.__call__

    def broken_writeup(kwargs):
        if "your search and fetch budget is now spent" in all_text(kwargs):
            from types import SimpleNamespace as NS
            return NS(content=[NS(type="text", text="no block", citations=None)], stop_reason="end_turn",
                      usage=__import__("fakes").usage(), model=kwargs["model"], stop_details=None)
        return real(kwargs)

    monkeypatch.setattr(llm, "_client", FakeAnthropic(broken_writeup))
    with pytest.raises(StageError, match="no <research> block"):
        research.run(r, cfg, rows="L01")  # L01 pauses with its budget spent
    assert json.loads(row_paths(r, "L01")[1].read_text())["pending_writeup"] is True
    monkeypatch.setattr(llm, "_client", FakeAnthropic(real))
    research.run(r, cfg, rows="L01")
    assert resp.row_calls["L01"] == 1  # the tool loop ran once; only the write-up was repeated
    assert row_paths(r, "L01")[0].exists()


def _write(r, rid, entries, followup=False, fetched=None, fingerprint=None):
    md, src = row_paths(r, rid, followup)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(f"# {rid}: x\n\n## Entries\n" + "\n".join(entries) + "\n\n## Not found\nNothing.\n")
    src.write_text(json.dumps({"urls": {}, "fetched": fetched or [], "fingerprint": fingerprint}))


def test_followup_reusing_numbers_is_kept_and_its_references_resolve(ledgered, cfg):
    r, _ = ledgered
    led = ledger.load(r, cfg)
    _write(r, "L01", ["[L01.1] (claim, tier 1) First. Source: https://a.org",
                      "[L01.2] (material, tier 3) Second, see L01.1. Source: https://a.org"])
    md, _ = row_paths(r, "L01")
    md.write_text(md.read_text().replace("## Not found\nNothing.", "## Not found\nMore on L01.2."))
    _write(r, "L01", ["[L01.1] (material, tier 2) Weil at Solesmes. Source: https://b.org",
                      "[L01.2] (claim, tier 2) Follow-up fact, after L01.1. Source: https://b.org"], followup=True)
    for rid in ("L02", "L03"):
        _write(r, rid, [f"[{rid}.1] (claim, tier 2) X {rid}. Source: https://c.org"])
    d = build_dossier(r, cfg, led)
    entries = dossier_entries(d)
    assert "Weil at Solesmes" in d and "Follow-up fact, after D03." in entries[4]
    assert "Second, see D01." in entries[2]
    assert "More on D02." in d  # Not found references renumbered too
    assert "reused a first-round number" in d


def test_redo_sets_aside_the_old_followup(ledgered, cfg):
    r, resp = ledgered
    research.run(r, cfg)
    ledger_update.run(r, cfg)
    assert row_paths(r, "L02", followup=True)[0].exists()
    r.force = True
    research.run(r, cfg, rows="L02")
    assert not row_paths(r, "L02", followup=True)[0].exists()
    assert list((r.path("research_dir") / "_superseded").glob("L02.followup.*.md"))
    assert "Weil at Solesmes" not in r.path("dossier").read_text()
    ledger_update.run(r, cfg)  # a new follow-up is allowed
    assert resp.row_calls["L02 follow-up"] == 2


def test_partly_failed_followups_rerun_without_a_false_hand_edit_error(ledgered, cfg, monkeypatch):
    r, resp = ledgered
    resp.judge_rounds = [{"L01": "partial", "L02": "partial", "L03": "filled"},
                         {"L01": "filled", "L02": "filled", "L03": "filled"}]
    research.run(r, cfg)
    real = resp.__call__

    def fail_l01_followup(kwargs):
        msg = real(kwargs)
        t = all_text(kwargs)
        if "This is a follow-up." in t and "Your idea is row L01" in t and resp.row_calls["L01 follow-up"] == 1:
            msg.content = [type(msg.content[-1])(type="text", text="I ran out.", citations=None)]
        return msg

    monkeypatch.setattr(llm, "_client", FakeAnthropic(fail_l01_followup))
    with pytest.raises(StageError, match="follow-up agents failed"):
        ledger_update.run(r, cfg)
    assert "Weil at Solesmes" in r.path("dossier").read_text()  # the finished follow-up is merged
    resp.judge_calls = 0
    ledger_update.run(r, cfg)  # no --force needed, and only L01's follow-up runs again
    assert resp.row_calls["L02 follow-up"] == 1 and resp.row_calls["L01 follow-up"] == 2
    assert "L02 (an earlier run)" in r.path("ledger_update").read_text()


def test_hand_edit_is_caught_before_any_paid_call(ledgered, cfg):
    r, resp = ledgered
    research.run(r, cfg)
    r.path("dossier").write_text(r.path("dossier").read_text() + "\nIan's note.\n")
    with pytest.raises(StageError, match="edited by hand"):
        ledger_update.run(r, cfg)
    assert resp.judge_calls == 0


def test_filled_with_no_tiered_or_retrieved_entry_is_partial(ledgered, cfg):
    r, _ = ledgered
    led = ledger.load(r, cfg)
    dossier = ("## Entries\n"
               "[D01] (L01, material, tier episode) A scene. Source: episode\n"
               "[D02] (L02, claim, tier 1) A fact from a page nobody fetched. Source: https://made-up.org/x\n"
               "[D03] (L02, material, tier 1) Weil. Source: https://en.wikipedia.org/wiki/Weil\n"
               "[D04] (L03, claim, tier 2, corrected) The episode says X. In fact Y. Source: https://a.org\n")
    verdicts = {"rows": [{"id": x, "status": "filled", "dossier_refs": [], "best_tier": 1, "gap": ""}
                         for x in ("L01", "L02", "L03")], "connections": []}
    ledger_update.apply(led, verdicts, dossier, cfg, seen_urls={"https://a.org", "https://en.wikipedia.org/wiki/Weil"})
    rows = ledger.by_id(led)
    assert rows["L01"]["status"] == "partial" and rows["L01"]["best_tier"] is None  # episode only
    assert rows["L02"]["status"] == "partial" and rows["L02"]["best_tier"] == 4  # unseen URL; wikipedia is tier 4
    assert rows["L03"]["status"] == "filled" and rows["L03"]["best_tier"] == 2  # the judge's 1 is clamped


def test_verdict_variants_are_read():
    for group in ("claim, tier 2, verdict: corrected", "claim, tier 2, Corrected.", "claim, tier 2, **corrected**"):
        assert parse_tags(group)["verdict"] == "corrected", group


def test_rows_ranges_and_bad_values():
    assert parse_rows("L01-L03") == ["L01", "L02", "L03"]
    assert parse_rows("L1, 4") == ["L01", "L04"]
    for bad in ("all", "L03-L01", ""):
        with pytest.raises(StageError):
            parse_rows(bad)


def test_ledger_output_that_fails_validation_is_kept(run, cfg, monkeypatch):
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("t")
    run.path("map").write_text("m")
    run.path("angle").write_text("a")
    resp = Responder()
    real = resp.__call__

    def blank_idea(kwargs):
        msg = real(kwargs)
        if msg.content[0].text.startswith('{"rows"'):
            data = json.loads(msg.content[0].text)
            data["rows"][0]["idea"] = ""
            msg.content[0].text = json.dumps(data)
        return msg

    monkeypatch.setattr(llm, "_client", FakeAnthropic(blank_idea))
    with pytest.raises(StageError, match="_partial_ledger.json"):
        ledger.run(run, cfg)
    assert json.loads((run.root / "_partial_ledger.json").read_text())["rows"]
    assert not run.path("ledger").exists()


def test_rank_gaps_close_and_bad_connections_are_reported(run, cfg):
    rows = [{"id": f"L0{i}", "rank": rank, "idea": f"I{i}", "type": "missing", "research_question": "Q?",
             "tier_needed": 2} for i, rank in ((1, 1), (2, 3), (3, 4), (4, 9))]
    run.path("ledger").write_text(json.dumps({"rows": rows, "parked": []}))
    led = ledger.load(run, cfg)
    assert [x["rank"] for x in ledger.active(led)] == [1, 2, 3, 4]
    assert ledger.budget(cfg, ledger.by_id(led)["L03"]) == (8, 4)  # third in order: a top-ranks budget
    run.path("ledger").write_text(json.dumps({"rows": rows, "parked": [], "connections": ["L01 and L02"]}))
    with pytest.raises(StageError, match="connections must be"):
        ledger.load(run, cfg)


def test_quote_check_short_pieces_word_boundaries_and_apostrophes():
    page = [_norm_text(PAGE_TEXT + " Diotima said: 'Love is the desire for the good.'")]
    assert quote_found('"He who has been instructed . . . things of love"', page)
    assert quote_found('"He who has been instructed ... things of hate"', page) is False  # short piece checked
    assert quote_found('"who has been instructed thus fa"', page) is False  # whole words only
    assert quote_found('"Love is the desire for the good."', page)  # nested single quotes on the page


def test_code_quote_check_reads_only_the_quoted_passage():
    dossier = ('## Entries\n[D01] (L01, quote, tier 1, verbatim: yes) "He who has been instructed thus far" '
               "Plato, Symposium 210e, translated by Benjamin Jowett for Oxford. Source: x\n")
    script = ('<!-- seg01 -->\n\nPlato: "He who has been instructed thus far." [D01] '
              'It was "translated by Benjamin Jowett for Oxford".')
    assert coverage.unbacked_quotes(script, dossier) == [(1, "translated by Benjamin Jowett for Oxford")]


def test_unclosed_marker_does_not_swallow_narration():
    s = "Weil said so. [D3 And Plato wrote [D04] more. [D05, D06]"
    assert spoken_text(s) == "Weil said so. [D3 And Plato wrote more."
    assert claim_sentences("One. [D03] Two [D4-D6]")[1][1] == [4, 5, 6]


def test_dossier_sublist_with_notes():
    segs = brief.parse_segments("## Segment 1: A\n- Dossier:\n  - D03: the ladder\n  - D07: a scene\n"
                                "- Ledger:\n  - L01: main\n- Target words: 700\n")
    assert segs[0]["entries"] == [3, 7] and segs[0]["rows"] == ["L01"]


def test_verify_prompt_uses_the_row_tier(ledgered, cfg):
    r, _ = ledgered
    led = ledger.load(r, cfg)
    row = ledger.by_id(led)["L03"]
    row["tier_needed"] = 1
    text = research.agent_prompt(cfg, row, led, "a", "m")
    assert "sources of tier 1 or better" in text and "tier 1 or 2 sources" not in text


def test_ledger_names_the_current_rank1_row(ledgered, cfg, capsys):
    r, _ = ledgered
    led = json.loads(r.path("ledger").read_text())
    led["rows"][0]["rank"], led["rows"][1]["rank"] = 2, 1
    r.path("ledger").write_text(json.dumps(led))
    ledger.run(r, cfg)
    assert "--rows L02 (the rank-1 row first)" in capsys.readouterr().out


def test_entry_tags_of_a_rebuilt_dossier_round_trip(ledgered, cfg):
    r, _ = ledgered
    research.run(r, cfg)
    for n, text in dossier_entries(r.path("dossier").read_text()).items():
        t = entry_tags(text)
        assert t["kind"] and t["rows"], text


def test_new_ledger_moves_old_research_aside(ledgered, cfg):
    r, _ = ledgered
    research.run(r, cfg, rows="L01")
    r.force = True
    ledger.run(r, cfg)
    assert not row_paths(r, "L01")[0].exists()
    assert list(r.root.glob("04_research.old-*/L01.md"))
