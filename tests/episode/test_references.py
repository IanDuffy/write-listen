"""Stage 2 (references) and stage 3 (bundle), acceptance check 5, with the model's answers replayed."""

import json

import pytest

from episode import bundle, references
from stages.common import StageError
from stages.research import URL_IN_TEXT


@pytest.fixture
def replay(monkeypatch, timed):
    run, _ = timed
    calls = []

    def ask(r, cfg, seg, prompt):
        calls.append((seg.id, prompt))
        return json.loads((run / "references_recorded" / f"{seg.id}.json").read_text())
    monkeypatch.setattr(references, "ask", ask)
    return run, calls


def test_references_follow_the_rules(replay, cfg):  # check 5
    run, calls = replay
    refs, cost = references.build(run, cfg)
    assert [c[0] for c in calls] == ["seg01", "seg02"] and cost == 0
    timing = json.loads((run / "11_episode" / "timing.json").read_text())
    start = {x["id"]: x["start"] for s in timing["segments"] for x in s["sentences"]}
    assert all(r["sentence_id"] in start for r in refs)  # every reference points to a real sentence
    dossier_urls = set(URL_IN_TEXT.findall((run / "04_dossier.md").read_text()))
    assert all(r["url"] is None or r["url"] in dossier_urls for r in refs)  # every URL from the dossier
    sids = [r["sentence_id"] for r in refs]
    assert len(sids) == len(set(sids))  # at most one card per sentence
    for i, a in enumerate(refs):  # no source repeated within 60 seconds
        for b in refs[i + 1:]:
            if start[b["sentence_id"]] - start[a["sentence_id"]] < references.REPEAT_GAP:
                assert not (references._keys(a) & references._keys(b)), (a, b)
    assert all(r["image"] is None and r["kind"] in ("source", "work", "person", "quote") for r in refs)

    by = {r["sentence_id"]: r for r in refs}
    assert by["seg01-s003"]["kind"] == "quote" and by["seg01-s003"]["title"] == "a houseful of children"
    assert by["seg02-s001"] == {**by["seg02-s001"], "kind": "source", "dossier_ref": "D02",
                                "url": "https://example.org/symposium", "tier": 1}
    assert "seg02-s002" not in by  # D01 was not offered for seg02, and its quote is not in the sentence
    assert by["seg02-s003"]["dossier_ref"] == "D03"  # D02 again within a minute is skipped; D03 is new
    assert "D99" not in json.dumps(refs) and "words nobody said" not in json.dumps(refs)
    assert "Invented" not in json.dumps(json.loads((run / "11_episode" / "attachments.json").read_text()))


def test_offered_entries_are_the_briefs_plus_markers(timed, cfg):
    from episode import script
    from stages.common import dossier_entries
    run, _ = timed
    segs = script.parse((run / "09_script_final.md").read_text(), cfg["narration"]["pronunciations"])
    offered = references.segment_entries(run, segs, dossier_entries((run / "04_dossier.md").read_text()))
    assert offered == {"seg01": ["D01"], "seg02": ["D02", "D03", "D04"]}


def test_answers_are_reused_and_the_cap_is_checked_first(replay, cfg):
    run, calls = replay
    references.build(run, cfg)
    references.build(run, cfg)
    assert len(calls) == 2  # same prompts: the saved answers are reused, nothing is asked again
    for p in (run / "11_episode" / "references_raw").glob("*.json"):
        p.unlink()
    with pytest.raises(StageError, match="cap"):
        references.build(run, cfg, max_usd=0.0001)
    assert len(calls) == 2  # stopped before the call


def test_references_need_timing(small_run, cfg):
    with pytest.raises(StageError, match="timing.json first"):
        references.build(small_run, cfg)


def test_bundle_is_what_the_page_reads(replay, cfg):
    run, _ = replay
    references.write(run, references.build(run, cfg)[0])
    ep = json.loads(bundle.write(run).read_text())
    assert ep["title"] == "A Small Test Chapter" and ep["angle"] == "A Small Test Angle"
    assert ep["audio"] == "10_audio/chapter.mp3" and ep["duration"] == pytest.approx(25.59, abs=0.1)
    assert [s["id"] for s in ep["segments"]] == ["seg01", "seg02"]
    starts = {x["id"]: x["start"] for s in ep["segments"] for x in s["sentences"]}
    assert all(r["start"] == starts[r["sentence_id"]] for r in ep["references"])
    assert [r["start"] for r in ep["references"]] == sorted(r["start"] for r in ep["references"])


def test_reasoning_entries_are_not_shown_as_sources():
    from episode import script
    seg = script.Segment("seg01", 1, [script.Sentence("seg01-s001", [script.Word("Hello.", "Hello.")], ["D05"])])
    entries = {5: "[D05] (L01, connection, L02) Two rows meet here.", 6: "[D06] (L01, claim, tier 2) A fact. Source: X, https://example.org/x"}
    answer = {"sentences": [{"id": "seg01-s001", "works": [], "people": [], "quotes": [],
                             "sources": [{"dossier": "D05", "title": "A link", "creator": "", "note": ""},
                                         {"dossier": "D06", "title": "X", "creator": "", "note": ""}]}]}
    cards = references.candidates(seg, answer, ["D05", "D06"], entries)
    assert [c["dossier_ref"] for c in cards["seg01-s001"]] == ["D06"]


def test_links_follow_back_references_and_people_are_unlinked():
    from episode import script
    entries = {1: "[D01] (L01, claim, tier 2) A. Source: SEP, https://example.org/sep",
               2: "[D02] (L01, quote, tier 2) B. Source: SEP, §4, same URL as D01",
               3: "[D03] (L01, claim, tier 2) C. Source: as in D02"}
    assert references.entry_link(entries[3], entries) == "https://example.org/sep"
    seg = script.Segment("seg01", 1, [script.Sentence("seg01-s001", [script.Word("Reeve", "Reeve")], ["D02"])])
    ans = {"sentences": [{"id": "seg01-s001", "sources": [], "works": [], "quotes": [],
                          "people": [{"name": "C. D. C. Reeve", "dossier": "D02", "note": "a scholar"}]}]}
    (card,) = references.candidates(seg, ans, ["D01", "D02", "D03"], entries)["seg01-s001"]
    assert card["url"] is None and card["dossier_ref"] == "D02"
    a = {**card, "title": "C. D. C. Reeve"}
    b = {**card, "title": "Reeve", "dossier_ref": None}
    assert references._keys(a) & references._keys(b)  # one person for the repeat rule
