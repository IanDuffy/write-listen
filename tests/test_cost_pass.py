"""The cost pass (2026-11-11): batched stages, per-stage models, the follow-up cap,
and held beats ([pause]) in narration."""

import json

import pytest
from fakes import Responder

from stages import angles, choose, ledger, ledger_update, llm, map as map_stage, research, tts
from stages.common import StageError

MAP = [{"role": "user", "content": "Map the episode, please."}]


def log(run):
    return [json.loads(line) for line in run.path("log").read_text().splitlines()]


def test_batched_stage_is_half_price_without_fallbacks(run, cfg, responder):
    res = llm.call(run, cfg, "map", messages=MAP)
    assert res.text.startswith("## Episode")
    batches = llm._client.batches
    assert len(batches.created) == 1 and batches.created[0]["model"] == "claude-opus-5-5"  # fake checks no fallbacks
    rec = log(run)[-1]
    assert rec["batch"] is True and rec["request_id"] == "msgbatch_1"
    full = llm.token_cost(cfg, "claude-opus-5-5", input_tokens=1000, output_tokens=500)
    assert rec["cost_usd"] == pytest.approx(full / 2, abs=1e-5)
    assert llm.estimate_stage(cfg, "map", 1000, 500) == pytest.approx(full / 2)


def test_failed_batch_falls_back_to_streaming(run, cfg, responder):
    llm._client.batches.outcome = lambda params: "errored"
    res = llm.call(run, cfg, "map", messages=MAP)
    assert res.text.startswith("## Episode")
    failed, ok = log(run)[-2:]
    assert failed["error"].startswith("batch: errored") and failed["cost_usd"] == 0
    assert "batch" not in ok and responder.calls[-1]["fallbacks"] == "default"  # streamed, with fallbacks


def test_refused_batch_is_resent_streamed_with_fallbacks(run, cfg, responder):
    llm._client.batches.responder = lambda params: Responder.msg(params, "", stop="refusal")
    res = llm.call(run, cfg, "map", messages=MAP)
    assert res.text.startswith("## Episode")
    refused, failed, ok = log(run)[-3:]
    assert refused["stop_reason"] == "refusal" and refused["cost_usd"] > 0  # the refused batch is still paid for
    assert "refusal fallbacks" in failed["error"]
    assert responder.calls[-1]["fallbacks"] == "default"


def test_interrupted_batch_is_resumed_not_paid_twice(run, cfg, responder, monkeypatch):
    batches = llm._client.batches
    batches.status_on_create = "in_progress"
    polls = []

    def hook(bid):
        polls.append(bid)
        if len(polls) == 1:
            raise KeyboardInterrupt  # Ctrl-C, or a Bash timeout, while waiting

    batches.retrieve_hook = hook
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        llm.call(run, cfg, "map", messages=MAP)
    assert len(list(run.root.glob("_batch_*.json"))) == 1  # the id survives the interruption
    res = llm.call(run, cfg, "map", messages=MAP)
    assert res.text.startswith("## Episode") and len(batches.created) == 1 and batches.cancelled == []
    assert not list(run.root.glob("_batch_*.json"))
    # A changed request does not reuse an old batch: it cancels it and sends a new one.
    batches.retrieve_hook = lambda bid: (_ for _ in ()).throw(KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        llm.call(run, cfg, "map", messages=MAP)
    batches.retrieve_hook = None
    llm.call(run, cfg, "map", messages=[{"role": "user", "content": "Map the episode, again."}])
    assert len(batches.created) == 3 and batches.cancelled == ["msgbatch_2"]


def test_batch_results_read_is_retried_then_falls_back(run, cfg, responder, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    batches = llm._client.batches
    batches.results_failures = 1
    llm.call(run, cfg, "map", messages=MAP)
    assert log(run)[-1]["batch"] is True  # one dropped read, then the result
    batches.results_failures = 99
    res = llm.call(run, cfg, "map", messages=MAP)
    failed, ok = log(run)[-2:]
    assert "results unreadable" in failed["error"] and "batch" not in ok and res.text.startswith("## Episode")
    assert batches.cancelled == []  # an ended batch is not cancelled


def test_stage_model_overrides_the_role(run, cfg, responder):
    cfg["stages"]["coverage"]["batch"] = False
    llm.call(run, cfg, "coverage", messages=MAP)
    assert responder.calls[-1]["model"] == "claude-sonnet-5-5"
    assert llm.estimate_stage(cfg, "coverage", 1000, 500) == llm.estimate_call(cfg, "claude-sonnet-5-5", 1000, 500)
    assert not llm.batched(cfg, "map", tools=[{"type": "web_search_20260209"}])  # tool loops never batch


def test_followup_only_for_top_ranked_rows(run, cfg, responder):
    r = run
    r.yes = True
    r.path("source_dir").mkdir()
    r.path("transcript").write_text("David Brooks: Can beauty make us good?\n\nElaine Scarry: Yes.\n")
    for stage in (map_stage, angles, choose, ledger, research):
        stage.run(r, cfg)
    cfg["research"]["followup_max_rank"] = 1  # L02 (rank 2) ends round 1 partial
    ledger_update.run(r, cfg)
    assert responder.judge_calls == 1 and "L02 follow-up" not in responder.row_calls
    rows = {x["id"]: x for x in json.loads(r.path("ledger").read_text())["rows"]}
    assert rows["L02"]["status"] == "partial" and rows["L02"]["followup"] is False
    record = r.path("ledger_update").read_text()
    assert "Still open (for Ian): L02 partial" in record
    assert "Follow-ups: none for L02 (ranked below 1" in record and "none needed" not in record
    # The opening needs the show and the speakers: code adds the episode entry from the map.
    dossier = r.path("dossier").read_text()
    assert "### Episode: the conversation this chapter grows from" in dossier
    assert "(claim, tier episode) A map. Source: episode" in dossier.split("## Entries")[1].split("## Not found")[0]


def test_episode_intro_reads_the_map(run):
    run.path("map").write_text("# Episode Map: *The Show*, \"A Title\" (Host with Guest)\n\n## Episode in brief\n\n"
                               "Host hosts *The Show*. His guest is a professor.\nSecond line.\n\n- a list\n")
    assert research.episode_intro(run) == ('The episode: *The Show*, "A Title" (Host with Guest). '
                                           "Host hosts *The Show*. His guest is a professor. Second line.")


@pytest.fixture
def joined(monkeypatch):
    got = {}
    monkeypatch.setattr(tts, "join_to_mp3", lambda parts, out, work, bitrate: got.setdefault("parts", parts))
    return got


def test_pause_marks_become_held_silence(run, cfg, fake_tts, joined):
    n = cfg["narration"]
    segs = [(1, "The turn comes here. [D01]\n\n[pause]\n\nAnd then the rest. [pause] Still the rest.\n\n[pause]"),
            (2, "[pause]\n\nTwo.")]
    tts.narrate_segments(run, cfg, segs, run.root / "x.mp3", run.root / "work", stage="narrate")
    assert [c["text"] for c in fake_tts] == ["The turn comes here.", "And then the rest.", "Still the rest.", "Two."]
    pauses = [p for p in joined["parts"] if isinstance(p, int)]
    # A pause at a segment's edge is absorbed by the segment break.
    assert pauses == [n["beat_pause_ms"], n["beat_pause_ms"], n["segment_pause_ms"]]


def test_editorial_brackets_in_quotes_are_spoken(run, cfg, fake_tts, joined):
    segs = [(1, 'Plato writes, "[T]he lover only want[ed] to look [...] and to be [sic] with them." [D42]')]
    tts.narrate_segments(run, cfg, segs, run.root / "x.mp3", run.root / "work", stage="narrate")
    assert fake_tts[0]["text"] == 'Plato writes, "The lover only wanted to look … and to be with them."'


def test_other_bracket_tags_stop_narration(run, cfg, fake_tts, joined):
    with pytest.raises(StageError, match="read aloud"):
        tts.narrate_segments(run, cfg, [(1, "Hold it. [long pause] Go on.")], run.root / "x.mp3",
                             run.root / "work", stage="narrate")
    assert fake_tts == []  # nothing paid for


def _connection_error(*a):
    import anthropic
    import httpx2
    raise anthropic.APIConnectionError(request=httpx2.Request("GET", "https://api.anthropic.com"))


def test_network_drop_on_resume_never_pays_twice(run, cfg, responder, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    batches = llm._client.batches
    batches.status_on_create = "in_progress"
    batches.retrieve_hook = lambda bid: (_ for _ in ()).throw(KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        llm.call(run, cfg, "map", messages=MAP)
    n_calls = len(responder.calls)

    # Wi-Fi not back yet: one dropped retrieve is ridden out, the same batch is resumed.
    drops = [1]
    batches.retrieve_hook = lambda bid: drops.pop() and _connection_error() if drops else None
    res = llm.call(run, cfg, "map", messages=MAP)
    assert res.text.startswith("## Episode") and len(batches.created) == 1 and len(responder.calls) == n_calls

    # Down for good, and the batch can't be cancelled: stop, keep the id, stream nothing.
    batches.retrieve_hook = lambda bid: (_ for _ in ()).throw(KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        llm.call(run, cfg, "map", messages=MAP)
    n_calls = len(responder.calls)
    batches.retrieve_hook = _connection_error
    monkeypatch.setattr(batches, "cancel", _connection_error)
    cfg["batch"]["timeout_minutes"] = 0  # give up on the first drop
    with pytest.raises(StageError, match="Re-run the stage to resume it"):
        llm.call(run, cfg, "map", messages=MAP)
    assert len(responder.calls) == n_calls and len(list(run.root.glob("_batch_*.json"))) == 1


def test_quote_check_tolerates_editorial_brackets():
    from stages.coverage import unbacked_quotes
    dossier = ("## Entries\n\n[D01] (L01, quote, tier 2, verbatim: yes) Scarry: \"[T]he beautiful brings copies "
               "of itself into being [sic] at once.\" Source: https://example.org/scarry\n")
    script = '<!-- seg01 -->\n\nScarry says, "the beautiful brings copies of itself into being at once." [D01]\n'
    assert unbacked_quotes(script, dossier) == []
    assert research.quote_found('"[T]he beautiful brings copies"', ["the beautiful brings copies of itself"])
