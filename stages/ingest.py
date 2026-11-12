"""Stage 0: transcript PDF (or text) -> 00_source/transcript.txt

Extracts text, repairs ligature glyphs, and strips what a web page printed to
PDF carries along: page headers and footers, URLs, site chrome and
related-article lists. Every line removed, and every glyph repair, is written
to 00_source/removed_lines.txt so Ian can check nothing real was cut.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .common import Run, StageError

# Lines that are site chrome when they stand alone on a line (case-insensitive).
CHROME_LINES = {
    "share", "save", "listen", "subscribe", "sign in", "log in", "give a gift", "sign up",
    "share this article", "share this episode", "save this article", "save this episode",
    "more from the permanent questions", "related podcasts", "related", "most popular",
    "explore more topics", "about the author", "about the authors", "read more", "show more",
    "listen and subscribe", "listen and subscribe here", "advertisement", "play",
    "photo illustration", "illustration", "the atlantic", "close", "menu", "search", "popular",
    "latest", "newsletters", "sponsor content", "skip to content", "transcript",
    "discuss", "view discussion", "view comments", "comments", "follow", "sign in subscribe",
}

# Site chrome that carries a count, a promo or a credit, so it never matches a fixed line.
# Each pattern matches the whole shape of the line, so a wrapped line of dialogue that
# mentions Spotify, a subscription or the Atlantic Ocean is never mistaken for chrome.
_PLATFORM = r"(Apple Podcasts|Spotify|YouTube|Overcast|Pocket Casts|Amazon Music|iHeartRadio)"
CHROME_PATTERNS = [
    re.compile(r"^(share|discuss|comments?|save)\s+\d+$", re.I),                 # "SHARE 66"
    # "Subscribe here:  Apple Podcasts | Spotify | YouTube": capitalized verb, two or more platforms.
    re.compile(rf"^(Listen|Subscribe|Follow)\b[^.!?]{{0,40}}:\s*{_PLATFORM}(\s*\|\s*{_PLATFORM})+\s*\|?$"),
    # "Give an Atlantic subscription, plus a free tote.*": the offer, or a footnote asterisk, ends it.
    re.compile(r"\b(give|gift)\b.{0,60}\bsubscription\b.{0,40}(\bplus a free\b[^.!?]*[.!?]?\*?|\*)$", re.I),
    # Photo credit: the brackets hold only the credit, e.g. "(The Atlantic)" or "(Getty / AP)".
    re.compile(r"^[^:()]{1,80}\((The Atlantic|Getty Images|Getty|AP|Reuters|Alamy|Shutterstock)"
               r"(\s*/\s*[A-Z][\w .]{0,30})*\)$"),
]
# Buttons printed after the dateline: "NOVEMBER 8, 2026 DISCUSS SAVE" keeps only the date.
UI_SUFFIX = re.compile(r"(\s+(DISCUSS|SAVE|SHARE|LISTEN|COMMENTS?))+$")

# Ligature characters that have Unicode code points: write them out as letters.
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "ﬅ": "st", "ﬆ": "st"}
PUA = re.compile(r"[-]")

# A heading that starts a trailing block of related links. Everything from the
# first such heading in the final 15% of the document (at least its last 60
# lines) to the end is dropped. To count as a heading the line must start with
# a capital, not be a speaker turn, not end like a sentence, and follow a line
# that ended a sentence (so a hard-wrapped line of dialogue never matches).
TAIL_MARKERS = re.compile(
    r"^(more from|related podcasts|related|most popular|explore more topics|"
    r"about the author|listen and subscribe|you may also like|recommended reading)\b",
    re.I,
)

PRINT_HEADER = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(\s*[AP]M)?\b", re.I)
PAGE_COUNTER = re.compile(r"^(page\s+)?\d+\s*(/|of)\s*\d+$", re.I)
URL = re.compile(r"(https?://|www\.)\S+", re.I)
SPEAKER = re.compile(r"^[A-Z][A-Za-z.'\- ]{1,40}:\s")


def is_chrome(line: str) -> bool:
    """A UI label (capitalized; a wrapped 'listen.' is dialogue), share count, promo or photo credit.

    No speaker-turn exemption: chrome like 'Subscribe here: Apple Podcasts' looks like 'Name: ...'.
    The patterns are narrow enough not to match a line of dialogue.
    """
    # Only a trailing colon is stripped: a UI label never ends in a period, but a wrapped
    # sentence can ("...a staff writer at" / "The Atlantic.").
    if line[:1].isupper() and line.lower().rstrip(":") in CHROME_LINES:
        return True
    # A platform list on a line of its own, or wrapped ("Apple Podcasts | Spotify |"): only
    # platform names, joined by at least one pipe.
    parts = [p.strip() for p in line.split("|")]
    if "|" in line and any(parts) and all(re.fullmatch(_PLATFORM, p) for p in parts if p):
        return True
    return len(line) < 100 and any(p.search(line) for p in CHROME_PATTERNS)


def is_tail_heading(line: str, prev: str) -> bool:
    return bool(
        line and len(line) < 60 and line[0].isupper() and TAIL_MARKERS.match(line)
        and not SPEAKER.match(line)
        and not re.search(r"[.,;:!?]$", line)
        and (not prev or re.search(r"[.!?\"'”’)]$", prev))
    )


# Words a lone 'Th' glyph is trusted to start when there is too little evidence to judge.
COMMON_TH = {"the", "that", "this", "these", "those", "there", "their", "they", "them", "then",
             "than", "thank", "thanks", "think", "thing", "things", "though", "through", "thus"}


def repair_glyphs(text: str) -> tuple[str, list[str]]:
    """Return (text, notes). Writes out ligature characters and restores 'Th' ligatures.

    Web fonts (The Atlantic's among them) draw the capital 'Th' ligature from a
    private-use code point, because Unicode has none, so extraction yields an
    invisible character and 'These' reads 'ese'. A code point counts as 'Th' when
    most of its uses start a word, and most of those form a word ('th' + the
    letters after) that also appears spelled out in the text; then each use followed
    by a lowercase letter becomes 'Th' (inside a word it is listed for checking).
    With fewer than 3 word-initial uses there is too little to judge the code point,
    so each word-initial use is restored only if it forms a common (COMMON_TH) or
    spelled-out word, and each is listed for checking. Every other use, and every
    use of an unrecognized code point (icon fonts, 'ft' or 'fl' ligatures), is
    dropped and listed. Nothing is changed silently.
    """
    notes: list[str] = []
    n_lig = sum(text.count(k) for k in LIGATURES)
    for k, v in LIGATURES.items():
        text = text.replace(k, v)
    if n_lig:
        notes.append(f"[glyph] {n_lig} ligature characters (ff, fi, ffi...) written out as letters")

    vocab = {w for w in re.findall(r"(?<![-])\b[^\W\d_]+", text) if w[0].islower()}
    letter = re.compile(r"[^\W\d_]")

    def ctx(pos: int) -> str:
        return "..." + PUA.sub("<?>", text[max(0, pos - 25):pos + 25]).replace("\n", " ") + "..."

    for ch in sorted(set(PUA.findall(text))):
        code = f"U+{ord(ch):04X}"
        uses = list(re.finditer(re.escape(ch) + r"([^\W\d_]*)", text))
        lower = [m for m in uses if m.group(1)[:1].islower()]
        initial = [m for m in lower if m.start() == 0 or not letter.match(text[m.start() - 1])]
        known = [m for m in initial if f"th{m.group(1).lower()}" in vocab]
        few = len(initial) < 3
        if few:
            # Too little to judge the code point: restore each word-initial use on its own when
            # it forms a common or spelled-out word, and list every one for checking.
            restore = {m.start() for m in initial
                       if f"th{m.group(1).lower()}" in COMMON_TH or f"th{m.group(1).lower()}" in vocab}
        else:
            is_th = len(initial) >= 0.5 * len(uses) and len(known) >= 0.6 * len(initial)
            restore = {m.start() for m in lower} if is_th else set()
        dropped = [m for m in uses if m.start() not in restore]
        if restore:
            notes.append(f"[glyph] {code} restored as 'Th' ({len(restore)} times; {len(known)} of "
                         f"{len(initial)} word-initial uses form words spelled out elsewhere)")
            notes.extend(f"[glyph] {code} restored as 'Th' on little evidence, check: {ctx(m.start())}"
                         for m in initial if few and m.start() in restore)
            notes.extend(f"[glyph] {code} restored as 'Th' inside a word, check: {ctx(m.start())}"
                         for m in lower if m not in initial and m.start() in restore)
            label = f"[glyph] {code} dropped, check:"
        else:
            notes.append(f"[glyph] {code} unrecognized, dropped ({len(uses)} times):")
            label = f"[glyph] {code} dropped:"
        notes.extend(f"{label} {ctx(m.start())}" for m in dropped[:10])
        if len(dropped) > 10:
            notes.append(f"[glyph] {code} dropped {len(dropped) - 10} more times")
        text = re.sub(re.escape(ch), lambda m: "Th" if m.start() in restore else "", text)
    return text, notes


def extract_pdf_text(pdf: Path) -> list[str]:
    """Return the text of each page. pdftotext keeps reading order best; pypdf is the fallback."""
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"],
                             capture_output=True, text=True, check=True).stdout
        pages = out.split("\f")
    else:
        from pypdf import PdfReader

        pages = [p.extract_text() or "" for p in PdfReader(str(pdf)).pages]
    pages = [p for p in pages if p.strip()]
    if not pages:
        raise StageError(f"No text found in {pdf.name}. Is it a scanned image? (OCR is out of scope.)")
    return pages


def clean(pages: list[str]) -> tuple[str, list[str]]:
    """Return (clean text, removed lines)."""
    removed: list[str] = []
    n_pages = len(pages)

    # Short lines that recur at the top or bottom of many pages are running
    # headers or footers. Speaker turns ("Scarry: Yes.") never count.
    per_page = Counter()
    for page in pages:
        body = [ln.strip() for ln in page.splitlines() if ln.strip()]
        edges = {ln for ln in body[:3] + body[-3:] if len(ln) < 100 and not SPEAKER.match(ln)}
        for line in edges:
            per_page[line] += 1
    repeated = {ln for ln, c in per_page.items() if n_pages >= 3 and c >= max(3, n_pages // 2)}

    lines: list[str] = []
    for page in pages:
        lines.extend(ln.strip() for ln in page.splitlines())
        lines.append("")  # a page break is a paragraph-break candidate

    nonblank = [i for i, ln in enumerate(lines) if ln and ln not in repeated]
    if nonblank:
        cutoff = nonblank[max(0, min(int(len(nonblank) * 0.85), len(nonblank) - 60))]
        prev = ""
        for i in range(len(lines)):
            ln = lines[i]
            if i >= cutoff and is_tail_heading(ln, prev):
                removed.extend(f"[trailing related block] {ln}" for ln in lines[i:] if ln)
                lines = lines[:i]
                break
            # Same chrome test as the kept loop below: a wrapped "follow." is dialogue in both.
            if ln and ln not in repeated and not is_chrome(ln) and not URL.search(ln):
                prev = ln

    kept: list[str] = []
    for line in lines:
        if not line:
            kept.append("")
            continue
        reason = None
        if line in repeated:
            reason = "repeated header/footer"
        elif PRINT_HEADER.match(line):
            reason = "print header"
        elif PAGE_COUNTER.match(line):
            reason = "page counter"
        elif URL.fullmatch(line) or (URL.search(line) and len(URL.sub("", line).strip()) < 15):
            reason = "url"
        elif is_chrome(line):
            reason = "site chrome"
        if reason:
            removed.append(f"[{reason}] {line}")
            continue
        if line.isupper() and UI_SUFFIX.search(line):
            removed.append(f"[site chrome] {UI_SUFFIX.search(line).group(0).strip()} (from: {line})")
            line = UI_SUFFIX.sub("", line)
        kept.append(URL.sub("", line).rstrip() if URL.search(line) else line)

    return reflow(kept), removed


def reflow(lines: list[str]) -> str:
    """Join hard-wrapped lines into paragraphs. A blank line or a speaker label starts a new one.

    A word split by a hyphen at a line end ('Dio-' / 'tima') is re-joined without
    the hyphen only when the joined word appears elsewhere in the text; otherwise
    it is a real compound ('well-' / 'known') and keeps its hyphen.
    """
    vocab = {w.lower() for ln in lines for w in re.findall(r"[A-Za-z']+", ln)}
    paras: list[str] = []
    cur = ""
    for line in lines:
        if not line:
            if cur:
                paras.append(cur)
                cur = ""
            continue
        if cur and SPEAKER.match(line):
            paras.append(cur)
            cur = line
        elif cur.endswith("-") and not cur.endswith(" -") and line[:1].islower():
            head = re.search(r"([A-Za-z']+)-$", cur)
            tail = re.match(r"[A-Za-z']+", line)
            joined = (head.group(1) + tail.group(0)).lower() if head and tail else ""
            cur = cur[:-1] + line if joined in vocab else cur + line
        else:
            cur = f"{cur} {line}" if cur else line
    if cur:
        paras.append(cur)
    # A paragraph split by a page break mid-sentence: join it back.
    merged: list[str] = []
    for p in paras:
        if merged and not re.search(r"[.!?:\"'”’)\]]$", merged[-1]) and p[:1].islower():
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in merged) + "\n"


def run(r: Run, cfg: dict, source: str | None = None) -> Path:
    src_dir = r.path("source_dir")
    src_dir.mkdir(exist_ok=True)
    out = r.check_writable("transcript")

    raw_txt = src_dir / "transcript_raw.txt"
    use = None
    if source:  # an explicit --source is what gets ingested
        sp = Path(source).expanduser()
        if not sp.exists():
            raise StageError(f"Source file not found: {sp}")
        if sp.suffix.lower() == ".pdf":
            use = r.path("transcript_pdf")
        elif sp.suffix.lower() in (".txt", ".md"):
            use = raw_txt
        else:
            raise StageError("Source must be a .pdf, .txt or .md file.")
        if sp.resolve() != use.resolve():
            shutil.copyfile(sp, use)
    elif r.path("transcript_pdf").exists():
        use = r.path("transcript_pdf")
    elif raw_txt.exists():
        use = raw_txt

    if use is not None and use.suffix == ".pdf":
        pages = extract_pdf_text(use)
    elif use is not None:
        pages = use.read_text(encoding="utf-8").split("\f")
    else:
        raise StageError(
            "Missing input: put the transcript at 00_source/transcript.pdf, or pass "
            "--source path/to/transcript.pdf"
        )

    repaired, glyph_notes = repair_glyphs("\f".join(pages))
    pages = repaired.split("\f")
    text, removed = clean(pages)
    out.write_text(text, encoding="utf-8")
    (src_dir / "removed_lines.txt").write_text("\n".join(glyph_notes + removed) + "\n", encoding="utf-8")
    words = len(text.split())
    print(f"  {len(pages)} pages -> {words:,} words, {len(removed)} lines removed "
          f"(see 00_source/removed_lines.txt)")
    for note in glyph_notes:
        print(f"  {note}")
    r.log({"stage": "ingest", "provider": "none", "pages": len(pages), "words": words,
           "removed_lines": len(removed), "cost_usd": 0})
    return out
