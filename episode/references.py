"""Stage 2: references.json, each source, work, person and quote attached to the sentence that uses it.

One Claude Sonnet 5.5 call per segment (matching, not writing) gets the segment's sentences and
the dossier entries offered for it: the brief's per-segment assignment plus the sentences' own
markers. It returns dossier ids, never URLs. Code then:
- keeps only ids that exist and were offered for that segment, and takes each card's URL and tier
  from the dossier entry (a work or person without an entry is listed without a link);
- keeps a quote only if its words really are in the sentence;
- thins: at most one card per sentence, and the same source not again within REPEAT_GAP seconds
  of chapter time (looked up from timing.json, so the cards themselves carry sentence ids only and
  survive re-narration).

Every call's tokens and cost go to 11_episode/log.jsonl (the run's own log is never touched); the
stage stops before a call that would pass its spending cap.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from stages import llm
from stages.brief import parse_segments
from stages.common import Run, StageError, dossier_entries, entry_tags, load_config, parse_json
from stages.research import URL_IN_TEXT

from . import script
from .timing import EPISODE_DIR

REPEAT_GAP = 60.0
STAGE = "episode_references"
STAGE_SETTINGS = {"model": "claude-sonnet-5-5", "effort": "low", "max_tokens": 16000, "expected_output_tokens": 4000}
KIND_ORDER = {"quote": 0, "source": 1, "work": 2, "person": 3}  # which card a sentence shows, if several

_str = {"type": "string"}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["sentences"],
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "sources", "works", "people", "quotes"],
        "properties": {
            "id": _str,
            "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                        "required": ["dossier", "title", "creator", "note"],
                        "properties": {"dossier": _str, "title": _str, "creator": _str, "note": _str}}},
            "works": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                      "required": ["title", "creator", "dossier", "note"],
                      "properties": {"title": _str, "creator": _str, "dossier": _str, "note": _str}}},
            "people": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                       "required": ["name", "dossier", "note"],
                       "properties": {"name": _str, "dossier": _str, "note": _str}}},
            "quotes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                       "required": ["text", "speaker", "dossier"],
                       "properties": {"text": _str, "speaker": _str, "dossier": _str}}},
        }}}},
}


def load_prompt(**values: str) -> str:
    """episode/prompts/references.md, the pipeline's prompt format: notes above '---', {{placeholders}} below."""
    text = (Path(__file__).parent / "prompts" / "references.md").read_text(encoding="utf-8")
    body = re.split(r"^---[ \t]*$", text, maxsplit=1, flags=re.M)[1]

    def fill(m: re.Match) -> str:
        if m.group(1) not in values:
            raise StageError(f"episode/prompts/references.md uses {{{{{m.group(1)}}}}} but no value was given.")
        return values[m.group(1)]
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", fill, body).strip() + "\n"


def entry_link(entry: str, entries: dict[int, str] | None = None, _seen: frozenset = frozenset()) -> str | None:
    """The first URL in an entry's source, following 'same URL as D226' / 'as in D226' to that entry;
    None for an episode-only or link-less entry."""
    src = re.split(r"\bSource:", entry, maxsplit=1)
    part = src[1] if len(src) > 1 else entry
    urls = URL_IN_TEXT.findall(part)
    if urls:
        return urls[0].rstrip(".,;:")
    back = re.search(r"\b(?:same (?:URL|page|source) as|as in)\s+D0*(\d+)", part, re.I)
    if back and entries and int(back.group(1)) in entries and int(back.group(1)) not in _seen:
        return entry_link(entries[int(back.group(1))], entries, _seen | {int(back.group(1))})
    return None


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w']+", " ", s.lower().replace("’", "'")).split())


def segment_entries(run: Path, segments: list[script.Segment], entries: dict[int, str]) -> dict[str, list[str]]:
    """Per segment id: the D-ids offered (brief assignment, then markers), existing entries only."""
    assigned: dict[int, list[str]] = {}
    brief = run / "05_brief.md"
    if brief.exists():
        try:
            assigned = {s["n"]: [f"D{k:02d}" for k in s["entries"]] for s in parse_segments(brief.read_text("utf-8"))}
        except StageError:
            assigned = {}
    out = {}
    for seg in segments:
        ids = list(assigned.get(seg.n, []))
        for x in seg.sentences:
            ids += [d for d in x.dossier if d not in ids]
        out[seg.id] = [d for d in ids if int(d[1:]) in entries]
    return out


def prompt_for(seg: script.Segment, offered: list[str], entries: dict[int, str]) -> str:
    lines = "\n".join(f"{x.id} | {x.text}" + (f" [{', '.join(x.dossier)}]" if x.dossier else "") for x in seg.sentences)
    ents = "\n\n".join(entries[int(d[1:])] for d in offered) or "(none)"
    return load_prompt(sentences=lines, entries=ents)


def ask(r: Run, cfg: dict, seg: script.Segment, prompt: str) -> dict:
    res = llm.call(r, cfg, STAGE, role="writer", messages=[{"role": "user", "content": prompt}],
                   json_schema=SCHEMA, label=f"references {seg.id}")
    return parse_json(r, res.text, f"references {seg.id}")


def candidates(seg: script.Segment, answer: dict, offered: list[str], entries: dict[int, str]) -> dict[str, list[dict]]:
    """Validated cards per sentence id, before thinning. URLs and tiers only from the dossier."""
    by_id = {x.id: x for x in seg.sentences}
    ok_ids = set(offered)
    out: dict[str, list[dict]] = {}

    def from_dossier(d: str) -> tuple[str | None, str | None, int | str | None]:
        d = (d or "").strip().upper()
        if not re.fullmatch(r"D\d+", d) or f"D{int(d[1:]):02d}" not in ok_ids:
            return None, None, None
        d = f"D{int(d[1:]):02d}"
        e = entries[int(d[1:])]
        if entry_tags(e)["kind"] in ("connection", "tension"):  # the researchers' reasoning: no source to show
            return None, None, None
        return d, entry_link(e, entries), entry_tags(e)["tier"]

    for s in answer.get("sentences", []):
        sent = by_id.get(s.get("id"))
        if sent is None:
            continue
        cards = []
        for src in s.get("sources", []):
            d, url, tier = from_dossier(src.get("dossier"))  # a reasoning entry comes back as no entry
            if d and src.get("title", "").strip():
                cards.append({"kind": "source", "title": src["title"].strip(), "creator": src.get("creator", "").strip(),
                              "url": url, "tier": tier, "dossier_ref": d, "note": src.get("note", "").strip()})
        for w in s.get("works", []):
            if w.get("title", "").strip():
                d, url, tier = from_dossier(w.get("dossier"))
                cards.append({"kind": "work", "title": w["title"].strip(), "creator": w.get("creator", "").strip(),
                              "url": url, "tier": tier, "dossier_ref": d, "note": w.get("note", "").strip()})
        for p in s.get("people", []):
            if p.get("name", "").strip():
                d, url, tier = from_dossier(p.get("dossier"))
                # No link: the entry is a source that mentions them, rarely a page about them.
                cards.append({"kind": "person", "title": p["name"].strip(), "creator": "", "url": None, "tier": tier,
                              "dossier_ref": d, "note": p.get("note", "").strip()})
        for q in s.get("quotes", []):
            text = q.get("text", "").strip().strip('"“”')
            if text and _norm(text) in _norm(sent.text) and len(text.split()) <= 25:  # really read in this sentence
                d, url, tier = from_dossier(q.get("dossier"))
                cards.append({"kind": "quote", "title": text, "creator": q.get("speaker", "").strip(), "url": url,
                              "tier": tier, "dossier_ref": d, "note": ""})
        if cards:
            out[sent.id] = cards
    return out


def _keys(card: dict) -> set[str]:
    """What counts as 'the same source' for the repeat rule: the same work (by title, whichever
    kind of card names it), the same link, or the same dossier entry."""
    keys = {"title:" + _norm(card["title"])}
    if card["kind"] == "person" and _norm(card["title"]):  # "C. D. C. Reeve" and "Reeve" are one person
        keys.add("person:" + _norm(card["title"]).split()[-1])
    if card["url"]:
        keys.add("url:" + card["url"])
    if card["dossier_ref"]:
        keys.add("ref:" + card["dossier_ref"])
    return keys


def thin(order: list[str], cands: dict[str, list[dict]], start_of: dict[str, float], gap: float = REPEAT_GAP) -> list[dict]:
    """At most one card per sentence (quote, then source by tier, then work, then person), and the
    same source not again within `gap` seconds."""
    shown: dict[str, float] = {}
    out = []
    for sid in order:
        t = start_of[sid]
        options = sorted(cands.get(sid, []), key=lambda c: (KIND_ORDER[c["kind"]],
                                                            c["tier"] if isinstance(c["tier"], int) else 9))
        for c in options:
            if all(t - shown.get(k, -1e9) >= gap for k in _keys(c)):
                shown.update({k: t for k in _keys(c)})
                out.append({"id": f"ref{len(out) + 1:03d}", "sentence_id": sid, **c, "image": None})
                break
    return out


def build(run: Path, cfg: dict | None = None, max_usd: float = 3.0) -> tuple[list[dict], float]:
    cfg = copy.deepcopy(cfg or load_config())
    cfg["stages"][STAGE] = STAGE_SETTINGS
    tpath = run / EPISODE_DIR / "timing.json"
    if not tpath.exists():
        raise StageError("references need timing.json first: python -m episode timing <run>")
    timing = json.loads(tpath.read_text(encoding="utf-8"))
    start_of = {x["id"]: x["start"] for s in timing["segments"] for x in s["sentences"]}
    segments = script.parse((run / "09_script_final.md").read_text("utf-8"), cfg["narration"].get("pronunciations") or {})
    if [x.id for s in segments for x in s.sentences] != list(start_of):
        raise StageError("timing.json does not match the script's sentences: re-run timing.")
    entries = dossier_entries((run / "04_dossier.md").read_text("utf-8"))
    offered = segment_entries(run, segments, entries)
    r = Run(name="episode", root=run / EPISODE_DIR)  # log.jsonl here, never the run's own
    spent = 0.0
    all_cands: dict[str, list[dict]] = {}
    raw_dir = run / EPISODE_DIR / "references_raw"
    raw_dir.mkdir(exist_ok=True)
    for seg in segments:
        prompt = prompt_for(seg, offered[seg.id], entries)
        digest = hashlib.sha256((prompt + STAGE_SETTINGS["model"]).encode()).hexdigest()[:16]
        raw = raw_dir / f"{seg.id}.json"
        saved = json.loads(raw.read_text("utf-8")) if raw.exists() else {}
        if saved.get("prompt_sha") == digest:  # same sentences, same entries: the answer is reused
            all_cands.update(candidates(seg, saved["answer"], offered[seg.id], entries))
            continue
        est = llm.estimate_call(cfg, STAGE_SETTINGS["model"], llm.estimate_tokens(
            "".join(entries[int(d[1:])] for d in offered[seg.id]) + " ".join(x.text for x in seg.sentences)) + 1500,
            STAGE_SETTINGS["expected_output_tokens"])
        if spent + est > max_usd:
            raise StageError(f"references: the next call ({seg.id}, ~${est:.2f}) would pass the ${max_usd:.2f} cap "
                             f"(${spent:.2f} spent).")
        before = sum(rec.get("cost_usd", 0) or 0 for rec in r.read_log())
        answer = ask(r, cfg, seg, prompt)
        spent += sum(rec.get("cost_usd", 0) or 0 for rec in r.read_log()) - before
        raw.write_text(json.dumps({"prompt_sha": digest, "answer": answer}, indent=1), "utf-8")
        all_cands.update(candidates(seg, answer, offered[seg.id], entries))
    (run / EPISODE_DIR / "attachments.json").write_text(json.dumps(all_cands, ensure_ascii=False, indent=1), "utf-8")
    return thin(list(start_of), all_cands, start_of), spent


def write(run: Path, refs: list[dict]) -> Path:
    p = run / EPISODE_DIR / "references.json"
    p.write_text(json.dumps(refs, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
