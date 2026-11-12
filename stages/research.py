"""Stage 04: ledger.json + angle + map -> 04_research/Lnn.md -> 04_dossier.md

One research agent per active ledger row (docs/ledger-research-addon.md):

- Deep-dive and missing rows get a research agent (prompts/research_row.md);
  covered rows get a verify-only agent that checks the episode's factual claims
  against tier 1-2 sources (prompts/research_verify.md).
- Each agent's budget comes from its row's rank and type (research.budgets) and
  is set as the web tools' max_uses. The API applies max_uses per request, so a
  resumed request would get the whole budget again. A tool loop that pauses
  after using any of its budget is therefore not resumed: one call without
  tools writes the agent's findings from what it already read
  (prompts/research_writeup.md). No agent can pass its budget.
- All research agents together, follow-ups included, stay under
  research.max_searches: the stage asks before a run whose budgets would pass it.
- Each agent writes 04_research/Lnn.md, its numbered entries [Lnn.k], and
  Lnn.sources.json, every URL it saw and the text of every page it fetched.

When every active row has findings, code merges them into 04_dossier.md: entries
renumbered D01, D02, ... in rank order, each keeping its row tag and kind, exact
duplicates across agents dropped. No model rewrites an entry on the way. A
source check is appended. Every verbatim: yes quote is matched word for word
against the page it was fetched from, or the transcript for the episode; a quote
that does not match is downgraded to verbatim: no, so the script cannot quote it.

Findings are paid for, so they are kept: a re-run researches only rows without
findings, and --rows L01,L03 (re)does just those rows. --force allows the
dossier to be rebuilt. Stage 04b (ledger_update.py) runs the follow-up round
with run_agents and rebuilds the dossier with build_dossier.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from . import ledger, llm
from .common import (KINDS, Run, StageError, entry_id, format_tags, load_prompt, parse_tags,
                     row_id)

# URLs may hold balanced parentheses (Wikipedia), but a markdown link's closing ')' is not part of one.
URL_IN_TEXT = re.compile(r"https?://(?:[^\s()\]>\"'`]|\([^\s()]*\))+")
QUOTE = re.compile(r"[\"“]([^\"”]*)[\"”]")  # pairs marks in order; short pairs are dropped later
EPISODE_SOURCE = re.compile(r"Source:\s*(the\s+)?(podcast\s+)?episode\b", re.I)
# '[L01.3]', '**[L1.3]**', '- [L01.3]' at the start of a line, in an agent's findings.
ROW_ENTRY = re.compile(r"^[ \t]*(?:[-*]|\d+\.)?[ \t]*\**\[L0*(\d+)\.(\d+)\]\**", re.M)
TAG_GROUP = re.compile(r"^[ \t]*\(([^)]*)\)")
LOCAL_REF = re.compile(r"\bL0*(\d+)\.(\d+)\b")  # an agent citing its own entry, e.g. 'see L01.3'
AGENT_STAGES = ("research_row", "research_followup")


# --- tools and what they returned ---------------------------------------------

def web_tools(cfg: dict, searches: int, fetches: int) -> list[dict]:
    return [
        {"type": cfg["tools"]["web_search"], "name": "web_search", "max_uses": searches},
        {"type": cfg["tools"]["web_fetch"], "name": "web_fetch", "max_uses": fetches,
         "max_content_tokens": cfg["research"]["max_fetch_tokens"]},
    ]


def tools(cfg: dict) -> list[dict]:
    """The baseline's tools: one agent, the single-prompt budget."""
    b = cfg["baseline"]
    return web_tools(cfg, b["max_searches"], b["max_fetches"])


def retrieved(content: list) -> dict:
    """Everything the web tools returned: {'urls': {url: title}, 'fetched': [{url, title, text}]}."""
    urls: dict[str, str] = {}
    fetched: list[dict] = []
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "web_search_tool_result":
            items = getattr(block, "content", None)
            if isinstance(items, list):  # an error result is a single object, not a list
                for item in items:
                    if getattr(item, "url", None):
                        urls.setdefault(item.url, getattr(item, "title", "") or "")
        elif btype == "web_fetch_tool_result":
            res = getattr(block, "content", None)
            url = getattr(res, "url", None)
            if url:
                doc = getattr(res, "content", None)
                title = getattr(doc, "title", "") or ""
                urls[url] = title or urls.get(url, "")
                src = getattr(doc, "source", None)
                text = None
                if getattr(src, "type", None) == "text":
                    text = getattr(src, "data", None)
                elif getattr(src, "type", None) == "base64":  # a fetched PDF
                    text = _pdf_text(getattr(src, "data", "") or "")
                if text:
                    fetched.append({"url": url, "title": title, "text": text})
        elif btype == "text":
            for c in getattr(block, "citations", None) or []:
                if getattr(c, "url", None):
                    urls.setdefault(c.url, getattr(c, "title", "") or "")
    return {"urls": urls, "fetched": fetched}


def _pdf_text(b64: str) -> str | None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(base64.b64decode(b64)))
        return "\n".join(p.extract_text() or "" for p in reader.pages) or None
    except Exception:  # an unreadable PDF just can't be quote-checked
        return None


def extract_tagged(text: str, tag: str) -> str:
    """The last complete <tag>...</tag> block. Agents narrate between tool calls."""
    end = text.rfind(f"</{tag}>")
    start = text.rfind(f"<{tag}>", 0, end) if end != -1 else -1
    if start == -1:
        raise StageError(f"no complete <{tag}> block in the response.")
    return text[start + len(tag) + 2:end].strip()


# --- an agent's findings ---------------------------------------------------------------

def row_paths(r: Run, rid: str, followup: bool = False) -> tuple[Path, Path]:
    stem = f"{rid}.followup" if followup else rid
    d = r.path("research_dir")
    return d / f"{stem}.md", d / f"{stem}.sources.json"


def _section(text: str, name: str) -> str:
    m = re.search(rf"^##\s+{name}\b.*$", text, re.M | re.I)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else None].strip()


def _chunks(text: str, pattern: re.Pattern) -> list[tuple[re.Match, str]]:
    """Each match of `pattern` with the text up to the next match or heading."""
    marks = list(pattern.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunk = text[m.start():end]
        h = re.search(r"^#{1,6}\s", chunk, re.M)
        out.append((m, (chunk[:h.start()] if h else chunk).strip()))
    return out


def parse_entries(findings: str) -> list[dict]:
    """The [Lnn.k] entries in an agent's '## Entries' section:
    [{'n': k, 'tags': {...}, 'body': text after the tags, 'tagged': whether tags were found}]."""
    out = []
    for _, chunk in _chunks(_section(findings, "Entries") or findings, ROW_ENTRY):
        head = ROW_ENTRY.match(chunk)
        rest = chunk[head.end():]
        tm = TAG_GROUP.match(rest)
        tags = parse_tags(tm.group(1)) if tm else parse_tags("")
        tagged = bool(tm and (tags["kind"] or tags["tier"] is not None))
        if not tagged:  # '(In 1933) Weil...' is text, not tags
            tags = parse_tags("")
        tags["rows"] = []  # the owner is the row whose file this is
        out.append({"n": int(head.group(2)), "tags": tags,
                    "body": (rest[tm.end():] if tagged else rest).strip(), "tagged": tagged})
    return out


# --- one research agent ----------------------------------------------------------------

def agent_prompt(cfg: dict, row: dict, led: dict, angle: str, episode_map: str,
                 followup: dict | None = None) -> str:
    searches, fetches = ledger.budget(cfg, row, followup=bool(followup))
    others = "\n".join(f"- {o['id']}: {o['idea']}" for o in ledger.active(led) if o["id"] != row["id"])
    fmt = load_prompt("research_format", row_id=row["id"], start=followup["start"] if followup else 1)
    note = load_prompt("research_followup", earlier=followup["earlier"], gap=followup["gap"]) if followup else ""
    return load_prompt(
        "research_verify" if row["type"] == "covered" else "research_row",
        angle=angle, map=episode_map, row_id=row["id"], idea=row["idea"], rank=row["rank"],
        type=row["type"], episode_has=row["episode_has"] or "Nothing.",
        research_question=row["research_question"], tier_needed=row["tier_needed"],
        other_rows=others or "(none)", searches=searches, fetches=fetches,
        source_tiers=load_prompt("source_tiers"), format=fmt, followup=note,
    )


def material_text(got: dict) -> str:
    seen = "\n".join(f"- {t}: {u}" if t else f"- {u}" for u, t in got["urls"].items()) or "(none)"
    pages = "\n\n".join(f"<page url=\"{f['url']}\" title=\"{f['title']}\">\n{f['text']}\n</page>"
                        for f in got["fetched"]) or "(no pages were fetched)"
    return f"Search results and pages seen:\n{seen}\n\nPages fetched, in full:\n\n{pages}"


def _write_up(r: Run, cfg: dict, label: str, prompt: str, notes: str, got: dict, extra: dict, check_spend):
    return llm.call(r, cfg, "research_writeup", role="researcher", label=f"{label} write-up",
                    messages=[{"role": "user", "content": load_prompt(
                        "research_writeup", instructions=prompt, notes=notes or "(none)",
                        material=material_text(got))}],
                    preapproved=True, before_request=check_spend, log_extra=extra)


def pending_path(r: Run, rid: str, followup: bool = False) -> Path:
    """Where a tool loop's material waits for its write-up: Lnn.pending.json, Lnn.followup.pending.json."""
    md, _ = row_paths(r, rid, followup)
    return md.with_name(md.stem + ".pending.json")


def has_pending(r: Run, row: dict, followup: bool = False) -> bool:
    p = pending_path(r, row["id"], followup)
    return p.exists() and json.loads(p.read_text(encoding="utf-8")).get("fingerprint") == ledger.fingerprint(row)


def run_agent(r: Run, cfg: dict, row: dict, led: dict, angle: str, episode_map: str,
              followup: dict | None = None, check_spend=None) -> dict:
    """Research one row. A row's files (Lnn.md and Lnn.sources.json) change only when new findings
    have been written, so a failed redo leaves the old findings, and their sources, as they were."""
    if check_spend:
        check_spend()  # a queued agent does not start once the spend cap is reached
    searches, fetches = ledger.budget(cfg, row, followup=bool(followup))
    stage = "research_followup" if followup else "research_row"
    label = f"research {row['id']}" + (" follow-up" if followup else "")
    extra = {"row": row["id"]}
    prompt = agent_prompt(cfg, row, led, angle, episode_map, followup)
    md, src = row_paths(r, row["id"], followup=bool(followup))
    pending = pending_path(r, row["id"], followup=bool(followup))
    md.parent.mkdir(parents=True, exist_ok=True)
    if has_pending(r, row, followup=bool(followup)):
        # An earlier run paid for the tool loop and then failed in the write-up: write up again only.
        saved = json.loads(pending.read_text(encoding="utf-8"))
        print(f"  {label}: writing up the research an earlier run paid for")
        got = {"urls": saved.get("urls", {}), "fetched": saved.get("fetched", [])}
        n_search, n_fetch, notes, cost = (saved.get("searches", 0), saved.get("fetches", 0),
                                          saved.get("notes", ""), saved.get("cost_usd", 0.0))
        paused = True
    else:
        # A paused tool loop is resumed only if it has used none of its budget: max_uses applies
        # per request, so a resumed request could spend the whole budget again.
        res = llm.call(
            r, cfg, stage, role="researcher", label=label, messages=[{"role": "user", "content": prompt}],
            tools=web_tools(cfg, searches, fetches), expected_searches=searches, preapproved=True,
            before_request=check_spend, log_extra=extra,
            resume_if=lambda u: u.web_searches == 0 and u.web_fetches == 0,
        )
        got = retrieved(res.content)
        n_search, n_fetch, notes, cost, paused = (res.usage.web_searches, res.usage.web_fetches, res.text,
                                                  res.usage.cost_usd, res.paused)
    record = {**got, "row": row["id"], "followup": bool(followup), "fingerprint": ledger.fingerprint(row),
              "budget": {"searches": searches, "fetches": fetches}, "searches": n_search, "fetches": n_fetch,
              "written_up": paused, "cost_usd": round(cost, 4)}
    text = notes
    if paused:  # save what the loop found first, so a failed write-up costs only itself
        pending.write_text(json.dumps({**record, "notes": notes}, ensure_ascii=False, indent=1), encoding="utf-8")
        w = _write_up(r, cfg, label, prompt, notes, got, extra, check_spend)
        text, cost = w.text, cost + w.usage.cost_usd
        record["cost_usd"] = round(cost, 4)
    try:
        findings = extract_tagged(text, "research")
    except StageError:
        partial = md.parent / f"_partial_{md.stem}.md"
        partial.write_text(text, encoding="utf-8")
        raise StageError(f"{label}: no <research> block; raw output saved to "
                         f"{partial.relative_to(r.root)}" + ("; the next run writes it up again from the saved "
                                                             "material" if paused else "")) from None
    md.write_text(findings + "\n", encoding="utf-8")
    src.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    pending.unlink(missing_ok=True)
    if not followup:  # new first-round findings: an old follow-up answered the old findings' gap
        _set_aside_followup(r, row["id"])
    entries = parse_entries(findings)
    kinds = Counter(e["tags"]["kind"] or "untagged" for e in entries)
    print(f"  {label} done: {len(entries)} entries ({', '.join(f'{k} {v}' for k, v in kinds.items()) or 'none'}), "
          f"{n_search} of {searches} searches, {n_fetch} of {fetches} fetches"
          + (", written up when its tool loop paused" if paused else "") + f", ${cost:.2f}")
    return {"row": row["id"], "entries": len(entries), "cost": cost}


def run_agents(r: Run, cfg: dict, jobs: list[tuple[dict, dict | None]], led: dict, angle: str,
               episode_map: str, check_spend=None) -> list[str]:
    """Run (row, followup-or-None) jobs in parallel, in rank order as slots free up.
    Returns error messages for the agents that failed; the others are saved."""
    errors = []
    workers = max(1, min(cfg["research"]["parallel_agents"], len(jobs)))
    print(f"  {len(jobs)} agent(s), {workers} at a time: "
          + ", ".join(row["id"] + (" follow-up" if fu else "") for row, fu in jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_agent, r, cfg, row, led, angle, episode_map, fu, check_spend): (row, fu)
                   for row, fu in jobs}
        for f in as_completed(futures):
            row, fu = futures[f]
            try:
                f.result()
            except StageError as e:
                errors.append(f"{row['id']}{' follow-up' if fu else ''}: {e}")
                print(f"  {row['id']} failed: {e}")
    return errors


# --- budgets, the search cap, the spend cap ------------------------------------------------

def agent_estimate(cfg: dict, searches: int, fetches: int, stage: str = "research_row") -> float:
    """Upper-end cost of one agent. Its context grows with each search result and page,
    and the tool loop re-reads it every iteration (mostly from cache): x1.5, priced uncached."""
    page = cfg["research"]["max_fetch_tokens"]
    input_tokens = int((30_000 + searches * 8_000 + fetches * page) * 1.5)
    return llm.estimate_call(cfg, llm.stage_model(cfg, stage, "researcher"), input_tokens,
                             cfg["stages"][stage]["expected_output_tokens"], searches)


def estimate(cfg: dict, jobs: list[tuple[dict, bool]]) -> float:
    total = 0.0
    for row, followup in jobs:
        s, f = ledger.budget(cfg, row, followup=followup)
        total += agent_estimate(cfg, s, f, "research_followup" if followup else "research_row")
    return total


def searches_used(r: Run) -> int:
    return sum(rec.get("web_searches", 0) or 0 for rec in r.read_log() if rec.get("stage") in AGENT_STAGES)


def check_search_cap(r: Run, cfg: dict, planned: int, what: str) -> None:
    cap, used = cfg["research"]["max_searches"], searches_used(r)
    if used + planned > cap:
        llm.confirm(r, "search cap", f"{what} would bring research to up to {used + planned} searches "
                                     f"({used} used so far, up to {planned} more), past the {cap}-search "
                                     "cap (research.max_searches).")


def research_spend(r: Run) -> float:
    return sum(rec.get("cost_usd", 0) or 0 for rec in r.read_log() if rec.get("stage", "").startswith("research"))


def spend_checker(r: Run, cfg: dict):
    """A before_request hook: stop once this invocation's research spend reaches research.max_usd."""
    cap, before = cfg["research"]["max_usd"], research_spend(r)

    def check() -> None:
        spent = research_spend(r) - before
        if spent >= cap:
            raise StageError(f"research has spent ${spent:.2f}, the research.max_usd cap. Finished rows "
                             "are saved; raise the cap in config.yaml and re-run to continue.")
    return check


# --- merge ---------------------------------------------------------------------------------

def _norm(url: str) -> str:
    url = url.rstrip(".,;:")
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = url.split("#", 1)[0]
    return url.rstrip("/").lower()


SUPERSCRIPTS = str.maketrans({c: " " for c in "⁰¹²³⁴⁵⁶⁷⁸⁹"})


def _norm_text(s: str) -> str:
    """Lowercase words for matching quotes against pages: punctuation, underscores (emphasis),
    footnote numbers glued to a word ('beautiful12') and superscripts are dropped."""
    s = s.lower().translate(SUPERSCRIPTS).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    # Editorial brackets: '[T]he' and 'want[ed]' match 'The' and 'wanted'; '[sic]' is dropped. The
    # house style tells the writer to quote around them, so both sides of a check normalize alike.
    s = re.sub(r"\[([^\W\d_]+)\]", lambda m: "" if m.group(1) == "sic" else m.group(1), s)
    s = re.sub(r"(?<!\w)'|'(?!\w)", " ", s)  # a quote mark, not an apostrophe inside a word
    s = re.sub(r"(?<=[^\W\d])\d+\b", "", s)  # a footnote number glued to a word ('love2', '_love_2')
    return " ".join(re.sub(r"[^\w']+|_", " ", s).split())


ELLIPSIS = re.compile(r"\s*(?:\.\s?\.\s?\.|…)\s*")


def quote_found(body: str, pages: list[str]) -> bool | None:
    """Whether each quoted passage in an entry's text (before 'Source:') is on one of the
    normalized pages. An ellipsis marks a cut: every piece of a passage must be on the same
    page, matched as whole words. Passages under three words (a title, a term) are not checked.
    None if nothing is quoted."""
    text = re.split(r"\bSource:", body, maxsplit=1)[0]
    passages = []
    for q in QUOTE.findall(text):
        pieces = [p for p in (_norm_text(x) for x in ELLIPSIS.split(q)) if p]
        if sum(len(p.split()) for p in pieces) >= 3:
            passages.append(pieces)
    if not passages:
        return None
    padded = [f" {page} " for page in pages]
    return all(any(all(f" {p} " in page for p in pieces) for page in padded) for pieces in passages)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def domain_tier(cfg: dict, urls: list[str]) -> tuple[int, str] | None:
    """The best tier the cited domains allow, if every cited domain is in research.tier_domains."""
    table = cfg["research"].get("tier_domains") or {}
    found = []
    for u in urls:
        host = _host(u)
        hit = next(((d, t) for d, t in table.items() if host == d or host.endswith("." + d)), None)
        if hit is None:
            return None
        found.append((hit[1], hit[0]))
    return min(found) if found else None


def gather(r: Run, led: dict) -> dict:
    """Every active row's entries (first round, then follow-up), deduplicated and numbered."""
    numbered: list[dict] = []
    by_text: dict[str, dict] = {}
    seen = {"urls": {}, "fetched": []}
    not_found, stats, merged, renumbered = [], [], [], []
    for row in ledger.active(led):
        rnum = int(row["id"][1:])
        first_round: dict[int, int] = {}  # local entry number -> D number, for the row's first file
        for fu in (False, True):
            md, src = row_paths(r, row["id"], fu)
            if not md.exists():
                continue
            findings = md.read_text(encoding="utf-8")
            if src.exists():
                s = json.loads(src.read_text(encoding="utf-8"))
                seen["urls"].update(s.get("urls", {}))
                seen["fetched"].extend(s.get("fetched", []))
                stats.append((row["id"] + (" follow-up" if fu else ""), s.get("searches", 0),
                              (s.get("budget") or {}).get("searches"), s.get("written_up", False)))
            here: dict[int, int] = {}  # this file's own numbers
            for e in parse_entries(findings):
                if e["n"] in here:
                    print(f"  warning: {row['id']}.{e['n']} appears twice in {md.name}; keeping the first")
                    continue
                if fu and e["n"] in first_round:
                    renumbered.append(f"{row['id']}.{e['n']} in {md.name}")
                key = " ".join([e["tags"]["kind"] or "claim", *e["tags"]["links"], *e["body"].split()])
                if key in by_text:  # an exact duplicate of another agent's entry
                    first = by_text[key]
                    if row["id"] not in first["tags"]["rows"]:
                        first["tags"]["rows"].append(row["id"])
                        if first not in merged:
                            merged.append(first)
                    here[e["n"]] = first["d"]
                    continue
                e["tags"]["rows"] = [row["id"]]
                e["d"], e["row"] = len(numbered) + 1, row["id"]
                here[e["n"]] = e["d"]
                by_text[key] = e
                numbered.append(e)
            # 'see L01.3' -> 'see D05': a file's own numbers first, then (for a follow-up) the first round's.
            refs = {**first_round, **here} if fu else here
            for e in numbered:
                if e.get("file") is None and e["row"] == row["id"]:
                    e["file"], e["refs"] = md.name, refs
            nf = _section(findings, "Not found")
            if nf and nf.strip().rstrip(".").lower() not in ("nothing", "none", "n/a"):
                not_found.append((row["id"] + (" follow-up" if fu else ""), _renumber(nf, rnum, refs)))
            if not fu:
                first_round = here
    for e in numbered:
        e["body"] = _renumber(e["body"], int(e["row"][1:]), e.pop("refs"))
        e.pop("file", None)
    return {"entries": numbered, "seen": seen, "not_found": not_found, "stats": stats, "merged": merged,
            "renumbered": renumbered}


def _renumber(text: str, rnum: int, refs: dict[int, int]) -> str:
    """An agent's references to its own row's entries, 'L01.3', become dossier numbers."""
    def sub(m: re.Match) -> str:
        if int(m.group(1)) == rnum and int(m.group(2)) in refs:
            return entry_id(refs[int(m.group(2))])
        return m.group(0)
    return LOCAL_REF.sub(sub, text)


def check_entries(cfg: dict, entries: list[dict], seen: dict, transcript: str) -> dict:
    """Quote, tier and URL checks. Downgrades verbatim: yes quotes that do not match their page."""
    by_url: dict[str, list[str]] = {}
    for f in seen["fetched"]:
        by_url.setdefault(_norm(f["url"]), []).append(_norm_text(f["text"]))
    every_page = [t for texts in by_url.values() for t in texts]
    transcript_n = _norm_text(transcript) if transcript else ""
    seen_n = {_norm(u) for u in seen["urls"]}
    out = {"verified_page": [], "verified_transcript": [], "downgraded": [], "no_tier": [], "untagged": [],
           "no_url": [], "episode": [], "tier_flags": [], "unseen_urls": set()}
    for e in entries:
        t, d, body = e["tags"], entry_id(e["d"]), e["body"]
        urls = [u.rstrip(".,;:") for u in URL_IN_TEXT.findall(body)]
        episode = bool(EPISODE_SOURCE.search(body)) or t["tier"] == "episode"
        if not e["tagged"]:
            out["untagged"].append(d)
        if t["kind"] != "connection":
            if t["tier"] is None:
                out["no_tier"].append(d)
            if episode:
                out["episode"].append(d)
            elif not urls:
                out["no_url"].append(d)
        out["unseen_urls"].update(u for u in urls if _norm(u) not in seen_n)
        if isinstance(t["tier"], int) and urls and (dt := domain_tier(cfg, urls)) and t["tier"] < dt[0]:
            out["tier_flags"].append(f"{d} (tier {t['tier']} recorded; {dt[1]} is tier {dt[0]})")
        if t["verbatim"] != "yes":
            continue
        if episode:
            pages, where = ([transcript_n] if transcript_n else []), "transcript"
        else:
            pages = [p for u in urls for p in by_url.get(_norm(u), [])] or every_page  # or a redirect
            where = "page"
        found = quote_found(body, pages) if pages else False
        if found:
            out["verified_transcript" if where == "transcript" else "verified_page"].append(d)
            continue
        reason = ("no quoted words" if found is None else
                  f"no {'transcript' if where == 'transcript' else 'fetched page'} to check against" if not pages else
                  "not in the transcript" if where == "transcript" else "not found on the fetched page")
        t["verbatim"] = "no"
        out["downgraded"].append(f"{d} ({reason})")
    return out


def episode_intro(r: Run) -> str:
    """The episode's identity from 01_map.md: its title line and the first paragraph of
    '## Episode in brief' (show, host, guest and who they are). Empty if there is no map."""
    p = r.path("map")
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    title = re.search(r"^#\s+(?:Episode Map:\s*)?(.+)$", text, re.M)
    brief = re.search(r"^##\s+Episode in brief\s*\n+(.+?)(?:\n\s*\n|\Z)", text, re.M | re.S)
    parts = [f"The episode: {title.group(1).strip()}." if title else "", brief.group(1).strip() if brief else ""]
    return " ".join(" ".join(p.split()) for p in parts if p)


def build_dossier(r: Run, cfg: dict, led: dict, transcript: str = "") -> str:
    """Merge every active row's findings into the dossier text. Code only: same files in, same dossier out."""
    g = gather(r, led)
    entries = g["entries"]
    if not entries:
        raise StageError("research: no [Lnn.k] entries in any row's findings; nothing to merge.")
    chk = check_entries(cfg, entries, g["seen"], transcript)
    angle = r.path("angle").read_text(encoding="utf-8") if r.path("angle").exists() else ""
    title = re.search(r"^#+\s+(?:Angle:\s*)?(.+)$", angle, re.M)  # the choose stage writes "# Angle: <title>"
    lines = [f"# Dossier: {title.group(1).strip() if title else r.name}", "",
             "Merged by code from 04_research/, one section per ledger row in rank order. Each entry keeps "
             "its row, its kind and its source tier. See the source check at the end.", "", "## Entries", ""]
    for row in ledger.active(led):
        lines += [f"### {row['id']}: {row['idea']}", "",
                  f"Rank {row['rank']}, {row['type']}; needs tier {row['tier_needed']} or better.", ""]
        mine = [e for e in entries if e["row"] == row["id"]]
        lines += [f"{'[' + entry_id(e['d']) + ']'} {format_tags(e['tags'])} {e['body']}\n" for e in mine] or \
                 ["(no entries)\n"]
    if intro := episode_intro(r):
        lines += ["### Episode: the conversation this chapter grows from", "",
                  "Added by code from 01_map.md, so the opening can say what the listener is hearing: "
                  "the show, who was talking and who they are.", "",
                  f"[{entry_id(max(e['d'] for e in entries) + 1)}] (claim, tier episode) {intro} Source: episode\n"]
    if g["not_found"]:
        lines += ["## Not found", ""] + [f"From {rid}:\n{text}\n" for rid, text in g["not_found"]]
    cited = sorted({u.rstrip(".,;:") for e in entries for u in URL_IN_TEXT.findall(e["body"])})
    lines += ["## Sources cited", ""]
    lines += [f"- {g['seen']['urls'][u]}: {u}" if g["seen"]["urls"].get(u) else f"- {u}" for u in cited] or ["(none)"]
    lines += ["", "## Source check (generated by the research stage)", ""]
    kinds = Counter(e["tags"]["kind"] or "claim" for e in entries)
    lines.append(f"- Entries: {len(entries)} ({', '.join(f'{k} {kinds[k]}' for k in KINDS if kinds[k])})")
    if g["renumbered"]:
        lines.append("- Follow-up entries that reused a first-round number (kept, renumbered): "
                     + ", ".join(g["renumbered"]))
    if g["merged"]:
        lines.append("- Exact duplicates merged across agents: "
                     + ", ".join(f"{entry_id(e['d'])} ({', '.join(e['tags']['rows'])})" for e in g["merged"]))
    no_material = [row["id"] for row in ledger.active(led) if row["type"] != "covered"
                   and not any(e["tags"]["kind"] == "material" and row["id"] in e["tags"]["rows"] for e in entries)]
    if no_material:
        lines.append(f"- Rows with no tellable material: {', '.join(no_material)}")
    for key, text in (("untagged", "Entries with no tags (treated as claims)"),
                      ("no_tier", "Entries with no source tier"),
                      ("no_url", "Entries with no source URL"),
                      ("tier_flags", "Tiers better than the source's domain allows (flagged, not changed)"),
                      ("episode", "Entries sourced to the episode")):
        if chk[key]:
            lines.append(f"- {text}: {', '.join(chk[key])}")
    if chk["unseen_urls"]:
        lines.append("- Cited URLs that no search or fetch returned. Check these by hand; "
                     "the agent may have written them from memory:")
        lines += [f"  - {u}" for u in sorted(chk["unseen_urls"])]
    verified = chk["verified_page"] + chk["verified_transcript"]
    lines.append(f"- Verbatim quotes matched word for word: {len(verified)}"
                 + (f" ({', '.join(chk['verified_page'])} on the fetched page" if chk["verified_page"] else "")
                 + (f"{'; ' if chk['verified_page'] else ' ('}{', '.join(chk['verified_transcript'])} in the "
                    "transcript" if chk["verified_transcript"] else "")
                 + (")" if verified else ""))
    if chk["downgraded"]:
        lines.append("- Quotes downgraded to verbatim: no, so the script may not quote them: "
                     + ", ".join(chk["downgraded"]))
    if g["stats"]:
        used = sum(s[1] or 0 for s in g["stats"])
        lines.append(f"- Searches: {used} (cap {cfg['research']['max_searches']}): " + ", ".join(
            f"{rid} {n} of {b}" + (", written up after its tool loop paused" if w else "") for rid, n, b, w in g["stats"]))
    return "\n".join(lines).rstrip() + "\n"


# --- the stage ----------------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_dossier(r: Run, text: str) -> None:
    """Write 04_dossier.md and record its hash, so ledger-update can tell a hand edit from a rebuild."""
    r.path("dossier").write_text(text, encoding="utf-8")
    r.path("dossier_hash").write_text(_sha(text) + "\n", encoding="utf-8")


def dossier_edited(r: Run, text: str) -> bool | None:
    """Whether the dossier differs from what the pipeline last wrote; None if there is no record."""
    p = r.path("dossier_hash")
    return p.read_text(encoding="utf-8").strip() != _sha(text) if p.exists() else None


def parse_rows(spec: str) -> list[str]:
    """'L01', 'L01,L03', 'L1-L3' or '1-3' -> row ids."""
    ids: list[str] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"[Ll]?0*(\d+)(?:[-–][Ll]?0*(\d+))?", part)
        if not m or int(m.group(2) or m.group(1)) < int(m.group(1)):
            raise StageError(f"--rows {spec!r}: use row ids like L01, L01,L03 or L01-L03.")
        ids += [row_id(n) for n in range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1)]
    if not ids:
        raise StageError(f"--rows {spec!r} names no rows.")
    return ids


def _stale(r: Run, row: dict) -> bool:
    for fu in (False, True):
        _, src = row_paths(r, row["id"], fu)
        if src.exists() and json.loads(src.read_text(encoding="utf-8")).get("fingerprint") != ledger.fingerprint(row):
            return True
    return False


def _set_aside_followup(r: Run, rid: str) -> None:
    """A redone row's old follow-up answered the old findings' gap: move it out of the merge."""
    files = [p for p in row_paths(r, rid, followup=True) if p.exists()]
    if not files:
        return
    old = r.path("research_dir") / "_superseded"
    old.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for f in files:
        f.rename(old / f"{f.stem}.{stamp}{f.suffix}")
    print(f"  {rid}: its old follow-up moved to 04_research/_superseded/; ledger-update can give it a new one")


def run(r: Run, cfg: dict, rows: str | None = None, plan_only: bool = False):
    led = ledger.load(r, cfg)
    angle, episode_map = r.read("angle"), r.read("map")
    transcript = r.path("transcript").read_text(encoding="utf-8") if r.path("transcript").exists() else ""
    if led.get("inputs") and transcript and led["inputs"] != ledger.inputs_hash(transcript, episode_map, angle):
        print("  warning: the transcript, map or angle has changed since the ledger was made. If its rows no "
              "longer fit the angle, edit ledger.json or run ledger --force.")
    if r.path("dossier").exists() and not r.force and not plan_only:
        raise StageError("Output already exists: 04_dossier.md. Use --force to rebuild it "
                         "(row findings already paid for are kept).")
    act = ledger.active(led)
    if rows:
        redo = set(parse_rows(rows))
        unknown = redo - {x["id"] for x in act}
        if unknown:
            raise StageError(f"--rows {rows!r}: not active rows in the ledger: {', '.join(sorted(unknown))}")
        todo = [x for x in act if x["id"] in redo]
    else:  # rows with no findings yet, or whose paid tool loop still waits for its write-up
        todo = [x for x in act if not row_paths(r, x["id"])[0].exists() or has_pending(r, x)]
    stale = [x["id"] for x in act if x not in todo and row_paths(r, x["id"])[0].exists() and _stale(r, x)]
    if stale:
        print(f"  warning: {', '.join(stale)} changed in ledger.json after being researched; their findings "
              f"answer the old question. Redo them with --rows {','.join(stale)}"
              + (" --force." if r.path("dossier").exists() else "."))

    print("  research plan, from ledger.json:")
    for x in act:
        s, f = ledger.budget(cfg, x)
        state = ("to research" if x in todo else "researched" if row_paths(r, x["id"])[0].exists()
                 else "not in this run")
        print(f"    {x['id']} rank {x['rank']} {x['type']:9} {s} searches, {f} fetches   {state}")
    planned = sum(ledger.budget(cfg, x)[0] for x in todo)
    est = estimate(cfg, [(x, False) for x in todo])
    print(f"  {len(todo)} agent(s) to run: up to {planned} searches ({searches_used(r)} used so far; cap "
          f"{cfg['research']['max_searches']}), estimated up to ${est:.2f}.")
    if plan_only:
        return None

    t0 = time.monotonic()
    if todo:
        check_search_cap(r, cfg, planned, f"Researching {len(todo)} row(s)")
        llm.guard(r, cfg, f"research ({len(todo)} agents)", est)
        errors = run_agents(r, cfg, [(x, None) for x in todo], led, angle, episode_map, spend_checker(r, cfg))
        if errors:
            raise StageError("some research agents failed; their rows will be researched on the next run:\n  "
                             + "\n  ".join(errors))
    missing = [x["id"] for x in act if not row_paths(r, x["id"])[0].exists()]
    if missing:
        print(f"  Still to research: {', '.join(missing)}. The dossier is built once every active row has "
              f"findings: python cli.py research {r.name}")
        return None
    dossier = build_dossier(r, cfg, led, transcript)
    r.check_writable("dossier")
    write_dossier(r, dossier)
    out = r.path("dossier")
    n = len(re.findall(r"^\[D\d+\]", dossier, re.M))
    print(f"  04_dossier.md: {n} entries from {len(act)} rows in {(time.monotonic() - t0) / 60:.1f} min. "
          "See its source check.")
    if any(x["status"] for x in act):
        print("  The ledger's dossier references predate this rebuild: run ledger-update --force.")
    print(f"\n  Next: python cli.py ledger-update {r.name} (stage 04b: judge each row, one follow-up round).")
    return out
