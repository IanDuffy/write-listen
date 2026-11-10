"""Regression tests for defects found in the second review (deep research)."""

import base64
import io
from types import SimpleNamespace as NS

import pytest
from fakes import FakeAnthropic, Responder

from stages import ingest, llm, narrate, research
from stages.brief import parse_segments
from stages.common import StageError, load_prompt, parse_range
from stages.report import claim_sentences
from stages.research import URL_IN_TEXT, merge_claims, quote_check, retrieved, topic_paths


def write_topic(run, n, text):
    md, _ = topic_paths(run, n)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(text)


def test_conflict_citations_are_not_claims(run):
    write_topic(run, 3, "## Claims\n  [T3.1] One. Source: https://a.org\n[T3.2] Two. Source: https://a.org\n"
                        "[T3.3] Three. Source: https://a.org\n\n## Conflicts\n- [T3.2] and [T3.3] disagree; "
                        "T3.2 is better sourced.\n")
    claims, mapping, _ = merge_claims(run, [{"id": 3, "title": "X"}])
    assert mapping == {"T3.1": 1, "T3.2": 2, "T3.3": 3}
    assert "[C1] One." in claims  # indented entry keeps its first characters
    assert "[C2] and [C3] disagree; C2 is better sourced." in claims
    assert "[C4]" not in claims


def test_quote_pairing_and_episode_quotes():
    page = [{"url": "https://a.org", "text": "Beauty is the one thing that pulls us out of ourselves, said she."}]
    d = ('## Claims\n[C1] Weil wrote "Beauty is the one thing that pulls us out of ourselves," in "Notebooks". '
         'Source: https://a.org\n'
         '[C2] "I think beauty makes us want to copy it, always and everywhere." Source: episode\n')
    ok, missing, unchecked = quote_check(d, page)
    assert ok == ["C1"] and missing == [] and unchecked == []


def test_urls_keep_balanced_parentheses():
    text = "Source: https://en.wikipedia.org/wiki/Symposium_(Plato) and [link](https://a.org/x)"
    assert URL_IN_TEXT.findall(text) == ["https://en.wikipedia.org/wiki/Symposium_(Plato)", "https://a.org/x"]


def test_fetched_pdf_text_is_kept_for_quote_checks(tmp_path):
    import sys
    sys.path.insert(0, str(tmp_path))
    from fakes import make_pdf
    pdf = tmp_path / "p.pdf"
    make_pdf(pdf, [["Beauty prompts a desire to replicate itself."]])
    b64 = base64.b64encode(pdf.read_bytes()).decode()
    block = NS(type="web_fetch_tool_result", content=NS(
        type="web_fetch_result", url="https://u.edu/paper.pdf",
        content=NS(type="document", title="Paper", source=NS(type="base64", data=b64))))
    got = retrieved([block])
    assert "desire to replicate" in got["fetched"][0]["text"]


def _setup(run, monkeypatch, responder=None):
    run.yes = True
    run.path("angle").write_text("a")
    run.path("map").write_text("m")
    resp = responder or Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    return resp


def test_rerun_resumes_rounds_and_topics_redo_skips_critic(run, cfg, monkeypatch):
    resp = _setup(run, monkeypatch)
    research.run(run, cfg)
    assert resp.critic_calls == 2
    run.force = True
    research.run(run, cfg, topics="T2")
    assert resp.critic_calls == 2  # an explicit redo runs no critic rounds
    research.run(run, cfg)
    assert resp.critic_calls == 2  # the critic already declared the research complete


def test_spend_cap_stops_agents(run, cfg, monkeypatch):
    _setup(run, monkeypatch)
    cfg["research"]["max_usd"] = 0.0001
    with pytest.raises(StageError, match="max_usd"):
        research.run(run, cfg)


def test_changed_angle_needs_replan(run, cfg, monkeypatch):
    _setup(run, monkeypatch)
    research.run(run, cfg, plan_only=True)
    run.path("angle").write_text("a different angle")
    with pytest.raises(StageError, match="--replan"):
        research.run(run, cfg, plan_only=True)
    research.run(run, cfg, plan_only=True, replan=True)
    assert list(run.root.glob("04_research.old-*"))


def test_dossier_rebuild_does_not_trip_the_guard(run, cfg, monkeypatch):
    _setup(run, monkeypatch)
    research.run(run, cfg)
    run.yes, run.force = False, True
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail("should not ask"))
    research.run(run, cfg)


def test_retry_on_dropped_stream(run, cfg, monkeypatch):
    import httpx2
    monkeypatch.setattr(llm, "RETRY_DELAYS", (0, 0))
    resp = Responder()
    calls = {"n": 0}

    def flaky(kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx2.RemoteProtocolError("peer closed connection")
        return resp(kwargs)

    monkeypatch.setattr(llm, "_client", FakeAnthropic(flaky))
    res = llm.call(run, cfg, "ping", role="cheap", messages=[{"role": "user", "content": "single word: ready"}])
    assert res.text == "ready" and calls["n"] == 2


def test_echo_after_fallback_drops_pre_fallback_internals():
    content = [NS(type="thinking"), NS(type="server_tool_use", id="a"), NS(type="text", text="x"),
               NS(type="fallback"), NS(type="thinking"), NS(type="server_tool_use", id="b")]
    kept = llm.echo_after_fallback(content)
    assert [b.type for b in kept] == ["text", "fallback", "thinking", "server_tool_use"]


def test_style_guide_notes_are_not_sent():
    sg = load_prompt("style_guide")
    assert "Ian" not in sg and sg.lstrip().startswith("- Written for the ear")


def test_ingest_keeps_lowercase_words_and_compounds():
    pages = ["Scarry: You have to\nlisten.\nThe well-\nknown passage. Dio-\ntima, Diotima again."]
    text, removed = ingest.clean(pages)
    assert "have to listen." in text and "well-known" in text and "Diotima, Diotima" in text


def test_brief_claims_as_sublist():
    segs = parse_segments("## Segment 1: A\n- Claims:\n  - C3\n  - C7\n- Target words: 700\n")
    assert segs[0]["claims"] == [3, 7] and segs[0]["target_words"] == 700


def test_report_samples_whole_sentences_across_abbreviations():
    s = claim_sentences("In 1940, St. Augustine's old city fell. [C3] She was 3.5 feet tall. [C9]")
    assert s[0] == ("In 1940, St. Augustine's old city fell.", [3])
    assert s[1] == ("She was 3.5 feet tall.", [9])


def test_parse_range_errors():
    assert parse_range("seg1-2", [1, 2, 3]) == [1, 2]
    for bad in ("one", "3-1"):
        with pytest.raises(StageError):
            parse_range(bad, [1, 2, 3])


def test_partial_narration_never_becomes_the_chapter(run, cfg, fake_tts):
    run.path("script_final").write_text("<!-- seg01 -->\n\nOne.\n\n<!-- seg02 -->\n\nTwo.\n")
    narrate.run(run, cfg, segments="2")
    assert not run.path("chapter_mp3").exists()
    assert (run.path("audio_dir") / "segments_2.mp3").exists()
