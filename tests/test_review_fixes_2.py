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
from stages.research import URL_IN_TEXT, _norm_text, quote_found, retrieved


def test_quote_pairing_ignores_short_titles():
    page = [_norm_text("Beauty is the one thing that pulls us out of ourselves, said she.")]
    body = 'Weil wrote "Beauty is the one thing that pulls us out of ourselves," in "Notebooks". Source: https://a.org'
    assert quote_found(body, page) is True
    assert quote_found('A claim with no quote, "Notebooks". Source: https://a.org', page) is None


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


def _setup(run, cfg, monkeypatch, responder=None):
    from stages import ledger
    run.yes = True
    run.path("angle").write_text("a")
    run.path("map").write_text("m")
    run.path("source_dir").mkdir(exist_ok=True)
    run.path("transcript").write_text("t")
    resp = responder or Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    ledger.run(run, cfg)
    return resp


def test_spend_cap_stops_agents(run, cfg, monkeypatch):
    _setup(run, cfg, monkeypatch)
    cfg["research"]["max_usd"] = 0.0001
    with pytest.raises(StageError, match="max_usd"):
        research.run(run, cfg)


def test_dossier_rebuild_does_not_trip_the_guard(run, cfg, monkeypatch):
    _setup(run, cfg, monkeypatch)
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


def test_report_samples_whole_sentences_across_abbreviations():
    s = claim_sentences("In 1940, St. Augustine's old city fell. [D03] She was 3.5 feet tall. [D9]")
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


def test_smoketest_offline(run, cfg, monkeypatch, fake_tts):
    from fakes import PAGE_TEXT, text_block, usage

    from stages import smoketest, tts

    def respond(kwargs):
        if "format" in kwargs.get("output_config", {}):  # the structured-output check
            assert kwargs["output_config"]["format"]["type"] == "json_schema"
            return NS(content=[text_block('{"word": "ready", "count": 3}')], stop_reason="end_turn",
                      usage=usage(), model=kwargs["model"], stop_details=None)
        return NS(content=[
            NS(type="server_tool_use", id="s1", name="web_search", input={"query": "q"}),
            NS(type="web_search_tool_result", tool_use_id="s1",
               content=[NS(type="web_search_result", url=smoketest.URL, title="Plato")]),
            NS(type="web_fetch_tool_result", tool_use_id="f1", content=NS(
                type="web_fetch_result", url=smoketest.URL,
                content=NS(type="document", title="Plato", source=NS(type="text", data=PAGE_TEXT)))),
            text_block('"He who has been instructed thus far in the things of love"')],
            stop_reason="end_turn", usage=usage(searches=1, fetches=1), model=kwargs["model"],
            stop_details=None)

    monkeypatch.setattr(llm, "_client", FakeAnthropic(respond))
    monkeypatch.setattr(tts, "model_char_limit", lambda m: 10000)
    smoketest.run(run, cfg)
    assert (run.root / "smoketest.wav").exists()
