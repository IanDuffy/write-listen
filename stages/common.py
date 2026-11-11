"""Shared plumbing: config, run folders, file guards, the call log, prompts.

Every stage reads files from a run folder and writes one new file. These helpers
enforce the two rules from the build doc: a stage refuses to run if its input is
missing, and never overwrites an existing output without --force.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
RUNS_DIR = ROOT / "runs"
CONFIG_PATH = ROOT / "config.yaml"


_LOG_LOCK = threading.Lock()  # research agents log from several threads


class StageError(Exception):
    """A stage cannot run. The message is shown to the user as-is."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# Every file a stage reads or writes, relative to the run folder.
PATHS = {
    "source_dir": "00_source",
    "transcript_pdf": "00_source/transcript.pdf",
    "transcript": "00_source/transcript.txt",
    "map": "01_map.md",
    "angles": "02_angles.md",
    "angle": "03_angle.md",
    "ledger": "ledger.json",
    "ledger_md": "ledger.md",
    "research_dir": "04_research",
    "ledger_update": "04_research/ledger_update.md",
    "dossier_hash": "04_research/dossier.sha256",  # the dossier as research or ledger-update last wrote it
    "dossier": "04_dossier.md",
    "brief": "05_brief.md",
    "segments_dir": "06_segments",
    "script_draft": "07_script_draft.md",
    "coverage": "07b_coverage.md",
    "edit_notes": "08_edit_notes.md",
    "script_final": "09_script_final.md",
    "audio_dir": "10_audio",
    "chapter_mp3": "10_audio/chapter.mp3",
    "baseline_dir": "baseline",
    "baseline_script": "baseline/script.md",
    "baseline_mp3": "baseline/chapter.mp3",
    "compare_dir": "compare",
    "report": "report.md",
    "log": "log.jsonl",
}


@dataclass
class Run:
    """A run folder, e.g. runs/beauty-scarry."""

    name: str
    root: Path
    force: bool = False
    yes: bool = False  # pre-approve the cost guard

    @classmethod
    def open(cls, name: str, *, force: bool = False, yes: bool = False, create: bool = False,
             runs_dir: Path | None = None) -> "Run":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise StageError(f"Run name {name!r} must be letters, digits, '.', '_' or '-'.")
        root = (runs_dir or RUNS_DIR) / name
        if not root.exists():
            if not create:
                raise StageError(
                    f"Run folder {root} does not exist. Create it with: python cli.py init {name}"
                )
            root.mkdir(parents=True)
        return cls(name=name, root=root, force=force, yes=yes)

    def path(self, key: str) -> Path:
        return self.root / PATHS[key]

    def segment_path(self, n: int) -> Path:
        return self.path("segments_dir") / f"seg{n:02d}.md"

    # --- guards -----------------------------------------------------------

    def need(self, key_or_path: str | Path) -> Path:
        """Return an input path, or stop if it is missing or empty."""
        p = self.path(key_or_path) if isinstance(key_or_path, str) else key_or_path
        if not p.exists():
            raise StageError(f"Missing input: {p.relative_to(self.root)}. Run the earlier stage first.")
        if p.is_file() and p.stat().st_size == 0:
            raise StageError(f"Input is empty: {p.relative_to(self.root)}.")
        return p

    def read(self, key_or_path: str | Path) -> str:
        return self.need(key_or_path).read_text(encoding="utf-8")

    def check_writable(self, key_or_path: str | Path) -> Path:
        """Stop if the output already exists, unless --force was given."""
        p = self.path(key_or_path) if isinstance(key_or_path, str) else key_or_path
        if p.exists() and not self.force:
            raise StageError(
                f"Output already exists: {p.relative_to(self.root)}. Use --force to overwrite."
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, key_or_path: str | Path, text: str) -> Path:
        p = self.check_writable(key_or_path)
        if not text.endswith("\n"):
            text += "\n"
        p.write_text(text, encoding="utf-8")
        return p

    # --- log ----------------------------------------------------------------

    def log(self, record: dict) -> None:
        """Append one record to log.jsonl. Never pass secrets in here."""
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _LOG_LOCK, open(self.path("log"), "a", encoding="utf-8") as f:
            f.write(line)

    def read_log(self) -> list[dict]:
        p = self.path("log")
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


# --- prompts ------------------------------------------------------------------

def load_prompt(name: str, **values: str) -> str:
    """Load prompts/<name>.md and fill {{placeholders}}.

    Everything above the first line that is exactly '---' is notes for Ian
    (the inputs the prompt expects) and is not sent to the model.
    A placeholder with no value is an error, so a typo cannot silently send
    a prompt with a hole in it.
    """
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    parts = re.split(r"^---[ \t]*$", text, maxsplit=1, flags=re.M)
    body = parts[1] if len(parts) == 2 else parts[0]

    def fill(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise StageError(f"Prompt {name}.md uses {{{{{key}}}}} but no value was given.")
        return str(values[key])

    return re.sub(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}", fill, body).strip() + "\n"


def parse_json(run: "Run", text: str, label: str) -> dict:
    """The JSON a structured-output call returned. If it does not parse, the raw text is
    saved as _partial_<label>.json and the stage stops."""
    t = text.strip()
    if t.startswith("```"):  # tolerate a fenced block
        t = re.sub(r"^```\w*\s*|\s*```$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError as e:
        partial = run.root / f"_partial_{label}.json"
        partial.write_text(text, encoding="utf-8")
        raise StageError(f"{label}: the model's output is not valid JSON ({e.msg}); saved to {partial.name}.") from None
    if not isinstance(data, dict):
        raise StageError(f"{label}: expected a JSON object, got {type(data).__name__}.")
    return data


# --- text helpers ----------------------------------------------------------

SEGMENT_MARKER = re.compile(r"^<!--\s*seg(\d+)\s*-->\s*$", re.M)
# A dossier marker after a sentence, on one line: [D12], [D03, D07], [D3-D5], [D3; D7]. Only marker syntax
# is matched, so a '[D12' left unclosed never swallows the narration that follows it.
MARKER = re.compile(r"[ \t]*\[D\d+(?:[ \t]*[,;\-–—][ \t]*D?\d+)*\]")


def word_count(text: str) -> int:
    """Words a listener would hear: no claim markers, segment markers or markdown."""
    return len(spoken_text(text).split())


def spoken_text(text: str) -> str:
    """Strip everything that is for Ian, not for the ear."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = MARKER.sub("", text)  # [D03], [D03, D07], [D3-D5], [D3; D7]
    text = re.sub(r"^#+\s.*$", "", text, flags=re.M)  # stray headings
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_script_segments(script: str) -> list[tuple[int, str]]:
    """Split an assembled script on <!-- segNN --> markers."""
    marks = list(SEGMENT_MARKER.finditer(script))
    if not marks:
        return [(1, script.strip())]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(script)
        out.append((int(m.group(1)), script[m.end():end].strip()))
    # Text above the first marker (say, an opening Ian added by hand) belongs to the first segment.
    lead = script[:marks[0].start()].strip()
    if lead:
        out[0] = (out[0][0], f"{lead}\n\n{out[0][1]}".strip())
    return out


def join_script_segments(segments: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"<!-- seg{n:02d} -->\n\n{body.strip()}" for n, body in segments) + "\n"


def parse_range(spec: str | None, available: list[int]) -> list[int]:
    """'1-2' or '1,3,5' -> list of ints. None -> all available."""
    if not spec:
        return available
    out: list[int] = []
    try:
        for part in re.sub(r"(?i)seg(ment)?s?\s*", "", spec).split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                if int(b) < int(a):
                    raise ValueError
                out.extend(range(int(a), int(b) + 1))
            elif part:
                out.append(int(part))
    except ValueError:
        raise StageError(f"Bad --segments value {spec!r}; use e.g. 1-2 or 1,3.") from None
    if not out:
        raise StageError(f"--segments {spec!r} selects nothing.")
    bad = [n for n in out if n not in available]
    if bad:
        raise StageError(f"Segment(s) {bad} not in the brief (has {available}).")
    return out


# --- dossier entries ----------------------------------------------------------
#
# An entry starts a line with its number, then its tags in parentheses:
#   [D07] (L01, quote, tier 1, verbatim: yes) "Exact words." Plato, Symposium 211a. Source: title, URL
#   [D12] (L02, L05, claim, tier 2) A fact. Source: title, URL
#   [D15] (L04, claim, tier 1, corrected) The episode says X. In fact Y. Source: title, URL
#   [D20] (L03, connection, L05) How the two ideas link.
# Ledger rows before the kind own the entry (several after a merge of exact
# duplicates); rows after a connection's kind are the rows it links to.

# '[D12]', '**[D12]**', '- [D12]' or '1. [D12]' at the start of a line.
DOSSIER_ENTRY = re.compile(r"^[ \t]*(?:[-*]|\d+\.)?[ \t]*\**\[D0*(\d+)\]\**", re.M)
KINDS = ("claim", "material", "quote", "tension", "connection")
VERDICTS = ("confirmed", "corrected", "unverified")


def entry_id(n: int) -> str:
    return f"D{n:02d}"


def row_id(n: int) -> str:
    return f"L{n:02d}"


def entry_numbers(text: str) -> list[int]:
    """'D3, D07', 'D3-D5', '[D03][D07]', 'D3; D7' -> sorted unique entry numbers, ranges expanded."""
    nums: set[int] = set()
    for a, b in re.findall(r"\bD0*(\d+)(?:[ \t]*[-–—][ \t]*D?0*(\d+))?", text):  # a range stays on one line
        lo, hi = int(a), int(b or a)
        if hi < lo or hi - lo > 200:
            hi = lo
        nums.update(range(lo, hi + 1))
    return sorted(nums)


def parse_tags(group: str) -> dict:
    """'L01, quote, tier 1, verbatim: yes' -> {'rows': ['L01'], 'kind': 'quote', 'tier': 1,
    'verbatim': 'yes', 'verdict': None, 'links': []}. Unknown words are ignored."""
    out = {"rows": [], "kind": None, "tier": None, "verbatim": None, "verdict": None, "links": []}
    for part in re.split(r"[,;]", group):
        p = part.strip().strip("*_").strip().lower()
        if not p:
            continue
        if m := re.fullmatch(r"l0*(\d+)", p):
            (out["links"] if out["kind"] else out["rows"]).append(row_id(int(m.group(1))))
        elif p in KINDS:
            out["kind"] = p
        elif m := re.fullmatch(r"(?:tier\s*:?\s*)?(episode)|tier\s*:?\s*([1-4])", p):
            out["tier"] = "episode" if m.group(1) else int(m.group(2))
        elif m := re.fullmatch(r"verbatim\s*:?\s*(yes|no)", p):
            out["verbatim"] = m.group(1)
        elif m := re.fullmatch(r"(?:verdict\s*:?\s*)?(confirmed|corrected|unverified)\W*", p):
            out["verdict"] = m.group(1)
    return out


def format_tags(t: dict) -> str:
    parts = list(t.get("rows") or [])
    parts.append(t.get("kind") or "claim")
    parts += list(t.get("links") or [])
    if t.get("tier") is not None:
        parts.append(f"tier {t['tier']}")
    if t.get("verbatim"):
        parts.append(f"verbatim: {t['verbatim']}")
    if t.get("verdict"):
        parts.append(t["verdict"])
    return "(" + ", ".join(parts) + ")"


ENTRY_HEAD = re.compile(r"^[ \t]*(?:[-*]|\d+\.)?[ \t]*\**\[D0*\d+\]\**[ \t]*\(([^)]*)\)")


def entry_tags(entry: str) -> dict:
    """The tags of one dossier entry, as returned by dossier_entries."""
    m = ENTRY_HEAD.match(entry)
    return parse_tags(m.group(1)) if m else parse_tags("")


def entries_section(dossier: str) -> str:
    """The '## Entries' section of a dossier (its ### subsections included), or the
    whole text if there is no such heading. Citations elsewhere are not entries."""
    m = re.search(r"^##\s+Entries\b.*$", dossier, re.M | re.I)
    if not m:
        return dossier
    nxt = re.search(r"^##\s", dossier[m.end():], re.M)
    return dossier[m.end(): m.end() + nxt.start() if nxt else None]


def dossier_entries(dossier: str) -> dict[int, str]:
    """Map entry number -> the full entry text (tags, text, source)."""
    dossier = entries_section(dossier)
    marks = list(DOSSIER_ENTRY.finditer(dossier))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(dossier)
        # An entry also ends at the next heading.
        chunk = dossier[m.start():end]
        h = re.search(r"^#{1,6}\s", chunk, re.M)
        out[int(m.group(1))] = (chunk[:h.start()] if h else chunk).strip()
    return out
