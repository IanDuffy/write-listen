"""The live chapter's archive (Spec One Live Voice, "Archive"): the reading the listener heard, cut from
the GPT-Live sessions' recordings into the layout the episode page and ask-by-voice already read.

    runs/<run>/live/<live_id>.wav    each session's recording (OpenAI, with live.store true; stereo 24 kHz
                                     PCM16, the listener left, the guide right, on the transcript's clock)
    10_audio/secNN/pKK.wav           one paragraph as heard, speech only (the guide channel)
    10_audio/secNN/pKK.mp3           its pause (live.json's pause_ms), then the speech
    10_audio/secNN/pKK.json          {id, text, seconds, pause_ms, last, simulated: false, live_id, times, parts, how}
    10_audio/secNN/section.mp3/.json the section joined
    10_audio/chapter.mp3, 09_script_final.md, 05_brief.md, 11_episode/timing.json
                                     faststart/archive.py's stitching (_assemble), timed by Whisper
    11_episode/episode.json          the episode page's file (reference cards only with references=True)
    12_asks/asides.jsonl             each live aside (a paragraph and sentence) gains t, sentence_id, segment,
                                     and its restart a t and sentence_id: the page shows it as text there
    fast/archive.json                real seconds, "estimated": false, what was skipped and joined
    status.json                      "audio": "10_audio/chapter.mp3"
    runs/<run>/live/archive.json     progress and the result (the route's background thread)

Which reading of a paragraph (runs/<run>/live/reading.jsonl, the page's log): the latest whole reading
(not cut, from sentence 0). Without one, the latest complete reading, which began at sentence k after a
question, joined to the interrupted reading before it up to where sentence k begins (found by Whisper
on that clip, then moved into the pause before it), and further back if that one began mid-paragraph
too. A paragraph never heard is skipped, listed, and left out of 09_script_final.md (so the timing and
the page hold only what was read; a section never heard at all is left out whole); one whose opening
was never heard is kept from where it was heard ("partial"; its script keeps the whole paragraph).

Cutting: the transcript's times come in 200 ms steps; the first word's start is close (within about
0.25 s), the last word's end is often early (0.6-0.9 s on the smoke runs). So a piece starts at the
first onset after a pause near the transcript's start, and ends where the guide falls quiet for
END_GAP after the transcript's end, never past the next reading's onset in the same session.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import traceback
import wave
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from episode import align as ep_align
from faststart import archive as fast_archive
from faststart import base
from faststart import live as fast_live
from faststart import narrate
from stages import tts
from stages.common import Run, StageError, spoken_text

from . import chapter as live_chapter
from . import session as live_session

GUIDE = 1               # the recording's right channel
FRAME = 0.01            # seconds per energy frame
SPEECH_RMS = 150        # a frame louder than this is speech (GPT-Live's silence is digital zero; speech peaks ~4,000)
ONSET_QUIET = 0.10      # an onset follows at least this much quiet
ONSET_BACK, ONSET_FWD = 0.35, 0.6  # where to look for it around the transcript's start
LEAD = 0.03             # kept before an onset
END_GAP = 0.5           # the guide quiet this long after the transcript's end: the paragraph has ended
END_LOOK = 3.0          # look this far past the transcript's end for it
TAIL = 0.12             # kept after the last speech (the decay)
JOIN_GAP_MS = 300       # between the two parts of a joined paragraph
LOG = "reading.jsonl"
PROGRESS = "archive.json"

_LOCK = threading.Lock()
_RUNNING: dict[str, threading.Thread] = {}


# --- the log and the recordings ------------------------------------------------------------------

def live_dir(r: Run) -> Path:
    return r.root / "live"


def readings(r: Run) -> list[dict]:
    """reading.jsonl in order; each record gets "n" (its line) and "live_id" (a record from before the page
    logged it takes the run's only session, when there is one)."""
    p = live_dir(r) / LOG
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            try:
                out.append({**json.loads(line), "n": i})
            except json.JSONDecodeError:
                continue
    ids = {x["live_id"] for x in out if x.get("live_id")}
    if len(ids) == 1:
        only = ids.pop()
        for x in out:
            x.setdefault("live_id", only)
    return out


def wav_path(r: Run, live_id: str) -> Path:
    return live_dir(r) / f"{live_id}.wav"


def fetch(r: Run, cfg: dict, client=None) -> dict:
    """Download each session's recording that reading.jsonl names into runs/<run>/live/<live_id>.wav
    (one already there is kept). {"have": [...], "downloaded": [...], "missing": [...]}; StageError when
    no recording of the reading is to be had."""
    recs = readings(r)
    if not recs:
        raise StageError(f"Nothing in {r.name} was read live (no live/{LOG}).")
    ids = list(dict.fromkeys(x["live_id"] for x in recs if x.get("live_id")))
    if not ids:
        raise StageError("The reading log names no GPT-Live session (the page logged readings before it kept "
                         "live_id), so there is no recording to fetch.")
    out = {"have": [], "downloaded": [], "missing": []}
    for lid in ids:
        dest = wav_path(r, lid)
        if dest.exists() and dest.stat().st_size > 44:
            out["have"].append(lid)
            continue
        part = dest.with_name(dest.name + ".part")
        try:
            got = live_session.recording(cfg, lid, part, client=client)
        except StageError:
            raise
        except Exception as e:  # network: this session is missing, the others may not be
            got = None
            out.setdefault("errors", {})[lid] = f"{type(e).__name__}: {e}"
        if got:
            part.replace(dest)
            out["downloaded"].append(lid)
        else:
            part.unlink(missing_ok=True)
            out["missing"].append(lid)
    if not out["have"] and not out["downloaded"]:
        raise StageError("No recording of the reading is available from OpenAI for "
                         f"{', '.join(ids)}. Recordings exist only when config live.store was true while the "
                         "chapter was read, only once the session has ended, and for 30 days.")
    return out


class Recording:
    """One session's WAV, read a window at a time (two hours of stereo is 690 MB)."""

    def __init__(self, path: Path):
        self.path = path
        try:
            with wave.open(str(path), "rb") as w:
                self.rate, self.channels, self.width, self.frames = (w.getframerate(), w.getnchannels(),
                                                                     w.getsampwidth(), w.getnframes())
        except (wave.Error, EOFError) as e:
            raise StageError(f"{path.name} is not a WAV this can read: {e}") from None
        if self.width != 2:
            raise StageError(f"{path.name} is not 16-bit PCM.")
        self.duration = self.frames / self.rate

    def guide(self, a: float, b: float) -> np.ndarray:
        """The guide channel's samples in [a, b) seconds, int16."""
        i, j = max(0, int(round(a * self.rate))), min(self.frames, int(round(b * self.rate)))
        if j <= i:
            return np.zeros(0, np.int16)
        with wave.open(str(self.path), "rb") as w:
            w.setpos(i)
            x = np.frombuffer(w.readframes(j - i), np.int16)
        x = x[: (len(x) // self.channels) * self.channels].reshape(-1, self.channels)
        return x[:, GUIDE if self.channels > 1 else 0].copy()

    def rms(self, a: float, b: float) -> tuple[float, np.ndarray]:
        """(where the first frame starts, the RMS of each FRAME from there to b)."""
        a = max(0.0, a)
        x = self.guide(a, b).astype(np.float64)
        f = int(self.rate * FRAME)
        n = len(x) // f
        return a, (np.sqrt((x[: n * f].reshape(n, f) ** 2).mean(axis=1)) if n else np.zeros(0))


def onset(rec: Recording, t: float) -> float:
    """Where the guide starts speaking near transcript time t: the first speech frame within
    [t - ONSET_BACK, t + ONSET_FWD] that follows ONSET_QUIET of quiet, less LEAD. t itself if there is none
    (the guide ran straight on into it)."""
    a, r = rec.rms(t - ONSET_BACK - ONSET_QUIET, t + ONSET_FWD)
    speech = r >= SPEECH_RMS
    q = int(round(ONSET_QUIET / FRAME))
    first = int(round((t - ONSET_BACK - a) / FRAME))
    for i in range(max(first, 0), len(speech)):
        if speech[i] and not speech[max(0, i - q):i].any() and (i >= q or a == 0.0):
            return max(0.0, a + i * FRAME - LEAD)
    return max(0.0, t - 0.05)


def speech_end(rec: Recording, t: float, hi: float) -> float:
    """Where the guide falls quiet after transcript end t: the end of the last speech before END_GAP of
    quiet, plus TAIL, and never past hi."""
    hi = min(hi, rec.duration)
    a, r = rec.rms(t - 0.3, hi)
    speech = r >= SPEECH_RMS
    gap, last, quiet = int(round(END_GAP / FRAME)), None, 0
    for i, s in enumerate(speech):
        if s:
            last, quiet = i, 0
        else:
            quiet += 1
            if last is not None and quiet >= gap:
                break
    if last is None:
        return min(t, hi)
    return min(hi, a + (last + 1) * FRAME + TAIL)


def cut_before(rec: Recording, t: float) -> float:
    """A cut just before a word that starts near t: in the pause before it (TAIL into the pause), or at
    the quietest frame when there is none."""
    a, r = rec.rms(t - 0.45, t + 0.1)
    if not len(r):
        return t
    quiet = r < SPEECH_RMS
    runs, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if j - i >= 6:  # 60 ms
                runs.append((i, j))
            i = j
        else:
            i += 1
    target = (t - a) / FRAME
    if runs:
        i, j = min(runs, key=lambda x: abs(x[1] - target))
        return a + (i + min(TAIL / FRAME, (j - i) / 2)) * FRAME
    return a + int(np.argmin(r)) * FRAME


# --- which reading ---------------------------------------------------------------------------

def _words(text: str) -> int:
    return len(live_chapter._norm(text))


def _covers(x: dict, p: dict, s: int, k: int) -> bool:
    """Reading x (from sentence s) was heard through sentence k-1."""
    need = _words(" ".join(p["sentences"][s:k]))
    return _words(x.get("heard") or "") >= need - 1


def choose(recs: list[dict], p: dict) -> dict:
    """The paragraph's audio from its readings (in log order, each with times and a session):
    {"how": "whole"|"joined"|"partial", "parts": [(reading, from sentence, to sentence or None)],
    "missing": sentences never heard (partial)} or {"skip": why}."""
    usable = [x for x in recs if (x.get("times") or {}).get("start_ms") is not None and x.get("live_id")]
    if not usable:
        return {"skip": "never heard" if not recs else "heard, but with no times or session to cut it by"}
    done = [x for x in usable if not x.get("cut")]
    whole = [x for x in done if not x.get("sentence")]
    if whole:
        return {"how": "whole", "parts": [(whole[-1], 0, None)]}
    if not done:
        return {"skip": "only interrupted readings, never picked up again"}
    last = done[-1]
    k = min(int(last["sentence"]), len(p["sentences"]) - 1)
    chain, need = [(last, k, None)], k
    for x in reversed(usable[:usable.index(last)]):
        s = int(x.get("sentence") or 0)
        if need and s < need and _covers(x, p, s, need):
            chain.insert(0, (x, s, need))
            need = s
    return {"how": "joined" if need == 0 else "partial", "parts": chain,
            **({"missing": [0, need]} if need else {})}


# --- cutting ---------------------------------------------------------------------------------

class Cutter:
    """Cuts readings out of the recordings: every reading in a session bounds its neighbours."""

    def __init__(self, r: Run, recs: list[dict], make_recognizer=None):
        self.r = r
        self.recs = recs
        self.recordings: dict[str, Recording] = {}
        self.make_recognizer = make_recognizer
        self._recognizer = None
        self.by_session: dict[str, list[dict]] = {}
        for x in recs:
            if (x.get("times") or {}).get("start_ms") is not None and x.get("live_id"):
                self.by_session.setdefault(x["live_id"], []).append(x)
        for xs in self.by_session.values():
            xs.sort(key=lambda x: x["times"]["start_ms"])

    def recording(self, lid: str) -> Recording | None:
        if lid not in self.recordings:
            p = wav_path(self.r, lid)
            self.recordings[lid] = Recording(p) if p.exists() else None
        return self.recordings[lid]

    def recognizer(self):
        if self._recognizer is None:
            if self.make_recognizer is None:
                from episode import recognize
                self._recognizer = recognize.WhisperRecognizer()
            else:
                self._recognizer = self.make_recognizer()
        return self._recognizer

    def span(self, x: dict) -> tuple[float, float]:
        """Reading x's speech in its session: (start, end) seconds."""
        rec = self.recording(x["live_id"])
        t0, t1 = x["times"]["start_ms"] / 1000, (x["times"].get("end_ms") or x["times"]["start_ms"]) / 1000
        start = onset(rec, t0)
        xs = self.by_session[x["live_id"]]
        later = [y for y in xs if y["times"]["start_ms"] > x["times"]["start_ms"]]
        hi = t1 + END_LOOK
        if later:
            hi = min(hi, onset(rec, later[0]["times"]["start_ms"] / 1000) - 0.02)
        return start, max(start, speech_end(rec, max(t1, start), max(hi, start)))

    def sentence_start(self, x: dict, p: dict, s: int, k: int, a: float, b: float) -> tuple[float, str]:
        """Where sentence k begins in reading x (which began at sentence s, speech a..b), and how it was found."""
        rec = self.recording(x["live_id"])
        words = [{"w": w, "spoken": w} for w in live_chapter.text_from(p, s).split()]
        wk = len(" ".join(p["sentences"][s:k]).split())
        found, how = None, "estimated"
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.wav"
            _write_wav(clip, rec.guide(a, b), rec.rate)
            try:
                heard = self.recognizer()(clip)
            except Exception as e:  # noqa: BLE001 (Whisper failing is not the chapter failing: estimate)
                heard, how = [], f"estimated (recognizer failed: {type(e).__name__})"
        if heard and wk < len(words):
            times = ep_align.align(words, heard, limit=b - a)
            if any(t["match"] != "estimated" for t in times[wk:]):
                found, how = a + times[wk]["start"], "whisper"
            elif any(t["match"] != "estimated" for t in times[:wk]):
                m = max(i for i, t in enumerate(times[:wk]) if t["match"] != "estimated")
                if m == wk - 1:  # it was heard to the end of sentence k-1 and no further
                    return b, "whisper (the reading stopped before it)"
        if found is None:  # by words, over the transcript's span
            heard_words = max(1, _words(x.get("heard") or ""))
            found = a + (b - a) * min(1.0, _words(" ".join(p["sentences"][s:k])) / heard_words)
        return cut_before(rec, found), how

    def cut(self, choice: dict, p: dict) -> tuple[np.ndarray, int, list[dict]]:
        """The paragraph's samples, the rate, and where each part came from."""
        parts, audio, rate = [], [], None
        for x, s, k in choice["parts"]:
            rec = self.recording(x["live_id"])
            a, b = self.span(x)
            how = "transcript"
            if k is not None:
                b, how = self.sentence_start(x, p, s, k, a, b)
            if rate is None:
                rate = rec.rate
            elif rec.rate != rate:
                raise StageError(f"The recordings of {p['id']} are at different sample rates.")
            if audio:
                audio.append(np.zeros(int(rate * JOIN_GAP_MS / 1000), np.int16))
            audio.append(rec.guide(a, b))
            parts.append({"live_id": x["live_id"], "start": round(a, 3), "end": round(b, 3), "sentence_from": s,
                          "sentence_to": k, "line": x["n"], "cut_by": how})
        return np.concatenate(audio) if audio else np.zeros(0, np.int16), rate or 24000, parts


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())


# --- the build -------------------------------------------------------------------------------

def _paragraphs(r: Run, cfg: dict, n: int) -> list[dict]:
    """live.json's paragraphs for section n; a locked section without one (a run read before live.json
    was written) gets it now, exactly as the live narrator writes it."""
    paras = fast_live.paragraphs(r, n)
    if not paras and r.segment_path(n).exists():
        text = r.segment_path(n).read_text(encoding="utf-8").strip()
        paras = fast_live.plan(cfg, n, text)
        base.write_json(base.section_dir(r, n) / fast_live.FILE,
                        {"section": n, "paragraphs": paras, "seconds": round(sum(p["seconds"] for p in paras), 3)})
    # The sentences the page counts a restart's "sentence" in (voice.gpt_live.chapter.clean_paragraphs).
    return [{**p, "sentences": p.get("sentences") or tts.split_sentences(p["text"])} for p in paras]


def heard_text(text: str, paras: list[dict], gone: set[str]) -> tuple[str, list[str]]:
    """A locked section's text without the paragraphs never heard (every live paragraph a block of it
    became is in `gone`), so the script, the timing and the episode page hold only what was read; and the
    ids dropped. The blocks are cleaned as clean_paragraphs cleans them and must give live.json's
    paragraphs in order; when they do not, the text is kept whole (its unheard words come out estimated)."""
    if not gone:
        return text, []
    out, dropped, i = [], [], 0
    for block in re.split(r"\n\s*\n", text):
        pieces = []
        for beat in tts.split_beats(spoken_text(block)):
            para = re.sub(r"\s+", " ", tts.speak_editorial_brackets(beat)).strip()
            if para:
                pieces += narrate._fit(para, live_chapter.PARA_MAX_CHARS)
        mine = paras[i:i + len(pieces)]
        if [p["text"] for p in mine] != pieces:
            return text, []
        i += len(pieces)
        if pieces and all(p["id"] in gone for p in mine):
            dropped += [p["id"] for p in mine]
        else:
            out.append(block)
    if i != len(paras):
        return text, []
    return "\n\n".join(out).strip(), dropped


def build(r: Run, cfg: dict, make_recognizer=None, references: bool = False, on_step=None) -> dict:
    """Cut every paragraph from the recordings, stitch the chapter, time it, build the episode, place the
    asides, and point status.json at the chapter. Returns what fast/archive.json now says."""
    step = on_step or (lambda *a, **k: None)
    if not base.is_live(r):
        raise StageError(f"{r.name} was not read live (fast.json has no \"voice\": \"live\").")
    outline = base.read_json(base.path(r, "outline_json"))
    if not outline:
        raise StageError(f"{r.name} has no outline (05_outline.json).")
    paras = {s["n"]: _paragraphs(r, cfg, s["n"]) for s in outline["sections"]}
    sections = fast_archive._sections(r)  # every section locked and in live.json, or StageError
    recs = readings(r)
    if not recs:
        raise StageError(f"Nothing in {r.name} was read live (no live/{LOG}).")
    known = {p["id"] for ps in paras.values() for p in ps}
    cutter = Cutter(r, recs, make_recognizer)
    for lid in cutter.by_session:
        if cutter.recording(lid) is None:
            raise StageError(f"The recording of session {lid} is not on disk: fetch it first.")

    step("cutting")
    ncfg = cfg["narration"]
    out_sections, skipped, partial, joined, deviating = [], [], [], [], []
    for n, text, _ in sections:
        d = base.section_dir(r, n)
        d.mkdir(parents=True, exist_ok=True)
        heard_paras = []
        for p in paras[n]:
            choice = choose([x for x in recs if x.get("id") == p["id"]], p)
            if "skip" in choice:
                skipped.append({"id": p["id"], "reason": choice["skip"]})
                continue
            samples, rate, parts = cutter.cut(choice, p)
            if len(samples) < rate * 0.2:
                skipped.append({"id": p["id"], "reason": "its cut held no speech"})
                continue
            heard_paras.append((p, choice, samples, rate, parts))
        pieces = []
        for i, (p, choice, samples, rate, parts) in enumerate(heard_paras):
            stem = d / f"p{i:02d}"
            stem.with_suffix(".json").unlink(missing_ok=True)
            _write_wav(stem.with_suffix(".wav"), samples, rate)
            pause = p["pause_ms"]
            if i == 0:  # the section's first piece, though its first paragraph was skipped: a section break,
                pause = paras[n][0]["pause_ms"] if out_sections else 0  # or nothing before the chapter
            narrate._play_mp3(stem.with_suffix(".wav"), pause, stem.with_suffix(".mp3"), ncfg["mp3_bitrate"])
            seconds = round(pause / 1000 + len(samples) / rate, 3)
            last = i == len(heard_paras) - 1
            text_heard = p["text"] if choice["how"] != "partial" else live_chapter.text_from(p, choice["missing"][1])
            meta = {"id": p["id"], "segment": n, "index": i, "text": text_heard, "seconds": seconds,
                    "pause_ms": pause, "last": last, "simulated": False, "request_id": None,
                    "live_id": parts[-1]["live_id"],
                    "times": {"start": parts[0]["start"], "end": parts[-1]["end"]}, "parts": parts,
                    "how": choice["how"]}
            base.write_json(stem.with_suffix(".json"), meta)
            pieces.append(narrate.Piece(section=n, index=i, wav=stem.with_suffix(".wav"), mp3=stem.with_suffix(".mp3"),
                                        seconds=seconds, pause_ms=pause, chars=len(text_heard),
                                        text=text_heard, simulated=False, last=last))
            if choice["how"] == "joined":
                joined.append({"id": p["id"], "parts": parts})
            elif choice["how"] == "partial":
                partial.append({"id": p["id"], "missing_sentences": choice["missing"]})
            if any(x.get("deviates") for x, _, _ in choice["parts"]):
                deviating.append(p["id"])
        for stale in d.glob("p*.*"):  # pieces past the end, from an earlier archive
            if (m := re.fullmatch(r"p(\d+)\.(?:wav|json|mp3)", stale.name)) and int(m.group(1)) >= len(pieces):
                stale.unlink()
        if pieces:  # a section never heard at all is left out of the chapter (and so of its script)
            text, _ = heard_text(text, paras[n], {s["id"] for s in skipped})
            _finish_section(r, cfg, n, text, pieces)
            out_sections.append((n, text, pieces))
        else:
            for name in ("section.mp3", "section.json", "script.md", "concat.txt"):
                (d / name).unlink(missing_ok=True)
    if not out_sections:
        raise StageError("No paragraph of the chapter has a usable reading in the recordings.")

    step("timing")
    layout = fast_archive._offsets(out_sections)
    saved = fast_archive._assemble(r, cfg, out_sections, layout)  # chapter.mp3, the script, Whisper timing, brief
    if references:
        saved["episode_cost_usd"] = fast_archive.episode(r, cfg)  # reference cards, then episode.json
    else:
        from episode import bundle
        bundle.write(r.root)

    step("asides")
    timing = json.loads((r.root / "11_episode" / "timing.json").read_text(encoding="utf-8"))
    asides = place_asides(r, timing, paras)
    status_audio(r)
    unknown = sorted({x.get("id") for x in recs if x.get("id") not in known})
    saved.update({"live": True, "estimated": False, "recorded": True, "episode": True, "references": references,
                  "pieces": sum(len(ps) for _, _, ps in out_sections),
                  "paragraphs": sum(len(ps) for ps in paras.values()),
                  "skipped": skipped, "partial": partial, "joined": joined, "deviating": deviating,
                  "unknown_ids": unknown, "asides": asides, "live_ids": list(cutter.by_session),
                  "archived_at": base.now_iso()})
    base.write_json(base.path(r, "work") / "archive.json", saved)
    r.log({"stage": "live_archive", "label": "the live reading, cut from the recordings",
           "audio_seconds": saved["seconds"], "pieces": saved["pieces"], "skipped": len(skipped),
           "joined": len(joined), "partial": len(partial)})
    return saved


def _finish_section(r: Run, cfg: dict, n: int, text: str, pieces: list[narrate.Piece]) -> None:
    from stages.common import join_script_segments

    d = base.section_dir(r, n)
    (d / "section.json").unlink(missing_ok=True)
    parts: list[Path | int] = []
    for p in pieces:
        if p.pause_ms:
            parts.append(p.pause_ms)
        parts.append(p.wav)
    tts.join_to_mp3(parts, d / "section.mp3", d, cfg["narration"]["mp3_bitrate"])
    (d / "joined.wav").unlink(missing_ok=True)
    base.write_atomic(d / "script.md", join_script_segments([(n, text)]))
    base.write_json(d / "section.json", {"section": n, "pieces": len(pieces),
                                         "seconds": round(sum(p.seconds for p in pieces), 3),
                                         "chars": sum(p.chars for p in pieces), "timing": False, "live": True})


# --- the asides ------------------------------------------------------------------------------

def _tok(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower().replace("’", "'"))


def locate(timing: dict, paras: dict[int, list[dict]], pid: str, sentence: int) -> dict | None:
    """Paragraph pid's sentence (0-based) in the timed chapter: {"sentence_id", "t", "segment"}, by
    matching the section's words as read with the timed script's words."""
    for n, ps in paras.items():
        ids = [p["id"] for p in ps]
        if pid in ids:
            break
    else:
        return None
    seg = next((s for s in timing["segments"] if s["id"] == f"seg{n:02d}"), None)
    if seg is None:
        return None
    live = []
    for p in ps:
        if p["id"] == pid:
            target = len(live) + len(" ".join(p["sentences"][:max(0, sentence)]).split())
        live += p["text"].split()
    timed = [(x["id"], w) for x in seg["sentences"] for w in x["words"]]
    a = [(i, t) for i, w in enumerate(live) if (t := _tok(w))]
    b = [(j, t) for j, (_, w) in enumerate(timed) if (t := _tok(w["w"]))]
    m = {}
    for blk in SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            m[a[blk.a + k][0]] = b[blk.b + k][0]
    if not m:
        return None
    after = [i for i in m if i >= target]
    j = m[min(after)] - (min(after) - target) if after else m[max(m)]
    j = max(0, min(len(timed) - 1, j))
    sid = timed[j][0]
    sent = next(x for x in seg["sentences"] if x["id"] == sid)
    return {"sentence_id": sid, "t": round(sent["start"], 3), "segment": n}


def place_asides(r: Run, timing: dict, paras: dict[int, list[dict]]) -> dict:
    """Give each live aside in 12_asks/asides.jsonl its place in the timed chapter: t and sentence_id
    (the sentence it interrupted; a check-in, the paragraph that came next), segment, and the restart's
    t and sentence_id. {"placed": n, "unplaced": [ids]}."""
    from ask import aside as ask_aside

    placed, unplaced = 0, []
    for a in ask_aside.load_asides(r.root):
        if a.get("voice") != "live":
            continue
        at = locate(timing, paras, a.get("paragraph") or "", int(a.get("sentence") or 0))
        if at is None:
            unplaced.append(a.get("id"))
            continue
        fields = dict(at)
        rs = a.get("restart")
        if isinstance(rs, dict) and rs.get("id"):
            back = locate(timing, paras, rs["id"], int(rs.get("sentence") or 0))
            if back:
                fields["restart"] = {**rs, "t": back["t"], "sentence_id": back["sentence_id"]}
        ask_aside.update_aside(r.root, a["id"], **fields)
        placed += 1
    return {"placed": placed, "unplaced": unplaced}


def status_audio(r: Run) -> None:
    """status.json's "audio": the chapter, so the server's audio route and the episode page find it."""
    p = r.path("status")
    try:
        status = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"run": r.name}
    except json.JSONDecodeError:
        status = {"run": r.name}
    status["audio"] = str(r.path("chapter_mp3").relative_to(r.root))
    status["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    base.write_json(p, status)


# --- the whole step, and in the background -----------------------------------------------------

def progress(r: Run, **fields) -> dict:
    p = live_dir(r) / PROGRESS
    old = base.read_json(p) or {}
    new = {**old, **fields, "updated": base.now_iso()}
    p.parent.mkdir(parents=True, exist_ok=True)
    base.write_json(p, new)
    return new


def archive(r: Run, cfg: dict, make_recognizer=None, references: bool = False, client=None) -> dict:
    """fetch, then build, with progress in runs/<run>/live/archive.json."""
    progress(r, state="fetching", step="fetching", error=None, result=None, started=base.now_iso())
    try:
        # Asked for as the session ends: OpenAI may take a moment to have the recording ready.
        deadline = time.time() + float(cfg["live"].get("archive_wait_seconds", 120))
        while True:
            try:
                got = fetch(r, cfg, client=client)
            except StageError:
                if time.time() > deadline:
                    raise
                got = None
            if got and (not got["missing"] or time.time() > deadline):
                break
            progress(r, state="waiting for the recording", step="fetching")
            time.sleep(float(cfg["live"].get("archive_retry_seconds", 10)))
        progress(r, fetched=got)
        result = build(r, cfg, make_recognizer, references,
                       on_step=lambda s: progress(r, state=s, step=s))
    except Exception as e:
        progress(r, state="failed", error=f"{type(e).__name__}: {e}",
                 detail=None if isinstance(e, StageError) else traceback.format_exc()[-2000:])
        raise
    summary = {k: result.get(k) for k in ("seconds", "pieces", "paragraphs", "skipped", "partial", "joined",
                                           "asides", "timing", "words")}
    progress(r, state="done", step="done", result=summary, audio=str(r.path("chapter_mp3").relative_to(r.root)))
    return result


def running(r: Run) -> bool:
    th = _RUNNING.get(str(r.root.resolve()))
    return bool(th and th.is_alive())


def start(r: Run, cfg: dict, references: bool = False, worker=None) -> dict:
    """The archive in a background thread, one at a time per run: {"started": true} or
    {"started": false, "reason"}."""
    key = str(r.root.resolve())
    with _LOCK:
        th = _RUNNING.get(key)
        if th and th.is_alive():
            return {"started": False, "reason": "This chapter is already being archived."}
        if not readings(r):
            return {"started": False, "reason": "Nothing in this chapter was read live."}
        work = worker or (lambda: archive(r, cfg, references=references))

        def go():
            try:
                work()
            except Exception:  # recorded in live/archive.json by archive()
                pass
        progress(r, state="starting", step="starting", error=None, result=None, started=base.now_iso())
        th = threading.Thread(target=go, name=f"live-archive-{r.name}", daemon=True)
        _RUNNING[key] = th
        th.start()
    return {"started": True}


def summary(result: dict) -> str:
    lines = [f"  chapter: {result['seconds'] / 60:.1f} min, {result['pieces']} of {result['paragraphs']} paragraphs, "
             f"{result.get('words', 0):,} words, timing {result.get('timing')}"]
    for s in result.get("skipped") or []:
        lines.append(f"  skipped {s['id']}: {s['reason']}")
    for j in result.get("joined") or []:
        lines.append(f"  joined {j['id']}: " + " + ".join(
            f"{p['live_id'][-6:]} {p['start']:.2f}-{p['end']:.2f} (from sentence {p['sentence_from']}, {p['cut_by']})"
            for p in j["parts"]))
    for p in result.get("partial") or []:
        lines.append(f"  partial {p['id']}: sentences {p['missing_sentences'][0]}-{p['missing_sentences'][1] - 1} never heard")
    a = result.get("asides") or {}
    lines.append(f"  asides placed: {a.get('placed', 0)}" + (f", unplaced {a['unplaced']}" if a.get("unplaced") else ""))
    return "\n".join(lines)
