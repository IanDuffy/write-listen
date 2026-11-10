"""Stage 4: angle + map -> 04_research/ -> 04_dossier.md

Deep research with several agents, in four steps:

1. Plan (Opus). Break the angle into research topics: 04_research/plan.md.
   Ian can edit the plan; `research <run> --plan-only` stops here.
2. Research (Sonnet, in parallel). One agent per topic, each with its own web
   search and fetch budget. Each writes 04_research/topics/Tnn.md (findings,
   claims numbered [Tn.k]) and Tnn.sources.json (every URL it saw and the
   text of every page it fetched).
3. Critique (Opus). Read all findings, name the gaps, contradictions and weak
   sources, and add follow-up topics to the plan for another round. Repeats up
   to research.rounds times, or until the critic finds nothing to add.
4. Merge. Claims are renumbered [C1], [C2], ... into 04_dossier.md by code, so
   no model can alter a source or a quote on the way. Opus writes an overview
   on top. A source check lists cited URLs no agent retrieved, and every quote
   is matched word for word against the fetched page it came from.

Topic files are paid for, so they are kept: a re-run only researches topics
without findings. --topics T3,T5 redoes those topics. --force only allows
04_dossier.md to be rebuilt.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import llm
from .common import DOSSIER_ENTRY as ENTRY
from .common import Run, StageError, claims_section, load_prompt

# URLs may hold balanced parentheses (Wikipedia), but a markdown link's closing ')' is not part of one.
URL_IN_TEXT = re.compile(r"https?://(?:[^\s()\]>\"'`]|\([^\s()]*\))+")
TOPIC_HEADING = re.compile(r"^##\s+T(\d+)\s*[:.\-–—]\s*(.+)$", re.M)
TOPIC_ENTRY = re.compile(r"^[ \t]*(?:[-*]|\d+\.)?[ \t]*\**\[T(\d+)\.(\d+)\]\**", re.M)
QUOTE = re.compile(r"[\"“]([^\"”]*)[\"”]")  # pairs marks in order; short pairs are dropped later
EPISODE_SOURCE = re.compile(r"Source:\s*(the\s+)?(podcast\s+)?episode\b", re.I)


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


def retrieved_urls(content: list) -> set[str]:
    return set(retrieved(content)["urls"])


def extract_tagged(text: str, tag: str) -> str:
    """The last complete <tag>...</tag> block. Agents narrate between tool calls."""
    end = text.rfind(f"</{tag}>")
    start = text.rfind(f"<{tag}>", 0, end) if end != -1 else -1
    if start == -1:
        raise StageError(f"no complete <{tag}> block in the response.")
    return text[start + len(tag) + 2:end].strip()


def extract_dossier(text: str) -> str:
    return extract_tagged(text, "dossier")


# --- the plan -------------------------------------------------------------------

def parse_topics(plan: str) -> list[dict]:
    """'## T3: Title' sections of the plan, each with its full text."""
    heads = list(TOPIC_HEADING.finditer(plan))
    topics = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(plan)
        nxt = re.search(r"^#{1,2}\s+(?!T\d)", plan[m.end():end], re.M)  # a non-topic heading ends it
        if nxt:
            end = m.end() + nxt.start()
        topics.append({"id": int(m.group(1)), "title": m.group(2).strip(),
                       "text": plan[m.start():end].strip()})
    ids = [t["id"] for t in topics]
    if len(ids) != len(set(ids)):
        raise StageError(f"04_research/plan.md has duplicate topic numbers: {ids}")
    return topics


def make_plan(r: Run, cfg: dict, angle: str, episode_map: str) -> str:
    rc = cfg["research"]
    prompt = load_prompt("research_plan", angle=angle, map=episode_map,
                         min_topics=rc["min_topics"], max_topics=rc["max_topics"],
                         searches=rc["searches_per_topic"], fetches=rc["fetches_per_topic"])
    res = llm.call(r, cfg, "research_plan", messages=[{"role": "user", "content": prompt}],
                   label="research plan")
    plan = res.text.strip()
    if not parse_topics(plan):
        (r.path("research_dir") / "_partial_plan.md").write_text(plan, encoding="utf-8")
        raise StageError("research plan: no '## T1: Title' topics found; raw output saved to "
                         "04_research/_partial_plan.md.")
    r.path("research_plan").write_text(plan + "\n", encoding="utf-8")
    return plan


# --- one research agent -------------------------------------------------------------

def topic_paths(r: Run, n: int) -> tuple[Path, Path]:
    d = r.path("topics_dir")
    return d / f"T{n:02d}.md", d / f"T{n:02d}.sources.json"


def run_topic(r: Run, cfg: dict, topic: dict, angle: str, episode_map: str, plan: str,
              check_spend=None) -> dict:
    rc = cfg["research"]
    if check_spend:
        check_spend()  # a queued agent does not start once the cap is reached
    others = "\n".join(f"- T{t['id']}: {t['title']}" for t in parse_topics(plan) if t["id"] != topic["id"])
    prompt = load_prompt(
        "research_topic", angle=angle, map=episode_map, topic=topic["text"], n=topic["id"],
        other_topics=others or "(none)", searches=rc["searches_per_topic"], fetches=rc["fetches_per_topic"],
    )
    res = llm.call(
        r, cfg, "research_topic", role="researcher", label=f"research T{topic['id']}",
        messages=[{"role": "user", "content": prompt}],
        tools=web_tools(cfg, rc["searches_per_topic"], rc["fetches_per_topic"]),
        expected_searches=rc["searches_per_topic"], preapproved=True, before_request=check_spend,
    )
    md_path, src_path = topic_paths(r, topic["id"])
    got = retrieved(res.content)
    got.update({"topic": topic["id"], "title": topic["title"], "searches": res.usage.web_searches,
                "fetches": res.usage.web_fetches, "cost_usd": round(res.usage.cost_usd, 4)})
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        findings = extract_tagged(res.text, "findings")
    except StageError:
        (md_path.parent / f"_partial_T{topic['id']:02d}.md").write_text(res.text, encoding="utf-8")
        raise StageError(f"T{topic['id']}: no <findings> block; raw output saved to "
                         f"04_research/topics/_partial_T{topic['id']:02d}.md") from None
    md_path.write_text(findings + "\n", encoding="utf-8")
    n_claims = len(topic_claims(findings))
    print(f"  T{topic['id']} done: {n_claims} claims, {res.usage.web_searches} searches, "
          f"{len(got['fetched'])} pages read, ${res.usage.cost_usd:.2f}")
    return {"id": topic["id"], "claims": n_claims, "cost": res.usage.cost_usd}


def run_topics(r: Run, cfg: dict, todo: list[dict], angle: str, episode_map: str, plan: str,
               check_spend=None) -> list[str]:
    """Research topics in parallel. Returns error messages for topics that failed;
    the others are saved, so a re-run picks up only what is missing."""
    errors = []
    workers = max(1, min(cfg["research"]["parallel_agents"], len(todo)))
    print(f"  researching {len(todo)} topic(s), {workers} agents at a time: "
          + ", ".join(f"T{t['id']}" for t in todo))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_topic, r, cfg, t, angle, episode_map, plan, check_spend): t for t in todo}
        for f in as_completed(futures):
            t = futures[f]
            try:
                f.result()
            except StageError as e:
                errors.append(f"T{t['id']}: {e}")
                print(f"  T{t['id']} failed: {e}")
    return errors


# --- the critic -------------------------------------------------------------------------

def findings_bundle(r: Run, topics: list[dict]) -> str:
    parts = []
    for t in topics:
        md, _ = topic_paths(r, t["id"])
        if md.exists():
            parts.append(f"<topic id=\"T{t['id']}\" title=\"{t['title']}\">\n"
                         f"{md.read_text(encoding='utf-8').strip()}\n</topic>")
    return "\n\n".join(parts)


def critique(r: Run, cfg: dict, round_no: int, angle: str, plan: str) -> list[dict]:
    """Ask the critic for follow-up topics; append them to the plan. Returns the new topics."""
    rc = cfg["research"]
    topics = parse_topics(plan)
    next_id = max(t["id"] for t in topics) + 1
    prompt = load_prompt("research_critic", angle=angle, plan=plan, findings=findings_bundle(r, topics),
                         next_id=next_id, max_followups=rc["max_followups"],
                         searches=rc["searches_per_topic"], fetches=rc["fetches_per_topic"])
    res = llm.call(r, cfg, "research_critic", messages=[{"role": "user", "content": prompt}],
                   label=f"research critic round {round_no}", preapproved=True)
    text = res.text.strip()
    (r.path("research_dir") / f"critic_round{round_no}.md").write_text(text + "\n", encoding="utf-8")
    new = [t for t in parse_topics(text) if t["id"] >= next_id][: rc["max_followups"]]
    if new:
        block = "\n\n".join(t["text"] for t in new)
        plan = plan.rstrip() + f"\n\n# Round {round_no} follow-ups\n\n{block}\n"
        r.path("research_plan").write_text(plan, encoding="utf-8")
    print(f"  critic round {round_no}: {len(new)} follow-up topic(s)"
          + (": " + ", ".join(f"T{t['id']} {t['title']}" for t in new) if new else ", research is complete"))
    return new


# --- merge ----------------------------------------------------------------------------------

def _entries(text: str, pattern: re.Pattern) -> list[tuple[re.Match, str]]:
    marks = list(pattern.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunk = text[m.start():end]
        h = re.search(r"^#{1,6}\s", chunk, re.M)
        out.append((m, (chunk[:h.start()] if h else chunk).strip()))
    return out


def topic_claims(findings: str) -> list[tuple[re.Match, str]]:
    """The [Tn.k] entries in a topic's '## Claims' section. Citations of claims in
    its other sections ('[T3.2] and [T3.3] disagree') are not entries."""
    return _entries(_section(findings, "Claims") or findings, TOPIC_ENTRY)


def _section(text: str, name: str) -> str:
    m = re.search(rf"^##\s+{name}\b.*$", text, re.M | re.I)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else None].strip()


def merge_claims(r: Run, topics: list[dict]) -> tuple[str, dict[str, int], dict]:
    """Renumber every topic's [Tn.k] claims as [Cn], in topic order, by code.
    Returns (claims markdown, {'T3.4': 17}, everything the agents retrieved)."""
    mapping: dict[str, int] = {}
    blocks, conflicts, gaps = [], [], []
    seen = {"urls": {}, "fetched": []}
    n = 0
    for t in topics:
        md, src = topic_paths(r, t["id"])
        if not md.exists():
            continue
        findings = md.read_text(encoding="utf-8")
        if src.exists():
            s = json.loads(src.read_text(encoding="utf-8"))
            seen["urls"].update(s.get("urls", {}))
            seen["fetched"].extend(s.get("fetched", []))
        lines = [f"### T{t['id']}: {t['title']}", ""]
        for m, entry in topic_claims(findings):
            tag = f"T{m.group(1)}.{m.group(2)}"
            if tag in mapping:
                print(f"  warning: {tag} appears twice in T{t['id']}'s claims; keeping the first")
                continue
            n += 1
            mapping[tag] = n
            body = TOPIC_ENTRY.sub("", entry, count=1).strip()
            lines.append(f"[C{n}] {body}")
        blocks.append("\n".join(lines))
        if c := _section(findings, "Conflicts"):
            conflicts.append(f"From T{t['id']}:\n{c}")
        if g := _section(findings, "Could not establish"):
            gaps.append(f"From T{t['id']}:\n{g}")

    def renumber(text: str) -> str:
        return re.sub(r"\bT(\d+)\.(\d+)\b",
                      lambda m: f"C{mapping[m.group(0)]}" if m.group(0) in mapping else m.group(0), text)

    out = "## Claims\n\n" + "\n\n".join(renumber(b) for b in blocks)
    if conflicts:
        out += "\n\n## Conflicts between sources\n\n" + renumber("\n\n".join(conflicts))
    if gaps:
        out += "\n\n## Could not establish\n\n" + renumber("\n\n".join(gaps))
    return out, mapping, seen


def merge(r: Run, cfg: dict, angle: str, plan: str) -> str:
    topics = parse_topics(plan)
    claims_md, mapping, seen = merge_claims(r, topics)
    if not mapping:
        raise StageError("research: no [Tn.k] claims in any topic's findings; nothing to merge.")
    prompt = load_prompt("research_synthesis", angle=angle, claims=claims_md)
    res = llm.call(r, cfg, "research_synthesis", messages=[{"role": "user", "content": prompt}],
                   label="research synthesis", preapproved=True)
    overview = res.text.strip()
    title = re.search(r"^#\s+(.+)$", plan, re.M)
    cited = {u.rstrip(".,;:") for u in URL_IN_TEXT.findall(claims_md)}
    sources = "\n".join(f"- {seen['urls'][u]}: {u}" if seen["urls"].get(u) else f"- {u}" for u in sorted(cited))
    dossier = (f"# Dossier: {title.group(1).replace('Research plan:', '').strip() if title else r.name}\n\n"
               f"{overview}\n\n{claims_md}\n\n## Sources cited\n\n{sources}\n")
    return dossier.rstrip() + "\n" + source_check(dossier, set(seen["urls"]), seen["fetched"])


# --- checks ----------------------------------------------------------------------------------

def _norm(url: str) -> str:
    url = url.rstrip(".,;:")
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = url.split("#", 1)[0]
    return url.rstrip("/").lower()


def _norm_text(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(re.sub(r"[^\w']+", " ", s).split())


def quote_check(dossier: str, fetched: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """Match every quote in a claim against the text of the pages the agents fetched.
    Returns claim ids (verified, not found, no fetched page to check against)."""
    by_url: dict[str, list[str]] = {}
    for f in fetched:
        by_url.setdefault(_norm(f["url"]), []).append(_norm_text(f["text"]))
    every_page = [t for texts in by_url.values() for t in texts]
    ok, missing, unchecked = [], [], []
    for m, entry in _entries(claims_section(dossier), ENTRY):
        if EPISODE_SOURCE.search(entry):
            continue  # episode quotes are listed separately, for checking against the transcript
        quotes = [q for q in QUOTE.findall(re.split(r"\bSource:", entry, maxsplit=1)[0]) if len(q) >= 25]
        if not quotes:
            continue
        urls = [_norm(u) for u in URL_IN_TEXT.findall(entry)]
        pages = [t for u in urls for t in by_url.get(u, [])]
        cid = f"C{m.group(1)}"
        if not pages:
            # Not fetched at that URL: still look in every page read, in case of a redirect.
            pages = every_page
            if not pages:
                unchecked.append(cid)
                continue
        found = True
        for q in quotes:
            # An ellipsis marks a cut: each piece must appear.
            for piece in re.split(r"\s*(?:\.\.\.|…)\s*", q):
                p = _norm_text(piece)
                if len(p.split()) >= 3 and not any(p in page for page in pages):
                    found = False
        (ok if found else missing).append(cid)
    return ok, missing, unchecked


def source_check(dossier: str, seen: set[str], fetched: list[dict] | None = None) -> str:
    """List cited URLs that never came back from a search or fetch, entries with no URL,
    episode-only claims, and quotes that do not match the fetched page."""
    seen_n = {_norm(u) for u in seen}
    cited = {u.rstrip(".,;:") for u in URL_IN_TEXT.findall(dossier)}
    unseen = sorted(u for u in cited if _norm(u) not in seen_n)

    no_url, episode_only = [], []
    entries = _entries(claims_section(dossier), ENTRY)
    for m, entry in entries:
        if EPISODE_SOURCE.search(entry):
            episode_only.append(f"C{m.group(1)}")
        elif not URL_IN_TEXT.search(entry):
            no_url.append(f"C{m.group(1)}")

    lines = ["", "## Source check (generated by the research stage)", "",
             f"- Numbered claims: {len(entries)}",
             f"- URLs retrieved by search or fetch: {len(seen)}",
             f"- URLs cited in this dossier: {len(cited)}"]
    if not entries:
        lines.append("- WARNING: no numbered [Cn] claims found. The draft stage needs them.")
    if no_url:
        lines.append(f"- Claims with no source URL: {', '.join(no_url)}")
    if episode_only:
        lines.append("- Claims sourced only to the episode (the researchers worked from the map, not the "
                     f"transcript; check these against the transcript): {', '.join(episode_only)}")
    if unseen:
        lines.append("- Cited URLs that no search or fetch returned. Check these by hand; "
                     "the model may have written them from memory:")
        lines += [f"  - {u}" for u in unseen]
    if fetched is not None:
        ok, missing, unchecked = quote_check(dossier, fetched)
        lines.append(f"- Quotes matched word for word against the fetched page: {len(ok)}")
        if missing:
            lines.append("- Quotes NOT found in any fetched page (misquoted, paraphrased, or from a page "
                         f"the agent did not fetch). Check before using: {', '.join(missing)}")
        if unchecked:
            lines.append(f"- Quotes with no fetched page to check against: {', '.join(unchecked)}")
    if entries and not no_url and not unseen:
        lines.append("- Every claim has a URL, and every cited URL was retrieved in this run.")
    return "\n".join(lines) + "\n"


# --- the stage ----------------------------------------------------------------------------------

def estimate(cfg: dict, n_topics: int, critic_rounds: int | None = None) -> float:
    """Upper-end cost of the research still to do, for the cost guard."""
    rc, st = cfg["research"], cfg["stages"]
    agent = llm.estimate_call(cfg, cfg["models"]["researcher"], st["research_topic"]["expected_input_tokens"],
                              st["research_topic"]["expected_output_tokens"], rc["searches_per_topic"])
    critic = llm.estimate_call(cfg, cfg["models"]["writer"], 80_000, st["research_critic"]["expected_output_tokens"])
    synth = llm.estimate_call(cfg, cfg["models"]["writer"], 60_000, st["research_synthesis"]["expected_output_tokens"])
    rounds = rc["rounds"] - 1 if critic_rounds is None else critic_rounds
    return (n_topics + rounds * rc["max_followups"]) * agent + rounds * critic + synth


def _inputs_hash(angle: str, episode_map: str) -> str:
    return hashlib.sha256((angle + "\0" + episode_map).encode()).hexdigest()[:12]


INPUTS_LINE = re.compile(r"^<!-- research inputs: (\w+) -->$", re.M)


def _rounds_done(r: Run) -> tuple[int, bool]:
    """(last critic round run, whether the critic has declared the research complete)."""
    last, complete = 1, False
    for p in sorted(r.path("research_dir").glob("critic_round*.md"),
                    key=lambda p: int(re.search(r"(\d+)", p.stem).group(1))):
        last = int(re.search(r"(\d+)", p.stem).group(1))
        complete = not parse_topics(p.read_text(encoding="utf-8"))
    return last, complete


def run(r: Run, cfg: dict, plan_only: bool = False, topics: str | None = None, replan: bool = False):
    angle = r.read("angle")
    episode_map = r.read("map")
    if r.path("dossier").exists() and not r.force and not plan_only:
        raise StageError("Output already exists: 04_dossier.md. Use --force to rebuild it "
                         "(topic findings already paid for are kept).")
    rc = cfg["research"]
    if replan and r.path("research_dir").exists():
        old = r.root / f"04_research.old-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(r.path("research_dir")), old)
        print(f"  moved the old research to {old.name}/")
    r.path("topics_dir").mkdir(parents=True, exist_ok=True)

    inputs = _inputs_hash(angle, episode_map)
    if r.path("research_plan").exists():
        plan = r.read("research_plan")
        m = INPUTS_LINE.search(plan)
        if m and m.group(1) != inputs:
            raise StageError("03_angle.md or 01_map.md has changed since the research plan was made. "
                             "Run research --replan to start over (the old research is moved aside, not "
                             "deleted), or delete the 'research inputs' line at the end of "
                             "04_research/plan.md to keep the plan.")
        print("  using the existing 04_research/plan.md")
    else:
        plan = make_plan(r, cfg, angle, episode_map)
        plan = plan.rstrip() + f"\n\n<!-- research inputs: {inputs} -->\n"
        r.path("research_plan").write_text(plan, encoding="utf-8")
    all_topics = parse_topics(plan)
    if not all_topics:
        raise StageError("04_research/plan.md has no '## T1: Title' topics.")
    print(f"  plan: {len(all_topics)} topics: " + "; ".join(f"T{t['id']} {t['title']}" for t in all_topics))
    if plan_only:
        print(f"\n  Plan written. Estimated research cost: up to ${estimate(cfg, len(all_topics)):.2f}.\n"
              "  Edit 04_research/plan.md if you like, then run research again.")
        return r.path("research_plan")

    redo = set()
    if topics:
        redo = {int(x) for x in re.findall(r"\d+", topics)}
        unknown = redo - {t["id"] for t in all_topics}
        if unknown:
            raise StageError(f"Topics not in the plan: {sorted(unknown)}")
    todo = [t for t in all_topics if t["id"] in redo or not topic_paths(r, t["id"])[0].exists()]

    # Critic rounds pick up where earlier runs stopped, and never run for an explicit --topics redo.
    last_round, complete = _rounds_done(r)
    rounds_left = 0 if (topics or complete) else max(0, rc["rounds"] - last_round)

    cap = rc["max_usd"]
    if todo or rounds_left:
        est = estimate(cfg, len(todo), rounds_left)
        llm.guard(r, cfg, f"research ({len(todo)} topics now, up to {rounds_left} critic round(s); "
                          f"hard stop at ${cap:.2f})", est)
    spent_before = _research_spend(r)

    def spent() -> float:
        return _research_spend(r) - spent_before

    def check_spend(_in_call: float = 0.0) -> None:
        if spent() >= cap:
            raise StageError(f"research has spent ${spent():.2f}, the research.max_usd cap. Finished "
                             "topics are saved; raise the cap in config.yaml and re-run to continue.")

    t0 = time.monotonic()
    errors = run_topics(r, cfg, todo, angle, episode_map, plan, check_spend) if todo else []
    if errors:
        raise StageError("some research agents failed; their topics will be retried on the next run:\n  "
                         + "\n  ".join(errors))

    for round_no in range(last_round + 1, last_round + 1 + rounds_left):
        if spent() >= cap:
            print(f"  stopping follow-ups: research has spent ${spent():.2f} of the ${cap:.2f} cap")
            break
        new = critique(r, cfg, round_no, angle, plan)
        if not new:
            break
        plan = r.read("research_plan")
        errors = run_topics(r, cfg, new, angle, episode_map, plan, check_spend)
        if errors:
            raise StageError("some follow-up agents failed; re-run research to retry them:\n  "
                             + "\n  ".join(errors))

    dossier = merge(r, cfg, angle, plan)
    out = r.write("dossier", dossier)
    print(f"  {len(ENTRY.findall(claims_section(dossier)))} numbered claims from {len(parse_topics(plan))} topics in "
          f"{(time.monotonic() - t0) / 60:.1f} min, ${spent():.2f} this run. "
          "See the source check at the end of 04_dossier.md.")
    return out


def _research_spend(r: Run) -> float:
    return sum(rec.get("cost_usd", 0) or 0 for rec in r.read_log() if rec.get("stage", "").startswith("research"))
