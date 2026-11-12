"""Live check of every API assumption the pipeline rests on, for about $0.10.

Run first when the keys are in place. Each check prints PASS or FAIL with what
it saw, so a wrong assumption shows up here and not halfway through a $20
research run.

Claude (one small research request, as a ledger research agent makes it, and one
tiny structured-output request, as the ledger, ledger-update and coverage stages make it):
  - web search and web fetch run, and results come back as the blocks we parse
  - the fetched page's text is captured, so quotes can be checked against it
  - usage reports searches and tokens; the cost lands in log.jsonl
  - the refusal-fallback beta and effort setting are accepted
  - output_config.format (JSON schema) is accepted alongside them, and returns parseable JSON
ElevenLabs (one short sentence):
  - the configured model exists, and its live per-request limit
  - the configured (or first candidate) voice synthesizes in the configured format
  - the response carries a request id, which request stitching needs
"""

from __future__ import annotations

from collections import Counter

from . import llm, research, tts
from .common import Run, StageError, parse_json

URL = "https://plato.stanford.edu/entries/plato/"


def _report(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def reply_quote_found(text: str, pages: list[str]) -> bool | None:
    """Whether the sentence the reply copied (its longest quoted passage) is on a fetched page.

    Only the longest quote is checked: Opus 5.5 also puts the search query it ran in quotation
    marks ("I searched for "Diotima Symposium ...""), which is not on the page.
    """
    quotes = research.QUOTE.findall(text)
    return research.quote_found(f"“{max(quotes, key=len)}”", pages) if quotes else None


def check_claude(r: Run, cfg: dict) -> bool:
    print("Claude:")
    try:
        res = llm.call(
            r, cfg, "smoketest", role="researcher", label="smoketest research",
            messages=[{"role": "user", "content": (
                "Search the web once for 'Diotima Symposium Stanford Encyclopedia', then fetch "
                f"{URL} and reply with one exact sentence copied from that page, in quotation marks.")}],
            tools=research.web_tools(cfg, 2, 1), max_tokens=4000, effort="low", expected_output_tokens=1000,
        )
    except StageError as e:
        return _report("request", False, str(e))
    types = Counter(getattr(b, "type", "?") for b in res.content)
    got = research.retrieved(res.content)
    ok = _report("request", True, f"model {', '.join(res.usage.served_by)}, blocks {dict(types)}")
    ok &= _report("web search ran", res.usage.web_searches > 0 or "web_search_tool_result" in types,
                  f"{res.usage.web_searches} searches reported, {len(got['urls'])} URLs seen")
    ok &= _report("fetched page text captured", bool(got["fetched"]),
                  f"{len(got['fetched'])} page(s), {sum(len(f['text']) for f in got['fetched']):,} characters"
                  if got["fetched"] else "no web_fetch_result with text; quote checks would find nothing")
    if got["fetched"]:
        pages = [research._norm_text(f["text"]) for f in got["fetched"]]
        verified = reply_quote_found(res.text, pages)
        ok &= _report("quote check", bool(verified),
                      "the reply's quote was found on the fetched page" if verified
                      else f"the reply's quote was not found on the page (reply: {res.text[:120]!r})")
    ok &= _report("cost logged", res.usage.cost_usd > 0, f"${res.usage.cost_usd:.4f}")
    ok &= check_structured(r, cfg)
    return ok


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["word", "count"],
          "properties": {"word": {"type": "string", "enum": ["ready"]}, "count": {"type": "integer"}}}


def check_structured(r: Run, cfg: dict) -> bool:
    try:
        res = llm.call(r, cfg, "smoketest", label="smoketest structured output",
                       messages=[{"role": "user", "content": "Return the word ready and the count 3."}],
                       json_schema=SCHEMA, max_tokens=2000, effort="low", expected_output_tokens=200)
        data = parse_json(r, res.text, "smoketest")
    except StageError as e:
        return _report("structured output", False, str(e))
    return _report("structured output", data.get("word") == "ready" and isinstance(data.get("count"), int),
                   f"model {', '.join(res.usage.served_by)}, returned {data}")


def check_elevenlabs(r: Run, cfg: dict) -> bool:
    print("ElevenLabs:")
    ncfg = cfg["narration"]
    try:
        limit = tts.model_char_limit(ncfg["model_id"])
    except StageError as e:
        return _report("models", False, str(e))
    ok = _report("model", limit is not None,
                 f"{ncfg['model_id']} max {limit} chars/request" if limit
                 else f"{ncfg['model_id']} not in the account's model list; run `python cli.py voices`")
    voice = ncfg.get("voice_id") or (ncfg.get("candidate_voices") or [{}])[0].get("voice_id")
    if not voice:
        return _report("voice", False, "no voice_id and no candidate voices in config.yaml") and False
    try:
        syn = tts.synthesize("Diotima taught Socrates about love.", voice_id=voice, ncfg=ncfg)
    except StageError as e:
        return _report("synthesis", False, str(e)) and False
    rate = tts.pcm_rate(ncfg["output_format"])
    secs = f", {len(syn.audio) / 2 / rate:.1f}s of audio" if rate else ""
    ok &= _report("synthesis", len(syn.audio) > 1000, f"voice {voice}, {len(syn.audio):,} bytes{secs}")
    ok &= _report("request id", bool(syn.request_id),
                  syn.request_id or "no request-id header; stitching will fall back to previous_text")
    out = r.root / "smoketest.wav"
    tts.write_wav(out, syn.audio, ncfg["output_format"])
    print(f"  listen: {out}")
    r.log({"stage": "smoketest", "provider": "elevenlabs", "characters": 36,
           "cost_usd": round(36 * ncfg["price_per_1000_chars"] / 1000, 5)})
    return ok


def run(r: Run, cfg: dict, anthropic: bool = True, elevenlabs: bool = True) -> None:
    results = []
    if anthropic:
        results.append(check_claude(r, cfg))
    if elevenlabs:
        results.append(check_elevenlabs(r, cfg))
    if not all(results):
        raise StageError("Smoke test found a problem; see the FAIL lines above.")
    print("\nAll checks passed.")
