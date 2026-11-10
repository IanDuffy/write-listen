"""Stage 0: transcript PDF (or text) -> 00_source/transcript.txt

Extracts text and strips what a web page printed to PDF carries along: page
headers and footers, URLs, site chrome and related-article lists. Every line
removed is written to 00_source/removed_lines.txt so Ian can check nothing
real was cut.
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
}

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


def is_tail_heading(line: str, prev: str) -> bool:
    return bool(
        line and len(line) < 60 and line[0].isupper() and TAIL_MARKERS.match(line)
        and not SPEAKER.match(line)
        and not re.search(r"[.,;:!?]$", line)
        and (not prev or re.search(r"[.!?\"'”’)]$", prev))
    )


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
            if ln and ln not in repeated and ln.lower().rstrip(".:") not in CHROME_LINES \
                    and not URL.search(ln):
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
        elif line[:1].isupper() and line.lower().rstrip(".:") in CHROME_LINES:
            reason = "site chrome"  # UI labels are capitalized; a wrapped "listen." is dialogue
        if reason:
            removed.append(f"[{reason}] {line}")
        else:
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

    text, removed = clean(pages)
    out.write_text(text, encoding="utf-8")
    (src_dir / "removed_lines.txt").write_text("\n".join(removed) + "\n", encoding="utf-8")
    words = len(text.split())
    print(f"  {len(pages)} pages -> {words:,} words, {len(removed)} lines removed "
          f"(see 00_source/removed_lines.txt)")
    r.log({"stage": "ingest", "provider": "none", "pages": len(pages), "words": words,
           "removed_lines": len(removed), "cost_usd": 0})
    return out
