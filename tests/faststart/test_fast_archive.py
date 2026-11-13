"""The archive (faststart/archive.py), acceptance check 7: the stitched length equals the sum of the
sections; merged timings never go backwards; the episode build runs on the fast run folder and the
episode page loads it; asides (ask and check_in) appear as text in the right places.

Offline: a fake voice with real audio and ElevenLabs-shaped timings, real ffmpeg, the references
model call replaced, the episode page in headless Chromium from a plain static server."""

import functools
import json
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fast_narrate_fakes import SECTIONS, TITLES, FakeVoice, lay_out, narrate_all

from ask import aside as ask_aside
from episode import references, script, timing
from faststart import archive, base, narrate
from faststart import outline as outline_mod
from stages import brief, tts
from stages.common import StageError, split_script_segments

PAGE = Path(__file__).resolve().parents[2] / "episode" / "page"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No live client and no Whisper. The brief is rendered by area A's own render_brief."""
    import anthropic
    import elevenlabs.client

    from episode import recognize

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")
    monkeypatch.setattr(anthropic, "Anthropic", live)
    monkeypatch.setattr(elevenlabs.client, "ElevenLabs", live)
    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: pytest.fail("Whisper was loaded"))


@pytest.fixture
def fast_run(run, cfg):
    """A fast run with its three sections locked and narrated: where the archive step starts."""
    lay_out(run)
    narrate_all(run, cfg)
    return run


@pytest.fixture
def cards(monkeypatch):
    """The references model call, replaced: one source card on each section's first sentence."""
    asked = []

    def ask(r, cfg, seg, prompt):
        asked.append(seg.id)
        first = seg.sentences[0]
        return {"sentences": [{"id": first.id, "sources": [{"dossier": first.dossier[0], "title": f"Source for {seg.id}",
                                                            "creator": "", "note": ""}],
                               "works": [], "people": [], "quotes": []}]}
    monkeypatch.setattr(references, "ask", ask)
    return asked


def words_of(t):
    return [w for s in t["segments"] for x in s["sentences"] for w in x["words"]]


# --- merge_timings, on its own --------------------------------------------------------------------

def test_merge_timings_moves_each_section_by_its_offset():
    def section(n, words, duration):
        ws = [{"w": w, "start": a, "end": b} for w, a, b in words]
        sent = {"id": f"seg{n:02d}-s001", "start": ws[0]["start"], "end": ws[-1]["end"], "text": "x", "words": ws}
        return {"audio": f"10_audio/sec{n:02d}/section.mp3", "duration": duration, "aligned_by": "elevenlabs", "pieces": 2,
                "stats": {"words": len(ws), "exact": len(ws), "fuzzy": 0, "estimated": 0, "gaps_reheard": 0},
                "segments": [{"id": f"seg{n:02d}", "start": ws[0]["start"], "end": ws[-1]["end"], "sentences": [sent]}]}

    one = section(1, [("A", 0.0, 0.4), ("b.", 0.5, 0.9)], 1.0)
    two = section(2, [("C", 1.6, 2.0), ("d.", 2.1, 2.5)], 3.0)
    merged = archive.merge_timings([one, two], [0.0, 1.0])
    assert merged["audio"] == "10_audio/chapter.mp3" and merged["duration"] == 4.0
    assert merged["aligned_by"] == "elevenlabs" and merged["pieces"] == 4 and merged["stats"]["words"] == 4
    assert [(w["w"], w["start"], w["end"]) for w in words_of(merged)] == [
        ("A", 0.0, 0.4), ("b.", 0.5, 0.9), ("C", 2.6, 3.0), ("d.", 3.1, 3.5)]
    seg2 = merged["segments"][1]
    assert (seg2["id"], seg2["start"], seg2["end"]) == ("seg02", 2.6, 3.5)  # the segment and its sentences move too
    assert seg2["sentences"][0]["id"] == "seg02-s001" and seg2["sentences"][0]["start"] == 2.6
    assert one["segments"][0]["start"] == 0.0 and two["segments"][0]["start"] == 1.6  # the inputs are untouched
    assert archive.timing_problems(merged, "<!-- seg01 -->\n\nA b.\n\n<!-- seg02 -->\n\nC d.\n", {}) == []
    # the checks: a different script, or a section placed too early
    assert archive.timing_problems(merged, "<!-- seg01 -->\n\nA b. More.\n", {}) == ["its sentences are not the script's"]
    early = archive.merge_timings([one, two], [0.0, -1.0])
    assert "its times go backwards" in archive.timing_problems(early, "<!-- seg01 -->\n\nA b.\n\n<!-- seg02 -->\n\nC d.\n", {})


# --- acceptance check 7 ---------------------------------------------------------------------------

def test_stitched_length_equals_the_sum_of_the_sections(fast_run, cfg):
    r = fast_run
    res = archive.run(r, cfg, build_episode=False)
    chapter = timing.audio_duration(r.path("chapter_mp3"))
    section_files = [timing.audio_duration(base.section_dir(r, n) / "section.mp3") for n in (1, 2, 3)]
    piece_seconds = [sum(p.seconds for p in narrate.pieces(r, n)) for n in (1, 2, 3)]
    assert abs(chapter - sum(section_files)) < 0.25      # the stitched file against the section files
    assert abs(chapter - sum(piece_seconds)) < 0.1       # and against every piece, pauses included
    assert res["seconds"] == pytest.approx(chapter, abs=0.001) and res["episode"] is False
    assert [s["n"] for s in res["sections"]] == [1, 2, 3]
    assert [s["seconds"] for s in res["sections"]] == [pytest.approx(x, abs=0.002) for x in piece_seconds]
    assert [s["offset"] for s in res["sections"]] == [pytest.approx(sum(piece_seconds[:k]), abs=0.002) for k in range(3)]
    # the join is the pieces the listener heard, in order, with the same pauses: nothing was narrated again
    listed = [Path(line[6:-1]).name for line in (r.path("audio_dir") / "concat.txt").read_text().splitlines()]
    want = []
    for n in (1, 2, 3):
        for p in narrate.pieces(r, n):
            want += ([f"silence_{p.pause_ms}ms_24000_2_1.wav"] if p.pause_ms else []) + [p.wav.name]
    assert listed == want
    assert not (r.path("audio_dir") / "joined.wav").exists()
    # the script is the locked sections under their markers
    assert split_script_segments(r.read("script_final")) == [(n, SECTIONS[n]) for n in (1, 2, 3)]
    assert res["words"] == sum(len(x.words) for s in script.parse(r.read("script_final")) for x in s.sentences)


def test_merged_timings_never_go_backwards_and_match_the_stitched_audio(fast_run, cfg):
    r = fast_run
    res = archive.run(r, cfg, build_episode=False)
    t = json.loads((r.root / "11_episode" / "timing.json").read_text())
    assert res["timing"] == "merged" and t["audio"] == "10_audio/chapter.mp3" and t["aligned_by"] == "elevenlabs"
    assert t["duration"] == pytest.approx(res["seconds"], abs=0.001)
    ws = words_of(t)
    assert all(b["start"] >= a["end"] for a, b in zip(ws, ws[1:]))  # never backwards
    assert all(w["end"] > w["start"] for w in ws) and ws[-1]["end"] <= t["duration"]
    assert t["stats"]["estimated"] == 0 and t["stats"]["words"] == len(ws) == res["words"]
    # every sentence of the script, once, in order
    parsed = script.parse(r.read("script_final"), cfg["narration"]["pronunciations"])
    assert [x["id"] for s in t["segments"] for x in s["sentences"]] == [x.id for s in parsed for x in s.sentences]
    # each section starts after its offset and its opening pause, and ends before the next begins
    for seg, sec in zip(t["segments"], res["sections"]):
        pause = narrate.pieces(r, sec["n"])[0].pause_ms / 1000
        assert seg["start"] == pytest.approx(sec["offset"] + pause, abs=0.03)
        assert seg["end"] <= sec["offset"] + sec["seconds"] + 0.03
    # merging the section timings gives what timing the stitched chapter itself gives
    whole = timing.build(r.root, timing.pick_timings(r.root, "auto", lambda: pytest.fail("no recognizer")),
                         cfg["narration"]["pronunciations"])
    assert [w["w"] for w in words_of(whole)] == [w["w"] for w in ws]
    assert max(abs(a["start"] - b["start"]) + abs(a["end"] - b["end"]) for a, b in zip(ws, words_of(whole))) < 0.01


def test_episode_build_runs_on_the_fast_run_and_the_brief_names_what_was_cited(fast_run, cfg, cards):
    r = fast_run
    res = archive.run(r, cfg)
    assert res["episode"] is True and cards == ["seg01", "seg02", "seg03"]
    ep = json.loads((r.root / "11_episode" / "episode.json").read_text())
    assert ep["title"] == "The Wall That Heals" and ep["angle"] == "The Wall That Heals"
    assert ep["audio"] == "10_audio/chapter.mp3" and ep["duration"] == res["seconds"]
    assert [s["id"] for s in ep["segments"]] == ["seg01", "seg02", "seg03"]
    refs = ep["references"]
    assert [x["sentence_id"] for x in refs] == ["seg01-s001", "seg02-s001", "seg03-s001"]
    assert [x["dossier_ref"] for x in refs] == ["D01", "D05", "D08"] and refs[1]["url"] == "https://example.org/5"
    assert refs[2]["start"] == ep["segments"][2]["sentences"][0]["start"] > res["sections"][2]["offset"]
    # the brief, in the overnight shape, lists the entries each locked section really cites
    segs = brief.parse_segments(r.read("brief"))
    assert [s["title"] for s in segs] == [TITLES[n] for n in (1, 2, 3)]
    assert [s["entries"] for s in segs] == [[1, 2, 3, 4], [5, 6, 7], [1, 8, 9]]
    assert [s["rows"] for s in segs] == [[], ["L01"], ["L02"]]
    # ask-by-voice reads the archived run as it reads an overnight one
    ch = ask_aside.Chapter(r.root, cfg)
    assert ch.title == "The Wall That Heals" and len(ch.segments) == 3 and ch.seg_title(1) == TITLES[2]


def test_a_second_archive_pays_for_nothing_and_changes_nothing(fast_run, cfg, cards, monkeypatch):
    r = fast_run
    first = archive.run(r, cfg, build_episode=False)
    assert first["episode"] is False and not (r.root / "11_episode" / "episode.json").exists() and cards == []
    stamp = r.path("chapter_mp3").stat().st_mtime_ns
    monkeypatch.setattr(tts, "join_to_mp3", lambda *a, **k: pytest.fail("the chapter was stitched again"))
    done = archive.run(r, cfg)  # the episode build alone
    assert done["episode"] is True and len(cards) == 3 and done["sections"] == first["sections"]
    again = archive.run(r, cfg)
    assert again == done and len(cards) == 3 and r.path("chapter_mp3").stat().st_mtime_ns == stamp
    assert json.loads((base.path(r, "work") / "archive.json").read_text()) == done


def test_the_references_cost_reaches_the_runs_own_log(fast_run, cfg, monkeypatch):
    r = fast_run

    def ask(er, c, seg, prompt):
        er.log({"stage": "episode_references", "label": seg.id, "cost_usd": 0.05})  # as llm.call logs a paid call
        return {"sentences": []}
    monkeypatch.setattr(references, "ask", ask)
    before = r.spent()
    res = archive.run(r, cfg)
    assert res["episode_cost_usd"] == pytest.approx(0.15) and r.spent() - before == pytest.approx(0.15)
    assert [rec["cost_usd"] for rec in r.read_log() if rec["stage"] == "fast_archive" and rec.get("cost_usd")] == [0.15]


def test_a_failed_episode_build_is_a_stage_error_and_leaves_a_chapter_that_plays(fast_run, cfg, monkeypatch):
    r = fast_run
    (r.root / "04_dossier.md").unlink()  # references.build would die with FileNotFoundError
    with pytest.raises(StageError, match="episode build failed"):
        archive.run(r, cfg)
    assert r.path("chapter_mp3").exists() and r.path("script_final").exists()
    assert (r.root / "11_episode" / "timing.json").exists() and not (r.root / "11_episode" / "episode.json").exists()
    monkeypatch.setattr(references, "build", lambda *a, **k: (_ for _ in ()).throw(SystemExit("No finished run")))
    with pytest.raises(StageError, match="episode build failed"):
        archive.run(r, cfg)


def test_the_archive_needs_every_section(run, cfg):
    lay_out(run)
    narrate_all(run, cfg, only=[1, 2])
    with pytest.raises(StageError, match="Section 3 is not locked and narrated"):
        archive.run(run, cfg, build_episode=False)
    assert not run.path("chapter_mp3").exists()
    (base.section_dir(run, 1) / "p01.wav").unlink()  # a narrated section that lost a piece is not whole either
    with pytest.raises(StageError, match="Section 1 is not locked and narrated in full"):
        archive.run(run, cfg, build_episode=False)


def test_a_chapter_that_does_not_add_up_is_refused(fast_run, cfg):
    meta = base.section_dir(fast_run, 2) / "p00.json"
    m = json.loads(meta.read_text())
    meta.write_text(json.dumps({**m, "seconds": m["seconds"] + 5}))
    with pytest.raises(StageError, match="add up"):
        archive.run(fast_run, cfg, build_episode=False)


def test_a_missing_section_timing_is_rebuilt_from_the_pieces_not_by_ear(fast_run, cfg):
    r = fast_run
    (base.path(r, "section_timings") / "seg02.json").unlink()
    res = archive.run(r, cfg, build_episode=False)  # Whisper would fail this test (see `offline`)
    t = json.loads((r.root / "11_episode" / "timing.json").read_text())
    assert res["timing"] == "rebuilt" and t["aligned_by"] == "elevenlabs" and t["stats"]["estimated"] == 0
    ws = words_of(t)
    assert all(b["start"] >= a["end"] for a, b in zip(ws, ws[1:])) and len(t["segments"]) == 3


def test_a_voice_without_timings_is_timed_by_ear_at_the_archive(run, cfg, monkeypatch):
    from episode import recognize

    lay_out(run)
    narrate_all(run, cfg, voice=FakeVoice(alignments=False))
    assert all(narrate.section_timing(run, n) is None for n in (1, 2, 3))

    class Ear:
        """Stands in for Whisper: hears each piece's own text, evenly."""
        name = "fake ear"
        reheard = False

        def __call__(self, wav):
            words = json.loads(wav.with_suffix(".json").read_text())["text"].split()
            step = timing.audio_duration(wav) / len(words)
            return [{"w": w, "start": i * step, "end": (i + 0.8) * step} for i, w in enumerate(words)]
    monkeypatch.setattr(recognize, "WhisperRecognizer", Ear)
    res = archive.run(run, cfg, build_episode=False)
    t = json.loads((run.root / "11_episode" / "timing.json").read_text())
    assert res["timing"] == "rebuilt" and t["aligned_by"] == "fake ear" and t["pieces"] == sum(
        len(narrate.pieces(run, n)) for n in (1, 2, 3))
    ws = words_of(t)
    assert all(b["start"] >= a["end"] for a, b in zip(ws, ws[1:])) and t["stats"]["estimated"] == 0


def test_rolling_gives_ask_by_voice_the_chapter_so_far(run, cfg):
    lay_out(run)
    assert archive.rolling(run, cfg) is None
    narrator, _ = narrate_all(run, cfg, only=[1, 2])
    t = archive.rolling(run, cfg)
    assert [s["id"] for s in t["segments"]] == ["seg01", "seg02"]
    assert [n for n, _ in split_script_segments(run.read("script_final"))] == [1, 2]
    assert json.loads((run.root / "11_episode" / "timing.json").read_text()) == t
    ch = ask_aside.Chapter(run.root, cfg)  # ask-by-voice opens the partial chapter
    assert len(ch.segments) == 2 and ch.sents[-1].id.startswith("seg02-")
    # the last section lands; the archive's timing agrees with what the listener was shown on the way
    narrator.section(3, SECTIONS[3])
    archive.run(run, cfg, build_episode=False)
    final = json.loads((run.root / "11_episode" / "timing.json").read_text())
    assert final["segments"][:2] == t["segments"] and len(final["segments"]) == 3
    stamp = (run.root / "11_episode" / "timing.json").stat().st_mtime_ns
    assert archive.rolling(run, cfg) == final  # after the archive it only reads
    assert (run.root / "11_episode" / "timing.json").stat().st_mtime_ns == stamp


# --- the episode page loads the run; asides are text in the right places ---------------------------

@pytest.fixture
def served(fast_run, cfg, cards, tmp_path):
    """The archived run with two conversations in its aside log, behind a plain static server."""
    r = fast_run
    res = archive.run(r, cfg)
    asked_at = "seg01-s004"  # a question, mid-section
    ask_aside.append_aside(r.root, {"id": "a1", "kind": "ask", "t": 9.0, "sentence_id": asked_at, "ended": "closing",
                                    "transcript": [{"who": "guide", "text": "Yes?"},
                                                   {"who": "listener", "text": "Where is Caesarea?"},
                                                   {"who": "tool", "name": "resume_chapter"},
                                                   {"who": "guide", "text": "On the coast of Israel."}]})
    # a check-in while section 2 was still being made: it sits at the top of section 2
    ask_aside.append_aside(r.root, {"id": "c1", "kind": "check_in", "t": res["sections"][1]["offset"],
                                    "sentence_id": "seg02-s001", "ended": "resumed",
                                    "transcript": [{"who": "guide", "text": "What do you make of it so far?"},
                                                   {"who": "listener", "text": "The wall getting stronger surprised me."},
                                                   {"who": "page", "text": "[The next part is ready.]"},
                                                   {"who": "guide", "text": "Then you will like the recipe."}]})
    asides = (r.root / "12_asks" / "asides.jsonl").read_bytes()
    assert archive.run(r, cfg) == res and (r.root / "12_asks" / "asides.jsonl").read_bytes() == asides  # never touched
    assert timing.audio_duration(r.path("chapter_mp3")) == pytest.approx(res["seconds"], abs=0.001)  # nor in the audio

    site = tmp_path / "site"
    shutil.copytree(PAGE, site / "page")
    shutil.copytree(r.root, site / "run")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", res
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_the_episode_page_loads_the_fast_run_with_asides_in_place(served):
    # Skipped without Playwright, as the other two page tests are (tests/episode/test_page_browser.py,
    # tests/test_ask_page.py): a run that reports acceptance check 7 must show no skip here (pytest -rs).
    sync_api = pytest.importorskip("playwright.sync_api", reason="acceptance check 7's page test needs Playwright "
                                   "(requirements-episode.txt): the episode page was NOT checked on a fast run")
    base_url, res = served
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        try:
            pg = browser.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base_url + "/page/index.html?run=/run")
            pg.wait_for_function("document.getElementById('audio').readyState >= 1 && document.querySelectorAll('.w').length > 0")
            # the run loads like any other: its title, one part per section, the stitched audio
            assert pg.inner_text("#title") == "The Wall That Heals"
            assert pg.locator("#transcript section").count() == 3
            assert abs(pg.evaluate("document.getElementById('audio').duration") - res["seconds"]) < 0.1
            ep = pg.evaluate("fetch('/run/11_episode/episode.json').then(r => r.json())")
            assert pg.locator(".w").count() == sum(len(x["words"]) for s in ep["segments"] for x in s["sentences"])

            # asides are text where they happened, collapsed to the first line
            pg.wait_for_function("document.querySelectorAll('details.aside').length === 2")
            where = pg.evaluate("""[...document.querySelectorAll('details.aside')].map((d) => ({
                next: d.nextElementSibling.id, first: d.previousElementSibling === null,
                part: [...document.querySelectorAll('#transcript section')].indexOf(d.closest('section')),
                summary: d.querySelector('summary').textContent }))""")
            assert where[0] == {"next": "seg01-s004", "first": False, "part": 0, "summary": "Yes?"}  # the question
            # the check-in names a section's first sentence: it opens that section
            assert where[1] == {"next": "seg02-s001", "first": True, "part": 1, "summary": "What do you make of it so far?"}
            assert not pg.is_visible("details.aside p")
            pg.locator("details.aside summary").nth(1).click()
            text = pg.locator("details.aside").nth(1).inner_text()
            assert "You: The wall getting stronger surprised me." in text and "Then you will like the recipe." in text
            assert "next part is ready" not in text  # the page's own notes are not conversation
            assert pg.locator("audio").count() == 1  # the conversations are text: there is no audio for them

            # the merged timings drive the page: a word in the last section seeks into the stitched audio
            pg.evaluate("document.getElementById('audio').muted = true")
            pg.locator("#seg03-s002 .w").nth(1).click()
            word = ep["segments"][2]["sentences"][1]["words"][1]
            assert word["start"] > res["sections"][2]["offset"]
            assert abs(pg.evaluate("document.getElementById('audio').currentTime") - word["start"]) < 0.35
            pg.wait_for_function("document.body.dataset.word && document.body.dataset.word.startsWith('seg03-s002')", timeout=4000)
            assert pg.locator("#cardlist .card").count() >= 1  # the cards spoken so far
            assert errors == []
        finally:
            browser.close()


# --- review fixes ---------------------------------------------------------------------------------

def test_the_brief_is_rendered_by_the_real_outline_module(fast_run, cfg):
    """The archive tests once ran against a stand-in for faststart.outline: a change to the real
    render_brief's line format would not have failed them."""
    import sys

    import faststart.outline
    assert sys.modules["faststart.outline"] is faststart.outline is outline_mod and outline_mod.__file__.endswith("outline.py")
    archive.run(fast_run, cfg, build_episode=False)
    text = fast_run.read("brief")
    assert "- Dossier: D01, D02, D03, D04" in text and "- Ledger: none" in text  # area A's own line format
    assert [s["entries"] for s in brief.parse_segments(text)] == [[1, 2, 3, 4], [5, 6, 7], [1, 8, 9]]


def test_the_archived_brief_has_the_titles_the_locks_gave(fast_run, cfg):
    """A lock may retitle its section (kept in fast/locks/segNN.json; the outline is never rewritten).
    The archive used to render the brief from the outline's titles, so ask-by-voice named the old one."""
    r = fast_run
    outline_before = (r.root / "05_outline.json").read_bytes()
    base.write_json(base.path(r, "locks") / "seg02.json", {"title": "Lime that looks like a mistake", "mode": "paired"})
    base.write_json(base.path(r, "locks") / "seg03.json", {"title": None, "mode": "final"})  # a lock that kept its title
    archive.run(r, cfg, build_episode=False)
    assert [s["title"] for s in brief.parse_segments(r.read("brief"))] == [
        TITLES[1], "Lime that looks like a mistake", TITLES[3]]
    assert ask_aside.Chapter(r.root, cfg).seg_title(1) == "Lime that looks like a mistake"
    assert (r.root / "05_outline.json").read_bytes() == outline_before


def test_titles_go_to_render_brief_whichever_signature_it_has(fast_run, cfg, monkeypatch):
    r = fast_run
    base.write_json(base.path(r, "locks") / "seg02.json", {"title": "Lime that looks like a mistake"})
    real, got = outline_mod.render_brief, {}

    def with_titles(outline, led, cited=None, titles=None):
        got["titles"] = titles
        return real(outline, led, cited)
    monkeypatch.setattr(outline_mod, "render_brief", with_titles)
    archive.run(r, cfg, build_episode=False)
    assert got["titles"] == {2: "Lime that looks like a mistake"}
    assert brief.parse_segments(r.read("brief"))[1]["title"] == "Lime that looks like a mistake"  # either way

    def old(outline, led, cited=None):
        return real(outline, led, cited)
    monkeypatch.setattr(outline_mod, "render_brief", old)
    (base.path(r, "work") / "archive.json").unlink()
    archive.run(r, cfg, build_episode=False)
    assert brief.parse_segments(r.read("brief"))[1]["title"] == "Lime that looks like a mistake"


def test_one_piece_without_timings_does_not_send_the_archive_to_whisper(run, cfg):
    """ElevenLabs may return a piece without character timings. That used to cost its section its
    timing, stop the rolling timing there, and send the whole chapter to Whisper at the archive."""
    lay_out(run)
    narrator = narrate.Narrator(run, cfg, synth=FakeVoice(bare=(5,)), sleep=lambda s: None)
    for n in (1, 2, 3):
        narrator.section(n, SECTIONS[n])
        t = archive.rolling(run, cfg)
        assert [s["id"] for s in t["segments"]] == [f"seg{k:02d}" for k in range(1, n + 1)]  # no section drops out
    bare = [p for n in (1, 2, 3) for p in narrate.pieces(run, n) if timing.chunk_alignment(p.wav) is None]
    assert len(bare) == 1 and bare[0].section == 2
    res = archive.run(run, cfg, build_episode=False)  # Whisper would fail this test (see `offline`)
    t = json.loads((run.root / "11_episode" / "timing.json").read_text())
    assert res["timing"] == "merged" and t["aligned_by"] == "elevenlabs" and len(t["segments"]) == 3
    assert t["stats"]["estimated"] == len(bare[0].text.split()) < t["stats"]["words"] == res["words"]
    ws = words_of(t)
    assert all(b["start"] >= a["end"] for a, b in zip(ws, ws[1:]))
    # and when the chapter is timed again from its pieces, the bare one is still no reason to load Whisper
    (base.path(run, "section_timings") / "seg03.json").unlink()
    (base.path(run, "work") / "archive.json").unlink()
    again = archive.run(run, cfg, build_episode=False)
    t = json.loads((run.root / "11_episode" / "timing.json").read_text())
    assert again["timing"] == "rebuilt" and t["aligned_by"] == "elevenlabs"
    assert t["stats"]["estimated"] == len(bare[0].text.split())


def test_an_archive_that_fails_leaves_no_chapter_file_behind(run, cfg, monkeypatch):
    """chapter.mp3 is how stages.seed.find_run tells a finished run: an archive that died after the
    stitch left one, and the same seed then started a second paid run instead of continuing this one."""
    from episode import recognize
    from stages import seed

    lay_out(run)
    (run.root / "00_source").mkdir(exist_ok=True)
    (run.root / "00_source" / "seed.md").write_text("Roman concrete\n", encoding="utf-8")
    narrate_all(run, cfg, voice=FakeVoice(alignments=False))  # no piece has timings: only Whisper could time it

    def no_whisper(*a, **k):
        raise ImportError("No module named 'faster_whisper'")
    monkeypatch.setattr(recognize, "WhisperRecognizer", no_whisper)
    with pytest.raises(StageError, match="Could not time the stitched chapter"):
        archive.run(run, cfg, build_episode=False)
    assert not run.path("chapter_mp3").exists() and not (base.path(run, "work") / "archive.json").exists()
    assert seed.find_run("Roman concrete\n", runs_dir=run.root.parent) == run.name  # still the run to continue
    assert all(narrate.narrated(run, n) for n in (1, 2, 3))  # what the listener heard is untouched
