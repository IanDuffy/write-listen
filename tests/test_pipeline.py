"""The whole pipeline, end to end, against the fake APIs. Real files, real ffmpeg."""

import json

import pytest
from fakes import all_text

from stages import (angles, assemble, baseline, brief, compare, draft, edit, map as map_stage, narrate,
                    report, research, revise)
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
        research.run(r, cfg)  # stops at the [Ian] checkpoint
    r.path("angle").write_text("Angle 1, the ladder. Notes: more Weil, please.\n")

    # Research: plan, agents in parallel, critic rounds, merge.
    research.run(r, cfg)
    plan = r.path("research_plan").read_text()
    assert "## T3: Sources for T1.5" in plan  # the critic's follow-up joined the plan
    assert {p.name for p in r.path("topics_dir").glob("T*.md")} == {"T01.md", "T02.md", "T03.md"}
    assert responder.critic_calls == 2  # round 2 added a topic; round 3 found nothing more

    dossier = r.path("dossier").read_text()
    assert dossier.startswith("# Dossier: Beauty as a ladder")
    assert "## Overview" in dossier and "Now writing" not in dossier
    assert "[C1] In the Symposium" in dossier and "[C15]" in dossier and "[C16]" not in dossier
    assert "C1 and C3 do not conflict" in dossier  # cross references renumbered
    check = dossier.split("## Source check")[1]
    assert "https://invented.example.com/page" in check
    assert "Claims with no source URL: C5, C10, C15" in check
    assert "Quotes matched word for word against the fetched page: 3" in check
    assert "NOT found in any fetched page" in check and "C4, C9, C14" in check

    topic_calls = [c for c in responder.calls if "You are one of several research agents" in all_text(c)]
    assert all(c["model"] == cfg["models"]["researcher"] for c in topic_calls)
    t1 = [c for c in topic_calls if "<topic>\n## T1:" in all_text(c)]
    assert len(t1) == 2 and t1[1]["messages"][-1]["role"] == "assistant"  # paused turn resumed
    assert t1[1]["tools"] == t1[0]["tools"]  # tools must not change between continuations
    assert t1[0]["cache_control"] == {"type": "ephemeral"}
    assert t1[0]["fallbacks"] == "default" and t1[0]["betas"] == ["server-side-fallback-2026-07-01"]

    # Re-running keeps paid-for topics.
    n_calls = len(responder.calls)
    with pytest.raises(StageError, match="--force"):
        research.run(r, cfg)
    r.force = True
    research.run(r, cfg)
    r.force = False
    new_calls = responder.calls[n_calls:]
    assert not any("You are one of several research agents" in all_text(c) for c in new_calls)

    brief.run(r, cfg)

    # Pilot: segments 1-2, then narrate them.
    draft.run(r, cfg, segments="1-2")
    assert r.segment_path(1).exists() and r.segment_path(2).exists() and not r.segment_path(3).exists()
    seg2_call = responder.calls[-1]
    assert seg2_call["system"][0]["cache_control"] == {"type": "ephemeral"}
    earlier_block = seg2_call["messages"][0]["content"][0]
    assert "Segment 1 opens" in earlier_block["text"] and earlier_block["cache_control"]
    assert "[C2]" in seg2_call["messages"][0]["content"][1]["text"]  # this segment's claims, in full

    narrate.run(r, cfg, pilot=True)
    assert (r.path("audio_dir") / "pilot.mp3").stat().st_size > 1000
    assert all("[C" not in c["text"] for c in fake_tts)  # markers never reach the voice

    # Draft the rest: existing segments are kept, not redrafted.
    r.segment_path(1).write_text(r.segment_path(1).read_text() + "\n\nIan added this line.\n")
    n_calls = len(responder.calls)
    draft.run(r, cfg)
    assert len(responder.calls) == n_calls + 1
    assert "Ian added this line." in r.segment_path(1).read_text()

    assemble.run(r, cfg)
    assert [n for n, _ in split_script_segments(r.path("script_draft").read_text())] == [1, 2, 3]
    edit.run(r, cfg)
    revise.run(r, cfg)
    final = r.path("script_final").read_text()
    assert "slowly and plainly" not in final
    assert [n for n, _ in split_script_segments(final)] == [1, 2, 3]

    tts_before = len(fake_tts)
    narrate.run(r, cfg)
    assert r.path("chapter_mp3").stat().st_size > 1000
    spoken = " ".join(c["text"] for c in fake_tts[tts_before:])
    assert "Dee-oh-TEE-mah" in spoken and "Diotima" not in spoken  # pronunciation table applied
    assert "seg0" not in spoken

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
    assert "| narrate |" in rep and "| research_topic |" in rep and "| research_critic |" in rep
    assert "0:00" not in rep.split("## Length")[1].split("## Cost")[0]

    log = [json.loads(l) for l in r.path("log").read_text().splitlines()]
    assert {"map", "angles", "research_plan", "research_topic", "research_critic", "research_synthesis", "brief", "draft", "edit", "revise", "baseline",
            "narrate", "narrate_pilot", "narrate_baseline"} <= {rec["stage"] for rec in log}
    assert all("cost_usd" in rec for rec in log)
    assert not any("sk-" in json.dumps(rec) for rec in log)


def test_narration_reuses_cached_chunks(ingested, cfg, fake_tts):
    r = ingested
    r.path("script_final").write_text("<!-- seg01 -->\n\nOne paragraph. [C1]\n\n<!-- seg02 -->\n\nTwo.\n")
    narrate.run(r, cfg)
    assert len(fake_tts) == 2
    r.force = True
    narrate.run(r, cfg)
    assert len(fake_tts) == 2  # nothing changed, nothing re-synthesized


def test_draft_needs_earlier_segments(ingested, cfg, responder):
    r = ingested
    r.path("dossier").write_text("[C1] x. Source: episode\n")
    from fakes import brief_text
    r.path("brief").write_text(brief_text(3))
    with pytest.raises(StageError, match="seg01 before seg03"):
        draft.run(r, cfg, segments="3")
