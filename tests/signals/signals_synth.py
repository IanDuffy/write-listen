"""Synthetic GPT-Live recordings for the signals tests: harmonic tone bursts on two channels (the listener left,
over low room noise; the other voice right, digital zero between its bursts), and transcripts that match them."""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

RATE = 24000
HARMONICS = (1.0, 0.5, 0.33, 0.25)
AMP = 2000              # the listener's bursts
OTHER_AMP = 3000


def rms_db(amp: float = AMP) -> float:
    """dBFS of a burst of this amplitude (the harmonics' RMS)."""
    return 20 * np.log10(amp * np.sqrt(sum(a * a for a in HARMONICS) / 2) / 32768)


def tone(dur: float, f0: float = 150.0, amp: float = AMP, st_amp: float = 0.0, st_rate: float = 0.5,
         t0: float = 0.0) -> np.ndarray:
    """A voice-like tone: f0 with three harmonics; st_amp semitones of slow vibrato (on the absolute clock t0)."""
    t = np.arange(int(round(dur * RATE))) / RATE + t0
    f = f0 * 2 ** (st_amp * np.sin(2 * np.pi * st_rate * t) / 12)
    ph = 2 * np.pi * np.cumsum(f) / RATE
    return amp * sum(a * np.sin((h + 1) * ph) for h, a in enumerate(HARMONICS))


def expected_sd(spans, st_amp: float, st_rate: float = 0.5, frame: float = 0.04) -> float:
    t = np.concatenate([np.arange(a, b - frame / 2, frame) + frame / 2 for a, b in spans])
    return float(np.std(st_amp * np.sin(2 * np.pi * st_rate * t)))


def wav(path: Path, total: float, listener=(), other=(), *, noise: float = 20.0, bleed: float = 0.0,
        bleed_delay: float = 0.02, muted=(), seed: int = 1) -> Path:
    """listener / other: (start, end) or (start, end, {tone kwargs}). bleed: that fraction of the other voice
    reaches the listener's mic, bleed_delay later. muted: (start, end) where the listener channel is digital zero."""
    n = int(round(total * RATE))
    L = np.random.default_rng(seed).normal(0, noise, n) if noise else np.zeros(n)
    O = np.zeros(n)
    for spec in other:
        a, b, kw = (*spec, {})[:3]
        i = int(round(a * RATE))
        x = tone(b - a, **{"f0": 220.0, "amp": OTHER_AMP, **kw})
        O[i:i + len(x)] += x
    for spec in listener:
        a, b, kw = (*spec, {})[:3]
        i = int(round(a * RATE))
        x = tone(b - a, t0=a, **kw)
        L[i:i + len(x)] += x
    if bleed:
        d = int(round(bleed_delay * RATE))
        L[d:] += bleed * O[:n - d]
    for a, b in muted:
        L[int(round(a * RATE)):int(round(b * RATE))] = 0
    st = np.stack([L, O], axis=1)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.clip(np.round(st), -32768, 32767).astype("<i2").tobytes())
    return path


# One GPT-Live session's worth: the other voice, then the listener's turns (A flat, B lively with a pause, C cutting
# in, D a sound GPT-Live never transcribed, E with a short breath that is not a pause, F louder).
OTHER = [(0.5, 3.0), (6.0, 9.0), (14.5, 17.0), (19.0, 21.0), (26.0, 28.0)]
VIBRATO = {"st_amp": 4.0}
LISTENER = [(3.8, 5.0), (10.2, 11.2, VIBRATO), (12.0, 13.5, VIBRATO), (16.4, 18.0), (22.0, 22.3),
            (24.0, 24.6), (24.9, 25.5), (28.9, 30.4, {"amp": 2 * AMP})]
TOTAL = 32.0
# start_s, end_s, gap_ms, cut_in, dur_ms, speak_ms, pauses, longest_pause_ms
TURNS = [(3.8, 5.0, 800, False, 1200, 1200, 0, 0),
         (10.2, 13.5, 1200, False, 3300, 2500, 1, 800),
         (16.4, 18.0, -600, True, 1600, 1600, 0, 0),
         (22.0, 22.3, 1000, False, 300, 300, 0, 0),
         (24.0, 25.5, 3000, False, 1500, 1500, 0, 0),
         (28.9, 30.4, 900, False, 1500, 1500, 0, 0)]
B_SD = expected_sd([(10.2, 11.2), (12.0, 13.5)], 4.0)
# What GPT-Live wrote for them (D has no line): B in two pieces with a tag across them.
TEXTS = {0: ["I went to the park today."], 1: ["[inhale", "]Yeah, it was good, kind of quiet."],
         2: ["Oh wait, sorry."], 4: ["Um, I don't know."], 5: ["It was really great fun, honestly!"]}


def rows_for(t0_secs: float = 0.0, lag: float = 2.5, texts=None, turns=None) -> list[tuple[float, str, bool]]:
    """(seconds after the session row when the row is written, text, the turn's first piece): each piece of a
    turn spread over it, written lag after its speech ended (the transcript's lag), at one-second resolution
    like the real rows."""
    texts = TEXTS if texts is None else texts
    turns = TURNS if turns is None else turns
    out = []
    for k, pieces in texts.items():
        a, b = turns[k][0], turns[k][1]
        for i, p in enumerate(pieces):
            end = a + (b - a) * (i + 1) / len(pieces)
            out.append((int(t0_secs + end + lag), p, i == 0))
    return out


def jsonl(p: Path, recs: list[dict]) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return p


def iso(base: str, secs: float) -> str:
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(base) + timedelta(seconds=secs)).isoformat(timespec="seconds")


def guide_session(base: str, live_id: str, at: float, rows, guide_lines=True) -> list[dict]:
    """One GPT-Live session in a guide transcript: its session row at `at` seconds after `base`, then the rows
    (rows_for's), with a guide line before each turn's first piece."""
    out = [{"t": iso(base, at), "who": "session", "live_id": live_id, "voice": "gleam", "model": "gpt-live-1"}]
    for secs, text, first in rows:
        if guide_lines and first:
            out.append({"t": iso(base, at + max(0, secs - 1)), "who": "guide", "text": "Mm.", "mode": "live"})
        out.append({"t": iso(base, at + secs), "who": "listener", "text": text, "mode": "live"})
    return out


def study_session(base: str, mode: str, live_id: str, rows) -> list[dict]:
    out = [{"t": base, "s": 0.0, "who": "session", "mode": mode, "live_id": live_id, "voice": "cedar"}]
    for secs, text, first in rows:
        if first:
            out.append({"t": iso(base, secs - 1), "s": float(secs) - 0.6, "who": "interviewer", "text": "I see."})
        out.append({"t": iso(base, secs), "s": float(secs) + 0.37, "who": "listener", "text": text})
    return out + [{"t": iso(base, 40), "s": 40.0, "who": "session", "ended": "done"}]


BASE = "2026-11-21T10:15:00"
GUIDE = "20261121-101500"
INTRO = "intro-20261121-101000"


def visit_files(lst: Path, src: Path, *, intro_mp3: bool = True, bleed_g2: float = 0.0) -> Path:
    """A listener's visit: an intro (one session, its recording only as the interviewer's mp3) and a guide
    conversation of two GPT-Live sessions with a rest between (live_g1, live_g2), each recording in
    signals/audio/, and a library session (never stored). Transcripts dated an hour ago (not live)."""
    import os
    import time

    from signals import store
    from stages import tts
    rec = wav(src / "src.wav", TOTAL, LISTENER, OTHER)
    intro_rows = rows_for(texts={0: ["Hey, yes, this is Pooja."], 1: ["Sure, I'm ready to try it now."]})
    jsonl(lst / "study" / f"{INTRO}.jsonl", study_session("2026-11-21T10:10:00", "intro", "live_i", intro_rows))
    if intro_mp3:
        tts.ffmpeg(["-i", str(rec), "-c:a", "libmp3lame", "-q:a", "4", str(lst / "study" / "live_i.mp3")])
    convo = [{"t": BASE, "who": "session", "names": ["Hanne"], "returning": False}]
    convo += guide_session(BASE, "live_g1", 0, rows_for())
    convo.append({"t": iso(BASE, 40), "who": "page", "text": "[The guide stepped back.]"})
    convo += guide_session(BASE, "live_g2", 120, rows_for(texts={0: ["Back again, it's me."]}))
    jsonl(lst / "sessions" / f"{GUIDE}.jsonl", convo)
    jsonl(lst / "sessions" / "20261121-120000.jsonl",
          [{"t": "2026-11-21T12:00:00", "who": "session", "mode": "intro", "live_id": "lib_1"},
           {"t": "2026-11-21T12:00:09", "who": "listener", "text": "Play it."}])
    g2 = wav(src / "bleed.wav", TOTAL, LISTENER, OTHER, bleed=bleed_g2) if bleed_g2 else rec
    for lid, f in (("live_g1", rec), ("live_g2", g2)):
        p = store.audio_path(lst, lid)
        p.parent.mkdir(parents=True, exist_ok=True)
        os.link(f, p)
    old = time.time() - 3600
    for p in [*lst.glob("sessions/*.jsonl"), *lst.glob("study/*.jsonl")]:
        os.utime(p, (old, old))
    return lst
