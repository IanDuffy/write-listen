"""The replay measurer (package 3): a GPT-Live recording and its transcript in, one record per listener turn out.

A recording is a stereo WAV, PCM16 at 24 kHz: the listener left (channel 0), the other voice right (channel 1;
the guide, or the interviewer in the intro and the debrief). It is read a window at a time (two hours of stereo
is 690 MB); an .mp3 (the interviewer's study/<live_id>.mp3) is decoded by ffmpeg to a temporary WAV first.

- Each frame (signals.recording.frame_ms) gets an RMS on both channels. The other voice is voiced at voice_rms:
  GPT-Live's silence is digital zero. The listener's channel is a mic with a room behind it: voiced at
  max(min_rms, floor_x * its floor), the floor a low percentile of its non-zero frames while the other voice is
  quiet (a muted mic sends digital zero, which is not the room). On a loudspeaker the other voice reaches the
  mic, so while it is voiced (and bleed_tail_ms after) a listener frame counts only at bleed_x times the bleed
  level, the median listener level under the other voice. Quiet shorter than fill_ms inside speech is speech;
  a voiced run shorter than min_run_ms is a click.
- A turn is the listener's runs within live.page.engaged_merge_ms of each other with the other voice silent
  between them, the page's own rule (countLine in page/live.js).
- Text: the transcript's listener rows of that GPT-Live session go to the turns by order and nearest time. A row
  is written when the page posts the line, some seconds after the speech ended (lag_s, or the lag within
  lag_search of it that fits the session's rows best), and its time on the session's clock is its time less the
  session row's. Audio turns with no row are dropped (sounds GPT-Live never transcribed); rows with no sound
  are counted.
"""
from __future__ import annotations

import tempfile
import time
import wave
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from stages.common import StageError

from . import fetch, record, store

LISTENER, OTHER = 0, 1
RECENT_SECS = 300      # a transcript changed this recently may still be live: its conversation waits (unless force)
BLOCK_S = 60           # seconds read at a time
DEFAULTS = {"frame_ms": 10, "voice_rms": 150, "min_rms": 200, "floor_x": 4, "floor_pct": 10, "bleed_x": 3,
            "bleed_tail_ms": 150, "fill_ms": 150, "min_run_ms": 80, "lag_s": 2.5, "lag_search": [-3, 4], "max_off_s": 6,
            "pitch": {"frame_ms": 40, "fmin": 75, "fmax": 400, "voicing": 0.5, "min_frames": 10, "outlier_st": 8}}


def settings(cfg: dict | None) -> dict:
    """signals.recording over DEFAULTS (record.settings merges one level only), with pause_ms and merge_ms."""
    over = dict(record.settings(cfg).get("recording") or {})
    out = {**DEFAULTS, **over, "pitch": {**DEFAULTS["pitch"], **(over.get("pitch") or {})}}
    out["pause_ms"] = record.settings(cfg)["pause_ms"]
    out["merge_ms"] = (((cfg or {}).get("live") or {}).get("page") or {}).get("engaged_merge_ms", 1500)
    return out


# --- the recording ---------------------------------------------------------------------------------------

class Audio:
    """A two-channel 16-bit recording, read a window at a time. Use it in a `with` (an mp3's decoded WAV is
    a temporary file)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._tmp = None
        self.wav = self.path
        if self.path.suffix.lower() == ".mp3":
            from stages import tts
            self._tmp = tempfile.TemporaryDirectory(prefix="wl-signals-")
            self.wav = Path(self._tmp.name) / "decoded.wav"
            try:
                tts.ffmpeg(["-i", str(self.path), "-c:a", "pcm_s16le", str(self.wav)])
            except StageError:
                self.close()
                raise
        try:
            with wave.open(str(self.wav), "rb") as w:
                self.rate, self.channels, self.width, self.frames = (w.getframerate(), w.getnchannels(),
                                                                     w.getsampwidth(), w.getnframes())
        except (wave.Error, EOFError, OSError) as e:
            self.close()
            raise StageError(f"{self.path.name} is not a WAV this can read: {e}") from None
        problem = ("is not 16-bit PCM" if self.width != 2 else
                   "has one channel: the listener and the other voice can't be told apart" if self.channels < 2
                   else None)
        if problem:
            self.close()
            raise StageError(f"{self.path.name} {problem}.")
        self.duration = self.frames / self.rate

    def read(self, i: int, j: int) -> np.ndarray:
        """Samples [i, j) of both channels, int16, shape (n, channels)."""
        i, j = max(0, i), min(self.frames, j)
        if j <= i:
            return np.zeros((0, self.channels), np.int16)
        with wave.open(str(self.wav), "rb") as w:
            w.setpos(i)
            x = np.frombuffer(w.readframes(j - i), np.int16)
        return x[: (len(x) // self.channels) * self.channels].reshape(-1, self.channels)

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """The True runs of a mask, as [start, end) frame indices."""
    if not len(mask):
        return []
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def clean(mask: np.ndarray, fill: int, min_run: int) -> np.ndarray:
    """Quiet shorter than `fill` frames between voiced runs becomes voiced, then runs shorter than `min_run` go."""
    m = mask.copy()
    rs = runs(m)
    for (_, b), (c, _) in zip(rs, rs[1:]):
        if c - b < fill:
            m[b:c] = True
    for a, b in runs(m):
        if b - a < min_run:
            m[a:b] = False
    return m


@dataclass
class Levels:
    """One recording's frames: RMS per frame on both channels, who is voiced, and the levels that decided it."""
    rate: int
    hop: int             # samples per frame
    secs: float          # seconds per frame
    lis: np.ndarray
    oth: np.ndarray
    lis_v: np.ndarray
    oth_v: np.ndarray
    floor: float
    thr: float
    bleed: float


def analyse(audio: Audio, R: dict) -> Levels:
    hop = max(1, int(round(audio.rate * R["frame_ms"] / 1000)))
    n = audio.frames // hop
    lis, oth = np.zeros(n, np.float32), np.zeros(n, np.float32)
    step = max(1, int(BLOCK_S * audio.rate) // hop)
    for k in range(0, n, step):
        m = min(step, n - k)
        x = audio.read(k * hop, (k + m) * hop).astype(np.float64)
        m = len(x) // hop
        if not m:
            break
        x = x[: m * hop].reshape(m, hop, audio.channels)
        r = np.sqrt((x ** 2).mean(axis=1))
        lis[k:k + m], oth[k:k + m] = r[:, LISTENER], r[:, OTHER]
    secs = hop / audio.rate
    fill = int(round(R["fill_ms"] / 1000 / secs))
    min_run = int(round(R["min_run_ms"] / 1000 / secs))
    oth_v = clean(oth >= R["voice_rms"], fill, min_run)
    quiet = lis[~oth_v & (lis > 0)]
    floor = float(np.percentile(quiet, R["floor_pct"])) if len(quiet) else 0.0
    thr = max(float(R["min_rms"]), R["floor_x"] * floor)
    under = lis[oth_v & (lis > 0)]
    bleed = float(np.median(under)) if len(under) else 0.0
    tail = int(round(R["bleed_tail_ms"] / 1000 / secs))
    near = oth_v.copy()
    for a, b in runs(oth_v):                 # the other voice's echo outlasts it a little
        near[b:b + tail] = True
    lis_v = (lis >= thr) & (~near | (lis >= R["bleed_x"] * bleed))
    lis_v = clean(lis_v, fill, min_run)
    return Levels(audio.rate, hop, secs, lis, oth, lis_v, oth_v, floor, thr, bleed)


# --- turns ------------------------------------------------------------------------------------------------

def turn_spans(L: Levels, merge_ms: float) -> list[tuple[int, int]]:
    """The listener's runs merged into turns: quiet under merge_ms between them, and the other voice silent."""
    merge = merge_ms / 1000 / L.secs
    out: list[list[int]] = []
    for a, b in runs(L.lis_v):
        if out and a - out[-1][1] < merge and not L.oth_v[out[-1][1]:a].any():
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def pitch_sd(x: np.ndarray, voiced: np.ndarray, hop: int, rate: int, P: dict) -> float | None:
    """Pitch spread in semitones over a turn's samples `x` (`voiced`: its energy frames of `hop` samples). Each
    pitch frame mostly voiced gets an f0 by normalized autocorrelation over fmin..fmax (the shortest period
    among the near-best peaks, so a doubled period doesn't win), its peak at least `voicing`; the spread is the
    standard deviation after dropping octave errors (outlier_st from the median). None with fewer than
    min_frames frames."""
    n = int(round(rate * P["frame_ms"] / 1000))
    k = len(x) // n
    keep = []
    for i in range(k):
        v = voiced[i * n // hop:(i + 1) * n // hop]
        if v.size and v.mean() >= 0.75:
            keep.append(i)
    if len(keep) < P["min_frames"]:
        return None
    f = x[: k * n].reshape(k, n)[keep].astype(np.float64)
    f -= f.mean(axis=1, keepdims=True)
    lo, hi = max(2, int(rate / P["fmax"])), min(n - 2, int(np.ceil(rate / P["fmin"])))
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    F = np.fft.rfft(f, nfft)
    ac = np.fft.irfft(F * np.conj(F), nfft)[:, : hi + 2]
    c = np.concatenate([np.zeros((len(f), 1)), np.cumsum(f ** 2, axis=1)], axis=1)
    lags = np.arange(lo - 1, hi + 2)
    den = np.sqrt(c[:, n - lags] * (c[:, n:n + 1] - c[:, lags]))
    r = np.where(den > 0, ac[:, lags] / np.where(den > 0, den, 1), 0.0)
    st = []
    for row in r:
        inner = row[1:-1]
        best = inner.max()
        if best < P["voicing"]:
            continue
        peak = (inner >= row[:-2]) & (inner >= row[2:]) & (inner >= max(P["voicing"], 0.9 * best))
        i = int(np.flatnonzero(peak)[0]) + 1 if peak.any() else int(inner.argmax()) + 1
        a, b, cc = row[i - 1], row[i], row[i + 1]
        d = a - 2 * b + cc
        lag = lags[i] + (0.5 * (a - cc) / d if d < 0 else 0.0)
        st.append(12 * np.log2(rate / lag / 100.0))
    if not st:
        return None
    st = np.array(st)
    st = st[np.abs(st - np.median(st)) <= P["outlier_st"]]
    return round(float(np.std(st)), 2) if len(st) >= P["min_frames"] else None


def measure_turns(audio: Audio, L: Levels, R: dict) -> list[dict]:
    """Raw measures of every audio turn in a recording (record.build's input), time order."""
    oruns = runs(L.oth_v)
    starts = [r[0] for r in oruns]
    pause = R["pause_ms"] / 1000 / L.secs
    out = []
    for a, b in turn_spans(L, R["merge_ms"]):
        k = bisect_left(starts, a) - 1           # the other voice's last run that began before the turn
        if k < 0:
            gap, cut = None, None
        else:
            end = oruns[k][1]
            gap, cut = (a - end) * L.secs * 1000, end > a
        quiet = [(c - a, d - a) for c, d in runs(~L.lis_v[a:b])]
        long = [d - c for c, d in quiet if d - c >= pause]
        v = L.lis_v[a:b]
        lv = L.lis[a:b][v].astype(np.float64)
        rms = float(np.sqrt((lv ** 2).mean())) if len(lv) else 0.0
        x = audio.read(a * L.hop, b * L.hop)[:, LISTENER]
        dur = (b - a) * L.secs * 1000
        out.append({"start_s": a * L.secs, "end_s": b * L.secs, "gap_ms": gap, "cut_in": cut, "dur_ms": dur,
                    "speak_ms": dur - sum(long) * L.secs * 1000, "pauses": len(long),
                    "longest_pause_ms": max(long) * L.secs * 1000 if long else 0,
                    "rms_db": float(20 * np.log10(rms / 32768)) if rms > 0 else None,
                    "pitch_sd_st": pitch_sd(x, v, L.hop, L.rate, R["pitch"])})
    return out


# --- text ---------------------------------------------------------------------------------------------------

def _when(r: dict) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(r.get("t")))
    except ValueError:
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def spoken_rows(session: dict) -> list[dict]:
    """The listener's spoken lines in one GPT-Live session, each with `at`: seconds on the session's clock
    when the row was written. Typed lines (a page note says so after them) are left out."""
    from voice.gpt_live import session as live_session
    typed = live_session.notes()["typed"].split("{text}")[0]     # the page's note after a typed line
    rows = session["rows"]
    t0, s0 = _when(session), session.get("s")
    out = []
    for k, r in enumerate(rows):
        text = str(r.get("text") or "").strip()
        if r.get("who") != "listener" or not text:
            continue
        if any(x.get("who") == "page" and str(x.get("text") or "").startswith(typed) and text in str(x["text"])
               for x in rows[k + 1:k + 6]):
            continue
        if s0 is not None and isinstance(r.get("s"), (int, float)):
            at = float(r["s"]) - float(s0)
        else:
            t = _when(r)
            if t is None or t0 is None:
                continue
            at = (t - t0).total_seconds()
        out.append({"text": text, "at": at})
    return out


def _align(spans: list[tuple[float, float]], ends: list[float], skip: float) -> tuple[list[int | None], float]:
    """(each row's turn or None, the total cost). A row never goes to an earlier turn than the row before it;
    the cost is the distance from a row's estimated speech end to its turn (and a tenth of the distance to the
    turn's end, so a row between two turns goes to the one it follows), `skip` for a row left out. Dynamic
    programming over (row, the last turn used)."""
    a = np.array([x for x, _ in spans])
    b = np.array([y for _, y in spans])
    D = np.full(len(spans) + 1, np.inf)        # state 0: no turn yet; s: the last row went to turn s - 1
    D[0] = 0.0
    hist = []
    for e in ends:
        cost = np.maximum(np.maximum(a - e, e - b), 0.0) + 0.1 * np.abs(e - b)
        take = np.minimum.accumulate(D)[1:] + cost   # to turn k from any state up to k + 1
        nd = D + skip
        took = take < nd[1:]
        nd[1:] = np.where(took, take, nd[1:])
        hist.append((D, took))
        D = nd
    s, total = int(np.argmin(D)), float(D.min())
    out: list[int | None] = []
    for prev, took in reversed(hist):
        if s > 0 and took[s - 1]:
            out.append(s - 1)
            s = int(np.argmin(prev[: s + 1]))
        else:
            out.append(None)
    return out[::-1], total


def assign(turns: list[dict], rows: list[dict], R: dict) -> list[int | None]:
    """Rows to turns, at the transcript lag that fits best: lag_s and the lags within lag_search of it, a quarter
    second apart (the session row's time and the recording's start are not the same instant, and how far the
    transcript runs behind varies), the nearest to lag_s on a tie."""
    spans = [(t["start_s"], t["end_s"]) for t in turns]
    if not spans or not rows:
        return [None] * len(rows)
    lag, (lo, hi) = float(R["lag_s"]), R["lag_search"]
    best = None
    for d in sorted(np.arange(lo, hi + 0.001, 0.25), key=abs):
        got, total = _align(spans, [r["at"] - lag - d for r in rows], float(R["max_off_s"]))
        if best is None or total < best[1] - 1e-9:
            best = (got, total)
    return best[0]


# --- a conversation and a listener ---------------------------------------------------------------------------

def measure_session(path: Path, session: dict, R: dict) -> dict:
    """One GPT-Live session's recording and transcript: {turns: [raw + text], untranscribed, unheard}."""
    with Audio(path) as audio:
        L = analyse(audio, R)
        turns = measure_turns(audio, L, R)
    rows = spoken_rows(session)
    got = assign(turns, rows, R)
    texts: dict[int, list[str]] = {}
    for row, k in zip(rows, got):
        if k is not None:
            texts.setdefault(k, []).append(row["text"])
    kept = [{**t, "text": record.join(texts[k])} for k, t in enumerate(turns) if k in texts]
    return {"turns": kept, "untranscribed": len(turns) - len(kept), "unheard": sum(1 for k in got if k is None)}


def conversation_records(listener: Path, conv: dict, cfg: dict, R: dict | None = None) -> dict:
    """Recording records of one conversation (not yet placed against the norm), what was missing, and the
    sessions not measured (no recording, or one that can't be read)."""
    R = R or settings(cfg)
    S = record.settings(cfg)
    raws, info = [], {"stem": conv["stem"], "part": conv["part"], "recordings": 0, "turns": 0,
                      "untranscribed": 0, "unheard": 0, "no_audio": [], "errors": []}
    unmeasured: set[str] = set()
    for sess in conv["sessions"]:
        src = fetch.audio_for(listener, sess["live_id"])
        t0 = _when(sess)
        if src is None or t0 is None:
            info["no_audio"].append(sess["live_id"])
            unmeasured.add(sess["live_id"])
            continue
        try:
            got = measure_session(src, sess, R)
        except StageError as e:
            info["errors"].append(f"{sess['live_id']}: {e}")
            unmeasured.add(sess["live_id"])
            continue
        info["recordings"] += 1
        info["untranscribed"] += got["untranscribed"]
        info["unheard"] += got["unheard"]
        raws += [(t0 + timedelta(seconds=t["start_s"]), sess["live_id"], t) for t in got["turns"]]
    raws.sort(key=lambda x: x[0])
    recs = [record.build(raw, raw["text"], stem=conv["stem"], part=store.part_of(conv["stem"]), source="recording",
                         turn=k, live_id=live_id, t=when.isoformat(timespec="milliseconds"), S=S)
            for k, (when, live_id, raw) in enumerate(raws)]
    info["turns"] = len(recs)
    return {"records": recs, "info": info, "unmeasured": unmeasured}


def _t(r: dict) -> datetime:
    return _when(r) or datetime.min


def run(listener: Path, cfg: dict, *, force: bool = False, now: float | None = None) -> dict:
    """Measure every conversation of a listener that has recordings and write signals/<stem>.jsonl: the guide's
    (every GPT-Live session of each), the intro and the debrief. All the listener's recording records are placed
    together in time order (the norm carries across the visit), then matched to that conversation's page records
    (store.match_page). A conversation whose transcript changed in the last five minutes is left alone unless
    force: it may still be live, and the page may be appending to its records. Returns
    {conversations: [info...], written: [stems], records: n}, and gone: "reset" or "deleted" when the folder was
    replaced or removed mid-run (a tester's reset or delete from /admin): it stops there and writes nothing more,
    so the old visit's records never land in the new folder."""
    listener = Path(listener)
    S, R = record.settings(cfg), settings(cfg)
    now = time.time() if now is None else now
    pool: dict[str, list[dict]] = {}
    write: list[str] = []
    infos = []
    out = {"listener": str(listener), "conversations": infos, "written": [], "records": 0}

    def gone() -> dict:
        return {**out, "gone": "reset" if listener.is_dir() else "deleted"}
    try:
        ident = fetch._ident(listener)
    except fetch.Gone:
        return gone()

    def still() -> bool:
        return fetch._still(listener, ident)
    for conv in fetch.conversations(listener):
        if not still():                          # measuring takes minutes: nothing more from a new folder
            return gone()
        stem = conv["stem"]
        try:
            recent = now - conv["path"].stat().st_mtime < RECENT_SECS
        except OSError:
            continue
        old = [r for r in store.rows(listener, stem) if r.get("source") == "recording"]
        if recent and not force:
            infos.append({"stem": stem, "part": conv["part"], "skipped": "changed in the last 5 minutes (it may "
                          "still be live); --force measures it anyway"})
            pool[stem] = old                      # its earlier records still count toward the norm
            continue
        got = conversation_records(listener, conv, cfg, R)
        infos.append(got["info"])
        # a session with no recording now (deleted, unreadable) keeps what an earlier run found in it
        recs = sorted(got["records"] + [r for r in old if r.get("live_id") in got["unmeasured"]], key=_t)
        pool[stem] = [{**r, "turn": k} for k, r in enumerate(recs)]
        if pool[stem] or store.path(listener, stem).exists():
            write.append(stem)
    placed = record.annotate(sorted((r for recs in pool.values() for r in recs), key=_t), S)
    by: dict[str, list[dict]] = {}
    for r in placed:
        by.setdefault(r["stem"], []).append(r)
    for stem in write:
        if not still():
            return gone()
        page = store.records(listener, stem, "page")
        recs = store.match_page(sorted(by.get(stem, []), key=lambda r: r["turn"]), page)
        try:
            store.write_recordings(listener, stem, recs, still=still)
        except FileNotFoundError:                 # reset or deleted as it wrote
            return gone()
        out["written"].append(stem)
        out["records"] += len(recs)
    return out
