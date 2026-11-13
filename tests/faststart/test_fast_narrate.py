"""Narration in pieces (faststart/narrate.py) and the timestamps option it needs in stages/tts.py.
Offline: a stub ElevenLabs client for the tts option, a fake voice for the rest, real ffmpeg."""

import base64
import contextlib
import json
import types

import pytest

from fast_narrate_fakes import SECTIONS, FakeVoice, alignment, narrate_all

from episode import timing
from faststart import base, narrate
from stages import tts
from stages.common import StageError


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test here may build a live client (a real .env is present)."""
    import anthropic
    import elevenlabs.client

    def live(*a, **k):
        raise AssertionError("a test reached a live API client")
    monkeypatch.setattr(anthropic, "Anthropic", live)
    monkeypatch.setattr(elevenlabs.client, "ElevenLabs", live)


@pytest.fixture
def small(cfg):
    """Piece sizes small enough to see the grouping on a few short paragraphs."""
    cfg["fast"]["narration"].update(first_piece_max_chars=60, piece_min_chars=50, piece_max_chars=120)
    return cfg


# --- stages/tts.py: the timestamps option -------------------------------------------------------------

class StubClient:
    """The two calls synthesize() makes on the ElevenLabs SDK, recorded. The timestamps reply is parsed
    by the SDK's own model from the JSON the API documents, so the field names are the installed SDK's."""

    def __init__(self, audio=b"\x01\x00" * 100, with_alignment=True):
        self.plain, self.timed = [], []
        self.audio, self.with_alignment = audio, with_alignment
        self.text_to_speech = types.SimpleNamespace(with_raw_response=types.SimpleNamespace(
            convert=self.convert, convert_with_timestamps=self.convert_with_timestamps))

    @contextlib.contextmanager
    def convert(self, voice_id, **kw):
        self.plain.append({"voice_id": voice_id, **kw})
        yield types.SimpleNamespace(data=iter([self.audio[:50], self.audio[50:]]), headers={"request-id": "req_plain"})

    def convert_with_timestamps(self, voice_id, **kw):
        import httpx
        from elevenlabs.core.http_response import HttpResponse
        from elevenlabs.core.unchecked_base_model import construct_type
        from elevenlabs.types import AudioWithTimestampsResponse

        self.timed.append({"voice_id": voice_id, **kw})
        body = {"audio_base64": base64.b64encode(self.audio).decode(),
                "alignment": alignment(kw["text"], 0.5) if self.with_alignment else None,
                "normalized_alignment": None}
        raw = httpx.Response(200, headers={"Request-Id": "req_timed"}, json=body)
        return HttpResponse(response=raw, data=construct_type(type_=AudioWithTimestampsResponse, object_=body))


@pytest.fixture
def stub(monkeypatch):
    client = StubClient()
    monkeypatch.setattr(tts, "_el_client", client)
    return client


def test_synthesize_with_timestamps_false_is_the_plain_call(stub, cfg):
    # Since e322052 (feature/ask-voice) narration asks for timings by default (narration.timestamps);
    # timestamps=False still forces the plain endpoint, with every field as before.
    ncfg = cfg["narration"]
    syn = tts.synthesize("Hello there.", voice_id="v1", ncfg=ncfg, previous_request_ids=["a", "b", "c", "d"],
                         previous_text="ignored when ids are given", next_text="Next.", timestamps=False)
    assert stub.timed == [] and len(stub.plain) == 1  # the plain endpoint
    sent = stub.plain[0]
    assert sent["voice_id"] == "v1" and sent["text"] == "Hello there." and sent["model_id"] == ncfg["model_id"]
    assert sent["output_format"] == ncfg["output_format"] and sent["seed"] == ncfg["seed"]
    assert sent["previous_request_ids"] == ["b", "c", "d"] and "previous_text" not in sent and sent["next_text"] == "Next."
    assert syn.audio == stub.audio and syn.request_id == "req_plain" and syn.alignment is None
    assert tts.Synth(b"x", "r").alignment is None  # the new field has a default: old callers still construct it


def test_synthesize_follows_narration_timestamps_unless_told(stub, cfg):
    ncfg = dict(cfg["narration"], timestamps=False)
    tts.synthesize("Hi.", voice_id="v1", ncfg=ncfg)
    assert len(stub.plain) == 1 and stub.timed == []          # the config says no
    tts.synthesize("Hi.", voice_id="v1", ncfg=ncfg, timestamps=True)
    assert len(stub.timed) == 1                                # Fast Start's pieces ask regardless
    tts.synthesize("Hi.", voice_id="v1", ncfg=dict(ncfg, timestamps=True))
    assert len(stub.timed) == 2                                # the default since e322052


def test_synthesize_with_timestamps_returns_the_alignment(stub, cfg):
    syn = tts.synthesize("Hi there.", voice_id="v1", ncfg=cfg["narration"], previous_text="Before.", timestamps=True)
    assert stub.plain == [] and len(stub.timed) == 1  # the with-timestamps endpoint
    sent = stub.timed[0]
    assert sent["text"] == "Hi there." and sent["previous_text"] == "Before." and "previous_request_ids" not in sent
    assert sent["model_id"] == cfg["narration"]["model_id"] and sent["output_format"] == cfg["narration"]["output_format"]
    assert syn.audio == stub.audio and syn.request_id == "req_timed"
    assert set(syn.alignment) == {"characters", "character_start_times_seconds", "character_end_times_seconds"} and syn.alignment["characters"] == list("Hi there.")
    json.dumps(syn.alignment)  # plain lists and floats: it goes straight into the piece's .json
    assert [w["w"] for w in timing.alignment_words(syn.alignment)] == ["Hi", "there."]  # the reader's shape


def test_synthesize_with_timestamps_but_no_alignment_or_no_audio(monkeypatch, cfg):
    monkeypatch.setattr(tts, "_el_client", StubClient(with_alignment=False))
    assert tts.synthesize("Hi.", voice_id="v1", ncfg=cfg["narration"], timestamps=True).alignment is None
    monkeypatch.setattr(tts, "_el_client", StubClient(audio=b""))
    with pytest.raises(StageError, match="no audio"):
        tts.synthesize("Hi.", voice_id="v1", ncfg=cfg["narration"], timestamps=True)


# --- the plan: pieces and their pauses ----------------------------------------------------------------

A, B, C, D = ("Alpha one is short.", "Bravo two is a short paragraph as well.",
              "Charlie three is the longest of these paragraphs, long enough to stand as a piece on its own.",
              "Delta four closes it.")


def test_first_piece_is_one_short_paragraph_then_paragraphs_are_grouped(small):
    text = f"{A} [D01]\n\n{B}\n\n{A}\n\n{C} [D02, D03]\n\n{D}"
    ncfg = small["narration"]
    got = narrate.plan(small, 1, text)
    # the chapter's first piece is the first paragraph alone, with no pause before it; markers are gone
    assert got[0] == (A, 0)
    # the pieces after a one-line opening stay small (19 characters play for about a second: B with A, or A with
    # C, would not be made in the time the audio so far takes to play), with the chunk pause between pieces
    assert got[1:] == [(t, ncfg["chunk_pause_ms"]) for t in (B, A, C, D)]
    assert all(len(t) <= 120 for t, _ in got)
    # with more audio ahead, short paragraphs go together until a piece reaches piece_min_chars
    assert [t for t, _ in narrate.plan(small, 1, f"{C}\n\n{A}\n\n{B}\n\n{D}\n\n{A}")] == [C, f"{A}\n\n{B}", f"{D}\n\n{A}"]
    # a later section has no short first piece, and starts with the section pause
    later = narrate.plan(small, 2, text)
    assert later[0] == (f"{A}\n\n{B}", ncfg["segment_pause_ms"]) and [p for _, p in later[1:]] == [ncfg["chunk_pause_ms"]] * 2


def test_a_long_first_paragraph_gives_up_its_first_sentences_and_long_ones_split_at_sentences(small):
    long_para = " ".join(f"Sentence number {i} of the opening runs on a little." for i in range(1, 9))
    got = [t for t, _ in narrate.plan(small, 1, long_para)]
    assert got[0] == "Sentence number 1 of the opening runs on a little."  # whole sentences, at most 60 characters
    assert all(len(t) <= 120 for t in got) and " ".join(got) == long_para  # nothing lost, nothing over the limit
    # a sentence nobody is there to fix is cut at a space rather than stopping the chapter
    words = " ".join(["word"] * 60) + "."
    cut = [t for t, _ in narrate.plan(small, 2, words)]
    assert all(len(t) <= 120 for t in cut) and " ".join(cut) == words


def test_pause_beats_split_pieces_and_set_the_pause(small):
    ncfg = small["narration"]
    text = f"{A}\n\n{B}\n\n[pause]\n\n{A}\n\n{D}\n\n[pause]\n\n{C}\n\n[pause]"
    got = narrate.plan(small, 2, text)
    assert [t for t, _ in got] == [f"{A}\n\n{B}", f"{A}\n\n{D}", C]  # never grouped across a [pause]; a trailing one is dropped
    assert [p for _, p in got] == [ncfg["segment_pause_ms"], ncfg["beat_pause_ms"], ncfg["beat_pause_ms"]]
    first = narrate.plan(small, 1, f"{A}\n\n[pause]\n\n{B}\n\n{A}")
    assert first == [(A, 0), (B, ncfg["beat_pause_ms"]), (A, ncfg["chunk_pause_ms"])]  # small after a one-line opening


def test_text_is_cleaned_as_narrate_segments_cleans_it(cfg):
    got = narrate.plan(cfg, 1, '## A heading\n\nBoethius said "[t]he end is **near** [...] now [sic]." [D03, D07]')
    assert got == [('Boh-EE-thee-us said "the end is near … now."', 0)]


@pytest.mark.parametrize("bad, message", [
    ("Fine so far.\n\nThen [laughs] she said it.", "read aloud"),
    ("Fine so far.\n\nA claim. [D12", "dossier marker"),
])
def test_a_stray_bracket_raises_before_any_synthesis(run, cfg, bad, message):
    voice = FakeVoice()
    with pytest.raises(StageError, match=message):
        narrate.Narrator(run, cfg, synth=voice).section(1, bad)
    assert voice.calls == [] and narrate.pieces(run, 1) == [] and run.read_log() == []


def test_nothing_to_narrate_is_an_error(run, cfg):
    with pytest.raises(StageError, match="no text"):
        narrate.Narrator(run, cfg, synth=FakeVoice()).section(2, "<!-- seg02 -->\n\n[pause]\n")


# --- pieces as they land ------------------------------------------------------------------------------

def test_on_piece_is_called_as_each_piece_lands_and_before_the_section_finishes(run, cfg):
    voice, seen = FakeVoice(), []

    def on_piece(p):
        # playable now: its files are whole, pieces() lists it, and the next piece has not been asked for yet
        assert p.wav.exists() and p.mp3.exists() and p.wav.with_suffix(".json").exists()
        assert len(voice.calls) == p.index + 1 and len(narrate.pieces(run, 1)) == p.index + 1
        assert not narrate.narrated(run, 1) and not (base.section_dir(run, 1) / "section.mp3").exists()
        seen.append(p)

    out = narrate.Narrator(run, cfg, synth=voice).section(1, SECTIONS[1], on_piece=on_piece)
    assert seen == out and [p.index for p in out] == list(range(len(out))) and len(out) >= 3
    assert [p.last for p in out] == [False] * (len(out) - 1) + [True]
    assert narrate.narrated(run, 1) and narrate.pieces(run, 1) == out
    first = out[0]
    assert first.text.startswith("Picture a harbour wall") and "[D01]" not in first.text and first.pause_ms == 0
    assert first.chars == len(first.text) <= cfg["fast"]["narration"]["first_piece_max_chars"]
    assert all(c["timestamps"] is True and c["voice_id"] == "voice_fake" for c in voice.calls)


def test_a_piece_is_its_pause_then_its_speech(run, cfg):
    ncfg = cfg["narration"]
    out = narrate.Narrator(run, cfg, synth=FakeVoice()).section(2, SECTIONS[2])
    assert out[0].pause_ms == ncfg["segment_pause_ms"]  # a later section opens with the section break
    assert ncfg["beat_pause_ms"] in [p.pause_ms for p in out[1:]]  # the [pause] before "They were not sloppy."
    for p in out:
        speech = timing.audio_duration(p.wav)
        assert p.seconds == pytest.approx(p.pause_ms / 1000 + speech, abs=0.001)
        assert abs(timing.audio_duration(p.mp3) - p.seconds) < 0.08  # what the page plays: the pause included
        meta = json.loads(p.wav.with_suffix(".json").read_text())
        assert meta["text"] == p.text and meta["segment"] == 2 and meta["pause_ms"] == p.pause_ms
        assert meta["seconds"] == p.seconds and meta["simulated"] is False and meta["request_id"].startswith("req_")
        assert timing.chunk_alignment(p.wav)["characters"] == list(p.text)  # the episode reader finds its timings
    # the section file is its pieces, pauses included: the same length
    section = base.section_dir(run, 2) / "section.mp3"
    assert abs(timing.audio_duration(section) - sum(p.seconds for p in out)) < 0.08
    assert not (base.section_dir(run, 2) / "joined.wav").exists()
    manifest = json.loads((base.section_dir(run, 2) / "section.json").read_text())
    assert manifest["pieces"] == len(out) and manifest["seconds"] == pytest.approx(sum(p.seconds for p in out), abs=0.001)


def test_each_synthesis_is_logged_with_its_cost(run, cfg):
    out = narrate.Narrator(run, cfg, synth=FakeVoice()).section(1, SECTIONS[1])
    log = [rec for rec in run.read_log() if rec["stage"] == "fast_narrate"]
    assert len(log) == len(out) and all(rec["provider"] == "elevenlabs" for rec in log)
    assert [rec["characters"] for rec in log] == [p.chars for p in out]
    price = cfg["narration"]["price_per_1000_chars"]
    assert run.spent() == pytest.approx(sum(p.chars for p in out) * price / 1000, abs=1e-4)


def test_the_runs_cap_stops_narration_before_it_pays(run, cfg):
    run.unattended, run.max_usd = True, 0.001
    voice = FakeVoice()
    with pytest.raises(StageError, match="cap"):
        narrate.Narrator(run, cfg, synth=voice).section(1, SECTIONS[1])
    assert voice.calls == []


# --- request stitching --------------------------------------------------------------------------------

def test_request_ids_are_carried_across_pieces_and_sections(run, cfg):
    narrator, voice = narrate_all(run, cfg, only=[1, 2])
    n1 = len(narrate.pieces(run, 1))
    calls = voice.calls
    assert calls[0]["previous_request_ids"] is None and calls[0]["previous_text"] is None
    for i, c in enumerate(calls[1:], 1):  # the last three ids, straight through the section break
        assert c["previous_request_ids"] == [f"req_{k:03d}" for k in range(max(1, i - 2), i + 1)]
        assert c["previous_text"] is None  # ids win over text
    assert calls[n1]["previous_request_ids"][-1] == f"req_{n1:03d}"  # section 2's first piece follows section 1's last
    # the start of the next piece goes along as context, except at a section's last piece (the next is not locked yet)
    assert calls[0]["next_text"] == calls[1]["text"][:500] and calls[n1 - 1]["next_text"] is None
    assert calls[-1]["next_text"] is None
    # after a restart the chain is picked up from the pieces on disk
    again = FakeVoice()
    narrate.Narrator(run, cfg, synth=again).section(3, SECTIONS[3])
    assert again.calls[0]["previous_request_ids"] == [f"req_{k:03d}" for k in range(len(calls) - 2, len(calls) + 1)]


def test_without_request_ids_the_previous_text_carries_the_voice(run, cfg):
    _, voice = narrate_all(run, cfg, voice=FakeVoice(ids=False), only=[1])
    assert all(c["previous_request_ids"] is None for c in voice.calls)
    assert voice.calls[1]["previous_text"] == voice.calls[0]["text"][-500:]


def test_a_busy_service_is_tried_again_and_refused_ids_fall_back_to_text(run, cfg):
    slept = []
    busy = FakeVoice(fail=lambda k: tts.ElevenLabsError("ElevenLabs error 429: busy", 429) if k == 2 else None)
    out = narrate.Narrator(run, cfg, synth=busy, sleep=slept.append).section(1, SECTIONS[1])
    assert slept == [narrate.RETRY_WAITS[0]] and len(busy.calls) == len(out) + 1
    assert busy.calls[1]["text"] == busy.calls[2]["text"]  # the same piece, asked for again

    stale = FakeVoice(fail=lambda k: tts.ElevenLabsError("ElevenLabs error 422: unknown request id", 422) if k == 1 else None)
    narrate.Narrator(run, cfg, synth=stale, sleep=slept.append).section(2, SECTIONS[2])
    assert stale.calls[0]["previous_request_ids"] and stale.calls[1]["previous_request_ids"] is None
    assert stale.calls[1]["previous_text"] == out[-1].text[-500:] and stale.calls[1]["text"] == stale.calls[0]["text"]

    down = FakeVoice(fail=lambda k: tts.ElevenLabsError("Could not reach ElevenLabs", None))
    with pytest.raises(tts.ElevenLabsError):
        narrate.Narrator(run, cfg, synth=down, sleep=slept.append).section(3, SECTIONS[3])
    assert len(down.calls) == 1 + len(narrate.RETRY_WAITS)  # it gives up after the waits are spent
    denied = FakeVoice(fail=lambda k: tts.ElevenLabsError("ElevenLabs error 401: bad key", 401))
    with pytest.raises(tts.ElevenLabsError):
        narrate.Narrator(run, cfg, synth=denied, sleep=slept.append).section(3, SECTIONS[3])
    assert len(denied.calls) == 1  # not something waiting fixes


# --- resume -------------------------------------------------------------------------------------------

def test_resume_reuses_the_pieces_on_disk(run, cfg, monkeypatch):
    _, voice = narrate_all(run, cfg, only=[1])
    first = narrate.pieces(run, 1)
    spent = run.spent()

    # a second run over a finished section: nothing synthesized, nothing joined, every piece still announced
    monkeypatch.setattr(tts, "join_to_mp3", lambda *a, **k: pytest.fail("a finished section was joined again"))
    again, seen = FakeVoice(), []
    assert narrate.Narrator(run, cfg, synth=again).section(1, SECTIONS[1], on_piece=seen.append) == first
    assert again.calls == [] and seen == first and run.spent() == spent
    monkeypatch.undo()

    # a run that died mid-section (two pieces whole, the third half-written, the section not joined)
    d = base.section_dir(run, 1)
    for name in ("p02.json", "section.json", "section.mp3"):
        (d / name).unlink()
    assert len(narrate.pieces(run, 1)) == 2 and not narrate.narrated(run, 1)
    resumed = FakeVoice()
    out = narrate.Narrator(run, cfg, synth=resumed).section(1, SECTIONS[1])
    assert resumed.texts == [p.text for p in first[2:3]]  # only the missing piece is paid for
    assert resumed.calls[0]["previous_request_ids"] == ["req_001", "req_002"]  # stitched to the pieces kept
    assert [p.text for p in out] == [p.text for p in first] and narrate.narrated(run, 1)


def test_a_changed_text_is_narrated_again_and_stale_pieces_go(run, cfg):
    narrate_all(run, cfg, only=[2])
    before = narrate.pieces(run, 2)
    shorter = "\n\n".join(SECTIONS[2].split("\n\n")[:2]).replace("recipe", "formula")
    voice = FakeVoice()
    out = narrate.Narrator(run, cfg, synth=voice).section(2, shorter)
    assert len(out) < len(before) and voice.texts == [p.text for p in out] and "formula" in out[0].text
    assert narrate.pieces(run, 2) == out and not (base.section_dir(run, 2) / f"p{len(out):02d}.wav").exists()
    assert "formula" in (base.section_dir(run, 2) / "script.md").read_text()
    assert abs(timing.audio_duration(base.section_dir(run, 2) / "section.mp3") - sum(p.seconds for p in out)) < 0.08


# --- simulated narration ------------------------------------------------------------------------------

def test_simulated_narration_has_the_length_of_speech_and_takes_as_long_as_the_service(cfg):
    slept = []
    synth = narrate.simulated(cfg, sleep=slept.append)
    text = "A sentence of simulated narration. " * 10
    syn = synth(text, voice_id="v", ncfg=cfg["narration"], previous_request_ids=None, previous_text=None,
                next_text=None, timestamps=True)
    f = cfg["fast"]["narration"]
    seconds = len(syn.audio) / 2 / 24000
    assert seconds == pytest.approx(len(text) / f["speech_chars_per_second"], abs=0.001)
    assert slept == [pytest.approx(len(text) / f["synth_chars_per_second"])]  # the wait is injected: no real sleep
    assert syn.request_id is None and set(syn.audio) == {0}  # silence
    a = syn.alignment
    assert a["characters"] == list(text) and a["character_start_times_seconds"][0] == 0
    assert a["character_end_times_seconds"][-1] == pytest.approx(seconds, abs=0.001)
    assert a["character_start_times_seconds"] == sorted(a["character_start_times_seconds"])


def test_simulate_after_chars_switches_to_silence_and_stays_there(run, cfg):
    todo = narrate.plan(cfg, 1, SECTIONS[1])
    cfg["fast"]["narration"]["simulate_after_chars"] = len(todo[0][0]) + len(todo[1][0])  # two real pieces' worth
    voice, slept = FakeVoice(), []
    narrator = narrate.Narrator(run, cfg, synth=voice, sleep=slept.append)
    out = narrator.section(1, SECTIONS[1]) + narrator.section(2, SECTIONS[2])
    assert [p.simulated for p in out] == [False, False] + [True] * (len(out) - 2)
    assert voice.texts == [p.text for p in out[:2]]  # ElevenLabs is asked for two pieces and no more
    f = cfg["fast"]["narration"]
    for p in out[2:]:
        assert timing.audio_duration(p.wav) == pytest.approx(p.chars / f["speech_chars_per_second"], abs=0.001)
    assert slept == [pytest.approx(p.chars / f["synth_chars_per_second"]) for p in out[2:]]
    log = [rec for rec in run.read_log() if rec["stage"] == "fast_narrate" and "characters" in rec]
    assert [rec["provider"] for rec in log] == ["elevenlabs"] * 2 + ["simulated"] * (len(out) - 2)
    assert all(rec["cost_usd"] == 0 for rec in log[2:])
    assert run.spent() == pytest.approx(sum(p.chars for p in out[:2]) * cfg["narration"]["price_per_1000_chars"] / 1000, abs=1e-4)
    # simulated pieces carry timings too, so the section is timed without the voice
    assert narrate.section_timing(run, 2)["stats"]["estimated"] == 0
    # a restart counts the real characters already on disk, and stays simulated
    later = FakeVoice()
    assert all(p.simulated for p in narrate.Narrator(run, cfg, synth=later, sleep=slept.append).section(3, SECTIONS[3]))
    assert later.calls == []


def test_simulate_from_the_start(run, cfg):
    cfg["fast"]["narration"]["simulate_after_chars"] = 0
    run.unattended, run.max_usd = True, 0.0  # silence costs nothing, so even a spent cap lets it through
    voice = FakeVoice()
    out = narrate.Narrator(run, cfg, synth=voice, sleep=lambda s: None).section(1, SECTIONS[1])
    assert voice.calls == [] and all(p.simulated for p in out) and run.spent() == 0


# --- the section's word timing ------------------------------------------------------------------------

def test_section_timing_is_built_from_the_alignments_without_whisper(run, cfg, monkeypatch):
    from episode import recognize

    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: pytest.fail("Whisper was loaded"))
    narrate_all(run, cfg, only=[1, 2])
    for n in (1, 2):
        t = narrate.section_timing(run, n)
        ps = narrate.pieces(run, n)
        assert t["aligned_by"] == "elevenlabs" and t["pieces"] == len(ps) and t["section"] == n
        assert t["stats"]["estimated"] == 0  # every word has a time from the timings it was given
        assert [s["id"] for s in t["segments"]] == [f"seg{n:02d}"]
        assert t["segments"][0]["sentences"][0]["id"] == f"seg{n:02d}-s001"
        words = [w for x in t["segments"][0]["sentences"] for w in x["words"]]
        assert all(b["start"] >= a["end"] for a, b in zip(words, words[1:]))
        assert t["seconds"] == pytest.approx(sum(p.seconds for p in ps), abs=0.002)
        assert words[-1]["end"] <= t["seconds"] + 0.05
        # section time starts at the first piece's pause: the first word comes right after it
        assert words[0]["start"] == pytest.approx(ps[0].pause_ms / 1000, abs=0.02)
        # and the last piece's words sit after everything before it, its own pause included
        offset = sum(p.seconds for p in ps[:-1]) + ps[-1].pause_ms / 1000
        hit = next(w for w in words if w["start"] >= offset - 0.02)
        assert hit["w"] == ps[-1].text.split()[0] and hit["start"] == pytest.approx(offset, abs=0.02)
    # the displayed word keeps the script's form; the spoken form is what the voice was sent
    t1 = narrate.section_timing(run, 1)
    quoted = next(w for x in t1["segments"][0]["sentences"] for w in x["words"] if w["w"].startswith('"[t]he'))
    assert quoted["spoken"] == '"the'
    assert "[D0" not in json.dumps(t1) and "<!--" not in json.dumps(t1)


def test_a_section_without_timings_still_plays_and_says_why(run, cfg, monkeypatch):
    from episode import recognize

    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: pytest.fail("Whisper was loaded"))
    out = narrate.Narrator(run, cfg, synth=FakeVoice(alignments=False)).section(1, SECTIONS[1])
    assert narrate.narrated(run, 1) and all(p.mp3.exists() for p in out)
    assert narrate.section_timing(run, 1) is None
    errors = [rec for rec in run.read_log() if rec.get("error")]
    assert len(errors) == 1 and "character timings" in errors[0]["error"] and errors[0]["label"] == "section 1 timing"
    assert json.loads((base.section_dir(run, 1) / "section.json").read_text())["timing"] is False


def test_the_default_voice_is_tts_synthesize_looked_up_at_call_time(run, cfg, monkeypatch):
    narrator = narrate.Narrator(run, cfg)  # made before the patch, as a long-lived narrator would be
    voice = FakeVoice()
    monkeypatch.setattr(tts, "synthesize", voice)
    narrator.section(1, SECTIONS[1])
    assert len(voice.calls) == len(narrate.pieces(run, 1)) > 0


# --- review fixes -------------------------------------------------------------------------------------

LINE = "A modern sea wall beside it would be rubble in fifty years, and nobody could say why."
OPENER = "Picture a harbour wall at Caesarea, two thousand years old, still standing in the surf."


def dry_seconds(cfg, pieces):
    """Seconds the player has nothing to play, if playback starts as the section's first piece lands and each
    later piece takes a request's own time and its length / synth_chars_per_second to make (the config's rates)."""
    f = cfg["fast"]["narration"]
    ready = at = dry = 0.0  # when the piece being made lands; where playback has got to
    for i, (text, pause) in enumerate(pieces):
        if i:
            ready += narrate.REQUEST_SECONDS + len(text) / f["synth_chars_per_second"]
            if ready > at:
                dry, at = dry + ready - at, ready
        at += pause / 1000 + len(text) / f["speech_chars_per_second"]
    return dry


def test_a_short_opener_does_not_leave_playback_dry(cfg):
    """A one-line opening and a long second paragraph: the second piece used to be the whole paragraph
    (12 s to make against 5.6 s of audio), so the audio stopped right after the first sentence."""
    f = cfg["fast"]["narration"]
    long_para = " ".join([LINE] * 7)
    got = narrate.plan(cfg, 1, f"{OPENER}\n\n{long_para}\n\n{long_para}")
    assert got[0] == (OPENER, 0)
    assert dry_seconds(cfg, got) == 0
    sizes = [len(t) for t, _ in got]
    assert sizes[1] < sizes[2] <= sizes[3]  # the pieces grow as the audio queued ahead of them builds
    assert max(sizes) <= f["piece_max_chars"]
    assert " ".join(t for t, _ in got).split() == f"{OPENER} {long_para} {long_para}".split()  # every word, once
    # a later section whose first beat is one line, heard by a listener who was waiting for it
    later = narrate.plan(cfg, 2, f"They were not sloppy.\n\n[pause]\n\n{long_para}")
    assert later[0] == ("They were not sloppy.", cfg["narration"]["segment_pause_ms"])
    assert later[1] == (LINE, cfg["narration"]["beat_pause_ms"]) and dry_seconds(cfg, later) == 0
    assert " ".join(t for t, _ in later[1:]) == long_para


def test_no_piece_takes_longer_to_make_than_the_audio_ahead_of_it(cfg):
    import random

    f, rng = cfg["fast"]["narration"], random.Random(11)
    speech, synth = f["speech_chars_per_second"], f["synth_chars_per_second"]

    def sentence():
        return " ".join(rng.choice(["wall", "harbour", "lime", "ash", "sea", "Romans", "built", "the", "a", "still"])
                        for _ in range(rng.randint(3, 28))).capitalize() + "."

    for trial in range(300):
        paras = [" ".join(sentence() for _ in range(rng.randint(1, 9))) for _ in range(rng.randint(2, 8))]
        if trial % 4 == 0:
            paras.insert(rng.randint(1, len(paras) - 1), "[pause]")
        n = rng.choice([1, 2])
        got = narrate.plan(cfg, n, "\n\n".join(paras))
        assert " ".join(t for t, _ in got).split() == " ".join(p for p in paras if p != "[pause]").split()
        lead = 0.0  # seconds of audio queued ahead of the piece about to be made
        for i, (text, pause) in enumerate(got):
            one_sentence = "\n\n" not in text and len(tts.split_sentences(text)) == 1
            make = narrate.REQUEST_SECONDS + len(text) / synth
            assert not i or make <= lead or one_sentence, (trial, i, len(text), lead)
            lead += pause / 1000 + len(text) / speech - (make if i else 0)


def test_one_piece_without_timings_does_not_cost_the_section_its_timing(run, cfg, monkeypatch):
    from episode import recognize

    monkeypatch.setattr(recognize, "WhisperRecognizer", lambda *a, **k: pytest.fail("Whisper was loaded"))
    out = narrate.Narrator(run, cfg, synth=FakeVoice(bare=(2,))).section(1, SECTIONS[1])
    assert len(out) >= 3 and timing.chunk_alignment(out[1].wav) is None  # the second piece came back bare
    t = narrate.section_timing(run, 1)
    assert t is not None and json.loads((base.section_dir(run, 1) / "section.json").read_text())["timing"] is True
    words = [w for x in t["segments"][0]["sentences"] for w in x["words"]]
    guessed = [w for w in words if w.get("estimated")]
    # that piece's words are marked, and placed evenly over its own audio (after its pause); the rest are exact
    assert " ".join(w.get("spoken", w["w"]) for w in guessed) == out[1].text
    assert t["stats"]["estimated"] == len(guessed) == len(out[1].text.split())
    begins = out[0].seconds + out[1].pause_ms / 1000
    assert guessed[0]["start"] == pytest.approx(begins, abs=0.02)
    assert guessed[-1]["end"] <= out[0].seconds + out[1].seconds + 0.02
    assert all(b["start"] >= a["end"] for a, b in zip(words, words[1:]))
    assert [rec for rec in run.read_log() if rec.get("error")] == []
    # a bare piece at the very end has no word after it to stop at: its words still end with the audio, in order
    last = len(narrate.plan(cfg, 2, SECTIONS[2]))
    out = narrate.Narrator(run, cfg, synth=FakeVoice(bare=(last,))).section(2, SECTIONS[2])
    t = narrate.section_timing(run, 2)
    words = [w for x in t["segments"][0]["sentences"] for w in x["words"]]
    assert [w["w"] for w in words if w.get("estimated")] == out[-1].text.split()
    assert all(b["start"] >= a["end"] for a, b in zip(words, words[1:])) and all(w["end"] > w["start"] for w in words)
    assert words[-1]["end"] <= t["seconds"] + 0.02


def test_narration_that_costs_nothing_is_not_refused_by_a_spent_cap(run, cfg):
    """A run is over its cap whenever its last Claude call overshot it: silence, and a section whose
    pieces are all on disk, must still go through."""
    run.unattended, run.max_usd = True, 5.0
    narrate.Narrator(run, cfg, synth=FakeVoice()).section(1, SECTIONS[1])
    first = narrate.pieces(run, 1)
    run.log({"stage": "fast_lock", "cost_usd": 0.5})
    run.max_usd = 0.01
    assert run.spent() > run.max_usd
    again = FakeVoice()
    assert narrate.Narrator(run, cfg, synth=again).section(1, SECTIONS[1]) == first and again.calls == []
    with pytest.raises(StageError, match="cap"):  # what does cost something is still stopped
        narrate.Narrator(run, cfg, synth=again).section(2, SECTIONS[2])
    assert again.calls == []


def test_simulated_narration_is_not_refused_by_a_spent_cap(run, cfg):
    cfg["fast"]["narration"]["simulate_after_chars"] = 0
    run.unattended, run.max_usd = True, 0.01
    run.log({"stage": "fast_lock", "cost_usd": 0.04})
    voice = FakeVoice()
    out = narrate.Narrator(run, cfg, synth=voice, sleep=lambda s: None).section(1, SECTIONS[1])
    assert voice.calls == [] and all(p.simulated for p in out) and narrate.narrated(run, 1)


def test_a_run_with_simulated_narration_is_not_continued_as_a_real_chapter(run, cfg):
    """It used to stay simulated without saying so: the silent pieces were reused as the voice and the rest
    of the chapter was silence, at no cost and with no error."""
    cfg["fast"]["narration"]["simulate_after_chars"] = 0
    narrate.Narrator(run, cfg, synth=FakeVoice(), sleep=lambda s: None).section(1, SECTIONS[1])
    cfg["fast"]["narration"]["simulate_after_chars"] = None  # config.yaml's own value
    with pytest.raises(StageError, match="simulated narration"):
        narrate.Narrator(run, cfg, synth=FakeVoice())
    cfg["fast"]["narration"]["simulate_after_chars"] = 0  # the build test itself may go on
    voice = FakeVoice()
    out = narrate.Narrator(run, cfg, synth=voice, sleep=lambda s: None).section(2, SECTIONS[2])
    assert voice.calls == [] and all(p.simulated for p in out)


def test_balance_reads_the_subscription_and_needed_chars_is_the_chapters_upper_estimate(cfg, monkeypatch):
    asked = []

    class Sub:
        def get(self):
            asked.append(1)
            return types.SimpleNamespace(tier="starter", character_count=23500, character_limit=40000)
    monkeypatch.setattr(tts, "_el_client", types.SimpleNamespace(user=types.SimpleNamespace(subscription=Sub())))
    assert narrate.balance() == (23500, 40000) and asked == [1]

    class Down:
        def get(self):
            raise tts.ElevenLabsError("Could not reach ElevenLabs", None)
    monkeypatch.setattr(tts, "_el_client", types.SimpleNamespace(user=types.SimpleNamespace(subscription=Down())))
    assert narrate.balance() is None  # a failed read must not stop a run
    f = cfg["fast"]
    assert narrate.needed_chars(cfg) == max(f["total_minutes"]) * f["words_per_minute"] * narrate.CHARS_PER_WORD
    assert 17_200 < narrate.needed_chars(cfg) < 40_000  # above the 20-minute chapters on record, inside one month's plan


def test_the_narrator_never_reads_the_balance(run, cfg, monkeypatch):
    monkeypatch.setattr(narrate, "balance", lambda: pytest.fail("the narrator read the balance"))
    narrate.Narrator(run, cfg, synth=FakeVoice()).section(1, SECTIONS[1])
