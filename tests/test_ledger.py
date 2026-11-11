"""The ledger: data model, stage 03b, Ian's hand edits, the brief's checks, stage 07b."""

import json

import pytest
from fakes import LEDGER, FakeAnthropic, Responder, brief_text

from stages import brief, coverage, ledger, llm
from stages.common import StageError


def row(**kw):
    base = {"id": "L01", "rank": 1, "idea": "An idea.", "type": "deep-dive", "episode_has": "",
            "research_question": "A question?", "tier_needed": 2}
    return {**base, **kw}


def test_validate_lists_every_problem():
    led = {"rows": [row(), row(id="L01", rank=1, type="topic", tier_needed=5),
                    row(id="X3", rank=3), row(id="L04", rank=4, idea=" ")], "parked": [3]}
    with pytest.raises(StageError) as e:
        ledger.validate(led)
    msg = str(e.value)
    for part in ("L01 appears twice", "L01 and L01 are both ranked 1", "type must be one of",
                 "tier_needed must be 1, 2, 3 or 4", "row 3: id 'X3' should look like L01 (the next free id is L05)",
                 "L04: idea must be a non-empty string", "parked must be a list"):
        assert part in msg


def test_validate_warns_on_row_count(cfg):
    assert any("5-7" in w for w in ledger.validate({"rows": [row()]}, cfg))
    rows = [row(id=f"L{i:02d}", rank=i) for i in range(1, 6)]
    assert ledger.validate({"rows": rows}, cfg) == []


def test_budget_by_rank_and_type(cfg):
    assert ledger.budget(cfg, row(rank=3)) == (8, 4)
    assert ledger.budget(cfg, row(rank=4, type="missing")) == (4, 2)
    assert ledger.budget(cfg, row(rank=1, type="covered")) == (2, 1)
    assert ledger.budget(cfg, row(rank=1), followup=True) == (4, 2)


@pytest.fixture
def staged(run, monkeypatch):
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("Brooks: hi.\n")
    run.path("map").write_text("m")
    run.path("angle").write_text("a")
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    return run, resp


def test_ledger_stage_numbers_by_rank_and_rerun_only_checks(staged, cfg):
    r, resp = staged
    ledger.run(r, cfg)
    led = json.loads(r.path("ledger").read_text())
    assert [x["idea"] for x in led["rows"]] == [x["idea"] for x in sorted(LEDGER["rows"], key=lambda x: x["rank"])]
    assert led["rows"][0]["status"] is None and led["rows"][0]["dossier_refs"] == []
    n = len(resp.calls)

    # Ian parks L03 and re-ranks; a re-run checks the file and re-renders ledger.md without the model.
    led["parked"].append(led["rows"].pop(2)["idea"])
    led["rows"][0]["rank"], led["rows"][1]["rank"] = 2, 1
    led["rows"][1]["note_for_me"] = "keep"
    r.path("ledger").write_text(json.dumps(led))
    ledger.run(r, cfg)
    assert len(resp.calls) == n
    md = r.path("ledger_md").read_text()
    assert md.index("| L02 | 1 |") < md.index("| L01 | 2 |")
    assert "- Beauty prompts a desire to replicate." in md.split("## Parked")[1]

    led["rows"][0]["type"] = "maybe"
    r.path("ledger").write_text(json.dumps(led))
    with pytest.raises(StageError, match="L01: type"):
        ledger.run(r, cfg)
    r.path("ledger").write_text("{not json")
    with pytest.raises(StageError, match="not valid JSON"):
        ledger.run(r, cfg)


def test_extra_fields_survive_a_save(staged, cfg):
    r, _ = staged
    ledger.run(r, cfg)
    led = json.loads(r.path("ledger").read_text())
    led["rows"][0]["ian"] = "my note"
    r.path("ledger").write_text(json.dumps(led))
    led = ledger.load(r, cfg)
    ledger.save(r, led)
    assert json.loads(r.path("ledger").read_text())["rows"][0]["ian"] == "my note"


def test_render_escapes_pipes():
    led = ledger.normalize({"rows": [row(idea="Either | or")], "parked": []})
    assert "Either \\| or" in ledger.render_md(led, "x")


def _ledger(r, rows):
    r.path("ledger").write_text(json.dumps({"rows": rows, "parked": []}))


def test_brief_check_warns_about_unassigned_rows_and_unknown_entries(run, cfg):
    _ledger(run, [row(), row(id="L02", rank=2), row(id="L03", rank=3)])
    led = ledger.load(run, cfg)
    dossier = "## Entries\n[D01] (L01, claim, tier 1) a\n[D02] (L01, claim, tier 1) b\n"
    warnings = brief.check(brief_text(2, rows=("L01", "L09")), dossier, led)
    assert any("assigned to no segment: L02, L03" in w for w in warnings)
    assert any("rows not in the ledger: L09" in w for w in warnings)
    assert any("entries not in the dossier: D03" in w for w in warnings)


def test_corrections_are_listed(run):
    d = ("## Entries\n[D01] (L01, claim, tier 2, confirmed) ok.\n"
         "[D02] (L03, claim, tier 1, corrected) The episode says 1999. In fact 1998. Source: https://a.org\n")
    assert brief.corrections(d) == ["[D02] (L03, claim, tier 1, corrected) The episode says 1999. In fact 1998. "
                                    "Source: https://a.org"]


def test_code_quote_check():
    dossier = ('## Entries\n[D01] (L01, quote, tier 1, verbatim: yes) "He who has been instructed thus far in '
               'the things of love" Source: x\n[D02] (L01, quote, tier 1, verbatim: no) "a paraphrase given as a '
               'quote by mistake" Source: y\n')
    script = ('<!-- seg01 -->\n\nPlato: "He who has been instructed ... the things of love." [D01] '
              'And "a paraphrase given as a quote by mistake". A "short title" is fine.\n\n'
              '<!-- seg02 -->\n\n"Words nobody wrote down anywhere at all."')
    assert coverage.unbacked_quotes(script, dossier) == [
        (1, "a paraphrase given as a quote by mistake"), (2, "Words nobody wrote down anywhere at all.")]


def test_coverage_needs_a_verdict_for_every_row(staged, cfg, monkeypatch):
    r, resp = staged
    ledger.run(r, cfg)
    r.path("brief").write_text(brief_text(2))
    r.path("dossier").write_text("## Entries\n[D01] (L01, claim, tier 1) a\n")
    r.path("script_draft").write_text("<!-- seg01 -->\n\nOne.\n\n<!-- seg02 -->\n\nTwo.\n")
    real = resp.__call__

    def drop_l03(kwargs):
        msg = real(kwargs)
        text = msg.content[0].text
        if text.startswith('{"rows": [{"id": "L01", "coverage"'):
            data = json.loads(text)
            data["rows"] = [x for x in data["rows"] if x["id"] != "L03"]
            msg.content[0].text = json.dumps(data)
        return msg

    monkeypatch.setattr(llm, "_client", FakeAnthropic(drop_l03))
    with pytest.raises(StageError, match="no verdict for L03"):
        coverage.run(r, cfg)
    assert not r.path("coverage").exists()
