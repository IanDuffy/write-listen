"""The whole pipeline, end to end, against the fake APIs. Real files, real ffmpeg."""

import json

import pytest
from fakes import all_text

from stages import (angles, assemble, baseline, brief, compare, coverage, draft, edit, ledger, ledger_update,
                    map as map_stage, narrate, report, research, revise)
from stages.common import StageError, split_script_segments


@pytest.fixture
def ingested(run):
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("David Brooks: Can beauty make us good?\n\nElaine Scarry: Yes.\n")
    return run


def test_full_run(ingested, cfg, responder, fake_tts):
    r = ingested
    r.yes = True  # research's whole-stage estimate is over the $5 guard
    map_stage.run(r, cfg)
    angles.run(r, cfg)
    with pytest.raises(StageError, match="03_angle.md"):
        ledger.run(r, cfg)  # stops at the [Ian] angle checkpoint
    r.path("angle").write_text("# The ladder and the self\n\nAngle 1, the ladder. Notes: more Weil, please.\n")

    # 03b: the ledger, numbered by rank, then [Ian] reviews it.
    ledger.run(r, cfg)
    led = json.loads(r.path("ledger").read_text())
    assert [(x["id"], x["rank"], x["type"]) for x in led["rows"]] == [
        ("L01", 1, "deep-dive"), ("L02", 2, "missing"), ("L03", 3, "covered")]
    assert led["parked"] == ["Kant on disinterested pleasure."]
    ledger_call = responder.calls[-1]
    assert ledger_call["output_config"]["format"]["type"] == "json_schema"
    assert "Brooks: Can beauty make us good?" in all_text(ledger_call)  # the full transcript, no summary
    md = r.path("ledger_md").read_text()
    assert "| L01 | 1 | deep-dive |" in md and "## Parked" in md

    # Stage 04: the rank-1 agent first, for Ian to read before the rest.
    research.run(r, cfg, rows="L01")
    assert research.row_paths(r, "L01")[0].exists() and not research.row_paths(r, "L02")[0].exists()
    assert not r.path("dossier").exists()
    l01 = [c for c in responder.calls if "Your idea is row L01" in all_text(c)]
    assert len(l01) == 2  # the agent, then its write-up: a loop paused at its budget is not resumed
    assert l01[0]["tools"][0]["max_uses"] == 8 and l01[0]["tools"][1]["max_uses"] == 4
    assert "tools" not in l01[1] and "your search and fetch budget is now spent" in all_text(l01[1])
    assert "Symposium" in all_text(l01[1])  # the fetched page goes to the write-up
    assert json.loads(research.row_paths(r, "L01")[1].read_text())["written_up"] is True

    research.run(r, cfg)  # the rest; L01 is not paid for twice
    assert responder.row_calls == {"L01": 1, "L02": 2, "L03": 1}
    l02 = [c for c in responder.calls if "Your idea is row L02" in all_text(c)]
    assert l02[1]["messages"][-1]["role"] == "assistant" and l02[1]["tools"] == l02[0]["tools"]  # resumed
    assert l02[0]["tools"][0]["max_uses"] == 8
    l03 = [c for c in responder.calls if "This idea is row L03" in all_text(c)][0]
    assert "You are a fact-checker" in all_text(l03) and l03["tools"][0]["max_uses"] == 2
    assert l03["model"] == cfg["models"]["researcher"] == "claude-opus-5-5"
    assert l03["fallbacks"] == "default" and l03["cache_control"] == {"type": "ephemeral"}

    dossier = r.path("dossier").read_text()
    assert dossier.startswith("# Dossier: The ladder and the self")
    assert "### L01: Plato's ladder" in dossier and "### L03: Beauty prompts" in dossier
    assert "[D01] (L01, L02, claim, tier 1) In the Symposium" in dossier  # exact duplicate merged
    assert dossier.count("Socrates attributes his teaching") == 1
    assert "[D02] (L01, quote, tier 1, verbatim: yes) \"He who has been instructed" in dossier
    assert "(L02, quote, tier 1, verbatim: yes) \"He who has been instructed" in dossier
    assert "(L01, quote, tier 1, verbatim: no) \"A sentence that appears on no page" in dossier  # downgraded
    assert "L01's idea abandons persons; compare D01." in dossier  # an agent's reference renumbered
    assert "L02's idea abandons persons; compare D01." in dossier  # ...to the merged duplicate
    check = dossier.split("## Source check")[1]
    assert "https://invented.example.com/page" in check and "not found on the fetched page" in check
    assert "Tiers better than the source's domain" in check and "wikipedia.org is tier 4" in check
    assert "Entries with no tags" in check and "Rows with no tellable material: L02" in check
    assert "Searches: " in check and "L01 8 of 8, written up at its budget" in check

    # Stage 04b: judge, L02 partial -> one follow-up with its gap, rebuild, judge again.
    ledger_update.run(r, cfg)
    assert responder.judge_calls == 2 and responder.row_calls["L02 follow-up"] == 1
    fu = [c for c in responder.calls if "This is a follow-up." in all_text(c)][0]
    assert "No tellable material for Weil" in all_text(fu) and fu["tools"][0]["max_uses"] == 4
    assert research.row_paths(r, "L02", followup=True)[0].exists()
    led = json.loads(r.path("ledger").read_text())
    rows = {x["id"]: x for x in led["rows"]}
    assert all(x["status"] == "filled" for x in rows.values())
    assert rows["L02"]["followup"] is True and rows["L01"]["followup"] is False
    assert "D99" not in rows["L01"]["dossier_refs"] and "D01" in rows["L02"]["dossier_refs"]
    assert led["connections"] == [{"rows": ["L01", "L02"], "link": "Both turn the self outward."}]
    dossier = r.path("dossier").read_text()
    assert "Weil at Solesmes" in dossier
    assert "## Connections" in r.path("ledger_md").read_text()
    with pytest.raises(StageError, match="--force"):
        ledger_update.run(r, cfg)

    brief.run(r, cfg)
    b = r.path("brief").read_text()
    assert b.rstrip().endswith("Source: SEP, https://plato.stanford.edu/entries/plato/")  # corrections, by code
    assert "## Corrections" in b and "In fact the dinner is set in 416 BC" in b.split("## Corrections")[1]
    brief_call = responder.calls[-1]
    assert "Research status: filled" in all_text(brief_call) and "Both turn the self outward." in all_text(brief_call)

    # Pilot: segments 1-2, then narrate them.
    draft.run(r, cfg, segments="1-2")
    assert r.segment_path(1).exists() and r.segment_path(2).exists() and not r.segment_path(3).exists()
    seg2_call = responder.calls[-1]
    assert seg2_call["system"][0]["cache_control"] == {"type": "ephemeral"}
    earlier_block = seg2_call["messages"][0]["content"][0]
    assert "Segment 1 opens" in earlier_block["text"] and earlier_block["cache_control"]
    assert "[D02] (L01, quote" in seg2_call["messages"][0]["content"][1]["text"]  # this segment's entries

    narrate.run(r, cfg, pilot=True)
    assert (r.path("audio_dir") / "pilot.mp3").stat().st_size > 1000
    assert all("[D" not in c["text"] for c in fake_tts)  # markers never reach the voice

    # Draft the rest: existing segments are kept, not redrafted.
    r.segment_path(1).write_text(r.segment_path(1).read_text() + "\n\nIan added this line.\n")
    n_calls = len(responder.calls)
    draft.run(r, cfg)
    assert len(responder.calls) == n_calls + 1
    assert "Ian added this line." in r.segment_path(1).read_text()

    assemble.run(r, cfg)
    assert [n for n, _ in split_script_segments(r.path("script_draft").read_text())] == [1, 2, 3]
    with pytest.raises(StageError, match="07b_coverage.md"):
        edit.run(r, cfg)  # the editor needs the coverage check

    # 07b: coverage columns into the ledger, the report for the editor.
    coverage.run(r, cfg)
    rows = {x["id"]: x for x in json.loads(r.path("ledger").read_text())["rows"]}
    assert rows["L02"]["coverage"] == "thin" and rows["L02"]["segments"] == [2]
    assert rows["L03"]["note"] == "Never lands."
    cov = r.path("coverage").read_text()
    assert "Clear until segment 3." in cov and "Dinner-party logistics" in cov
    # The reader flags it once; code flags it in each segment it appears in.
    assert cov.count("a sentence nobody ever wrote down anywhere") == 4 and 'seg03: "a sentence nobody' in cov
    assert '"He who has been instructed' not in cov.split("Found by code")[1]  # a verbatim entry is fine
    assert "Coverage notes" in r.path("ledger_md").read_text()

    edit.run(r, cfg)
    assert "Never lands." in all_text(responder.calls[-1])  # the editor read 07b_coverage.md
    revise.run(r, cfg)
    final = r.path("script_final").read_text()
    assert "slowly and plainly" not in final
    assert [n for n, _ in split_script_segments(final)] == [1, 2, 3]

    tts_before = len(fake_tts)
    narrate.run(r, cfg)
    assert r.path("chapter_mp3").stat().st_size > 1000
    spoken = " ".join(c["text"] for c in fake_tts[tts_before:])
    assert "Dee-oh-TEE-mah" in spoken and "Diotima" not in spoken  # pronunciation table applied
    assert "seg0" not in spoken and "[D" not in spoken

    baseline.run(r, cfg)
    assert r.path("baseline_script").read_text().startswith("A baseline paragraph")
    narrate.run(r, cfg, baseline=True)
    assert r.path("baseline_mp3").exists()

    compare.run(r, cfg)
    key = (r.path("compare_dir") / "key.txt").read_text()
    assert sorted(key.split()) == sorted(["A.mp3", "=", "pipeline", "B.mp3", "=", "baseline"]) or \
        sorted(key.split()) == sorted(["A.mp3", "=", "baseline", "B.mp3", "=", "pipeline"])

    report.run(r, cfg, seed=1)
    rep = r.path("report").read_text()
    assert "## Spot-check" in rep and "Diotima of Mantinea" in rep
    assert "| narrate |" in rep and "| research_row |" in rep and "| ledger_update |" in rep
    assert "0:00" not in rep.split("## Length")[1].split("## Cost")[0]

    log = [json.loads(line) for line in r.path("log").read_text().splitlines()]
    assert {"map", "angles", "ledger", "research_row", "research_writeup", "research_followup", "ledger_update",
            "brief", "draft", "coverage", "edit", "revise", "baseline",
            "narrate", "narrate_pilot", "narrate_baseline"} <= {rec["stage"] for rec in log}
    assert {rec.get("row") for rec in log if rec["stage"].startswith("research")} == {"L01", "L02", "L03"}
    assert all("cost_usd" in rec for rec in log)
    assert not any("sk-" in json.dumps(rec) for rec in log)


def test_narration_reuses_cached_chunks(ingested, cfg, fake_tts):
    r = ingested
    r.path("script_final").write_text("<!-- seg01 -->\n\nOne paragraph. [D01]\n\n<!-- seg02 -->\n\nTwo.\n")
    narrate.run(r, cfg)
    assert len(fake_tts) == 2
    r.force = True
    narrate.run(r, cfg)
    assert len(fake_tts) == 2  # nothing changed, nothing re-synthesized


def test_draft_needs_earlier_segments(ingested, cfg, responder):
    r = ingested
    r.path("dossier").write_text("[D01] (L01, claim, tier episode) x. Source: episode\n")
    from fakes import brief_text
    r.path("brief").write_text(brief_text(3))
    with pytest.raises(StageError, match="seg01 before seg03"):
        draft.run(r, cfg, segments="3")
