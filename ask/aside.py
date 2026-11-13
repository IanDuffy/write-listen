"""The chapter as the guide is told it during an aside, where playback restarts, and the log.

A chapter can be asked about once it has a timed transcript (11_episode/timing.json, from the
episode page work): sentence ids and times come from there, paragraphs from the script
(episode.script, the same parse that made the ids), the outline from the brief, the research from
04_dossier.md and ledger.json. Everything the guide is told is built here and passed to the aside
agent as dynamic variables (stages.voice ASIDE_PLACEHOLDERS), the way onboarding passes the profile.

Each aside is logged to <run>/12_asks/asides.jsonl when it ends; topics the guide saves for a
future chapter go to <run>/12_asks/seeds.md as well.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from episode import script as ep_script
from episode.timing import nearest_sentence_boundary
from stages import guide
from stages.brief import parse_segments
from stages.common import dossier_entries, listener_profile

ASKS_DIR = "12_asks"
TIMING = "11_episode/timing.json"
RULES = ("sentence", "previous", "paragraph")
LOG_LOCK = threading.Lock()


@dataclass
class Sent:
    id: str
    seg: int        # index into Chapter.segments
    n: int          # 1-based, within its segment
    para: int       # paragraph within its segment
    start: float
    end: float
    words: list     # [{"w", "start", "end"}]


def can_ask(run: Path) -> bool:
    return (run / TIMING).exists()


def _first_para(text: str) -> str:
    for block in re.split(r"\n\s*\n", text.strip()):
        if block.strip() and not block.lstrip().startswith("#"):
            return " ".join(block.split())
    return ""


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.M | re.I)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


def chapter_title(run: Path) -> str:
    for name, prefix in (("03_angle.md", "# Angle:"), ("05_brief.md", "# Brief:")):
        p = run / name
        if p.exists():
            first = p.read_text(encoding="utf-8").lstrip().splitlines()[0]
            if first.startswith(prefix):
                return first[len(prefix):].strip().replace("*", "")
    return run.name


def _clock(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


class Chapter:
    def __init__(self, run: Path, cfg: dict):
        self.run, self.cfg = run, cfg
        timing = json.loads((run / TIMING).read_text(encoding="utf-8"))
        self.timing = timing
        self.duration = float(timing["duration"])
        paras = {x.id: x.para for s in ep_script.parse((run / "09_script_final.md").read_text(encoding="utf-8"))
                 for x in s.sentences}
        self.segments = [{"id": s["id"], "start": s["start"], "end": s["end"]} for s in timing["segments"]]
        self.sents: list[Sent] = []
        for si, s in enumerate(timing["segments"]):
            for k, x in enumerate(s["sentences"]):
                self.sents.append(Sent(x["id"], si, k + 1, paras.get(x["id"], 0), x["start"], x["end"], x["words"]))
        self.counts = [len(s["sentences"]) for s in timing["segments"]]
        brief = (run / "05_brief.md").read_text(encoding="utf-8") if (run / "05_brief.md").exists() else ""
        self.brief_segments = parse_segments(brief) if brief else []
        self.through_line = _first_para(_section(brief, "Through-line"))
        angle = (run / "03_angle.md").read_text(encoding="utf-8") if (run / "03_angle.md").exists() else ""
        self.angle = _first_para(angle)
        self.title = chapter_title(run)
        d = run / "04_dossier.md"
        self.entries = dossier_entries(d.read_text(encoding="utf-8")) if d.exists() else {}
        lp = run / "ledger.json"
        self.ledger = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}

    # --- where they are ------------------------------------------------------------------------

    def locate(self, t: float) -> int:
        """The interrupted sentence: the one being spoken at t. In the pause after a sentence, the
        next one (nothing of it has been heard); after the last, the last."""
        i = -1
        for k, s in enumerate(self.sents):
            if s.start <= t:
                i = k
            else:
                break
        if i < 0:
            return 0
        if t >= self.sents[i].end and i + 1 < len(self.sents):
            return i + 1
        return i

    def seg_title(self, si: int) -> str:
        return self.brief_segments[si]["title"] if si < len(self.brief_segments) else self.segments[si]["id"]

    def _purpose(self, si: int) -> str:
        if si >= len(self.brief_segments):
            return ""
        m = re.search(r"\*\*Purpose:\*\*\s*(.+)", self.brief_segments[si]["text"])
        return m.group(1).strip() if m else ""

    def outline_line(self, si: int) -> str:
        p = self._purpose(si)
        return f"Segment {si + 1}: {self.seg_title(si)}" + (f". {p}" if p else "")

    def words_between(self, a: float, b: float) -> str:
        """The script's words that start in [a, b), with paragraph breaks and segment headings."""
        out, last = [], None
        for s in self.sents:
            ws = [w["w"] for w in s.words if a <= w["start"] < b]
            if not ws:
                continue
            heading = f"\n\n(Segment {s.seg + 1}: {self.seg_title(s.seg)})\n\n"
            if last is None:
                if s.n == 1 and len(ws) == len(s.words):  # the window opens on a segment's first word
                    out.append(heading)
            elif s.seg != last[0]:
                out.append(heading)
            elif s.para != last[1]:
                out.append("\n\n")
            else:
                out.append(" ")
            out.append(" ".join(ws))
            last = (s.seg, s.para)
        return "".join(out).strip()

    # --- what the guide is told ----------------------------------------------------------------

    def context(self, t: float, earlier: list[dict] | None = None) -> dict[str, str]:
        a = self.cfg["ask"]
        t = min(max(t, 0.0), self.duration)
        i = self.locate(t)
        s = self.sents[i]
        nseg = len(self.segments)
        where = (f"Segment {s.seg + 1} of {nseg}, \"{self.seg_title(s.seg)}\": sentence {s.n} of "
                 f"{self.counts[s.seg]} in that segment. {t / 60:.1f} minutes into a "
                 f"{self.duration / 60:.1f}-minute chapter ({_clock(t)} of {_clock(self.duration)}). "
                 + (f"They have heard segments 1 to {s.seg} and this segment up to here; " if s.seg > 1 else
                    "They have heard segment 1 and this segment up to here; " if s.seg == 1 else
                    "They are in the first segment; ")
                 + (f"segments {s.seg + 2} to {nseg} are still to come." if s.seg + 2 < nseg else
                    f"segment {nseg} is still to come." if s.seg + 2 == nseg else "this is the last segment."))
        just = self.words_between(t - a["just_said_secs"], t) or "(nothing yet: they tapped at the very start)"
        horizon = t + a["coming_secs"]
        coming = self.words_between(t, horizon) or "(nothing: the chapter has ended)"
        later_segs = [k for k, seg in enumerate(self.segments) if seg["start"] >= horizon]
        cut = next((k for k, seg in enumerate(self.segments) if seg["start"] < horizon <= seg["end"]), None)
        later = ([f"The rest of segment {cut + 1}, then:"] if cut is not None and later_segs else []) + \
            [self.outline_line(k) for k in later_segs]
        chapter = "\n".join([f"Title: {self.title}", f"Angle: {self.angle}", f"Through-line: {self.through_line}",
                             "Outline:"] + [self.outline_line(k) for k in range(nseg)])
        values = {
            "identity": guide.identity(self.cfg) or "(No record of your name with this listener. Don't make a point of names.)",
            "profile": listener_profile(self.cfg),
            "chapter": chapter,
            "where": where,
            "just_said": just,
            "coming": coming,
            "later": "\n".join(later) or "(nothing after that: the chapter ends)",
            "dossier": self.dossier_text(s.seg),
            "ledger": self.ledger_text(),
            "earlier_asides": earlier_text(earlier or []),
        }
        # The script and dossier mark titles in markdown (*Gilead*); a voice would read the asterisks.
        return {k: v.replace("*", "") for k, v in values.items()}

    def dossier_text(self, si: int) -> str:
        """Entries for the current and next segment in full (the brief's Dossier: lines), one line each for the rest."""
        full = set()
        for k in (si, si + 1):
            if k < len(self.brief_segments):
                full |= set(self.brief_segments[k]["entries"])
        n_words = self.cfg["ask"]["dossier_line_words"]
        lines = []
        for n in sorted(self.entries):
            e = " ".join(self.entries[n].split())
            if n in full:
                lines.append(e)
                continue
            m = re.match(r"(\[D\d+\])\s*(\([^)]*\))?\s*(.*)", e)
            body = re.sub(r"\s*Source:.*$", "", m.group(3) if m else e)
            words = body.split()
            lines.append(f"{m.group(1) if m else ''} {' '.join(words[:n_words])}{'…' if len(words) > n_words else ''}".strip())
        return "\n".join(lines) or "(no dossier)"

    def ledger_text(self) -> str:
        rows = [f"{r['id']} (rank {r.get('rank')}, {r.get('type')}): {r.get('idea', '')}"
                + (f" Question: {r['research_question']}" if r.get("research_question") else "")
                for r in self.ledger.get("rows", [])]
        parked = [f"- {p}" if isinstance(p, str) else f"- {p.get('idea', p)}" for p in self.ledger.get("parked", [])]
        return "\n".join(rows + (["Parked ideas:"] + parked if parked else [])) or "(no ledger)"

    # --- where playback restarts ---------------------------------------------------------------

    def restart_point(self, t: float, seconds: float, rewind: str | None = None) -> dict:
        """Where the chapter picks up after an aside of `seconds` that began at t: under 30 s the
        interrupted sentence, to 2 minutes one sentence earlier, over that the paragraph's start
        (config ask.restart_bands_secs). `rewind` overrides. Never back across a segment's start:
        a segment begins after a held beat, the natural place to pick up."""
        short, long_ = self.cfg["ask"]["restart_bands_secs"]
        rule = rewind if rewind in RULES else ("sentence" if seconds < short else "previous" if seconds <= long_ else "paragraph")
        i = self.locate(t)
        j = i
        if rule == "previous" and i > 0 and self.sents[i - 1].seg == self.sents[i].seg:
            j = i - 1
        elif rule == "paragraph":
            while j > 0 and (self.sents[j - 1].seg, self.sents[j - 1].para) == (self.sents[i].seg, self.sents[i].para):
                j -= 1
        start = self.sents[j].start
        return {"t": round(nearest_sentence_boundary(self.timing, start, "before"), 3),
                "sentence_id": self.sents[j].id, "rule": rule}


def earlier_text(asides: list[dict]) -> str:
    """Earlier asides during this chapter, in brief: when, what they asked, what the guide said."""
    if not asides:
        return "(none)"
    out = []
    for a in asides:
        said = [x["text"] for x in a.get("transcript", []) if x.get("who") == "listener" and x.get("text", "").strip(". ")]
        reply = [x["text"] for x in a.get("transcript", []) if x.get("who") == "guide"]
        line = f"At {_clock(a.get('t', 0))} ({a.get('sentence_id', '')}): "
        line += f"you opened with \"{reply[0][:80]}\"; " if reply else ""
        line += f"they said \"{said[0][:200]}\"" if said else "they said nothing"
        if len(reply) > 1:
            line += f"; you answered \"{reply[1][:240]}\""
        out.append(line + f"; ended: {a.get('ended', '?')}.")
    return "\n".join(out)


# --- the log ---------------------------------------------------------------------------------

def asks_dir(run: Path) -> Path:
    return run / ASKS_DIR


def load_asides(run: Path) -> list[dict]:
    p = asks_dir(run) / "asides.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def append_aside(run: Path, record: dict) -> None:
    d = asks_dir(run)
    d.mkdir(exist_ok=True)
    with LOG_LOCK, open(d / "asides.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_aside(run: Path, aside_id: str, **fields) -> bool:
    """Fill in fields of a logged aside (its cost, once ElevenLabs has processed the call)."""
    p = asks_dir(run) / "asides.jsonl"
    with LOG_LOCK:
        if not p.exists():
            return False
        rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        hit = False
        for r in rows:
            if r.get("id") == aside_id:
                r.update(fields)
                hit = True
        if hit:
            tmp = p.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            tmp.replace(p)
        return hit


def save_seed(run: Path, topic: str, why: str, t: float, sentence_id: str) -> dict:
    d = asks_dir(run)
    d.mkdir(exist_ok=True)
    p = d / "seeds.md"
    head = "" if p.exists() else "# Seeds from asides\n\nTopics the guide saved for a future chapter during asides.\n"
    with LOG_LOCK, open(p, "a", encoding="utf-8") as f:
        f.write(f"{head}\n## {topic.strip()}\n\nWhy: {why.strip()}\n\nFrom an aside at {_clock(t)} ({sentence_id}), "
                f"{time.strftime('%Y-%m-%d %H:%M')}.\n")
    return {"topic": topic.strip(), "why": why.strip()}
