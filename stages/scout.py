"""Stage 1 for a seed run: seed -> 01_map.md (+ 01_scout.sources.json)

A short research pass (config.yaml scout: 10 searches, 5 fetches) that maps the
territory around the listener's seed and finds candidate sources. It plays the
role the episode map plays for a transcript, so it writes 01_map.md, which the
angles, choose and ledger stages read.

Its budget holds the same way the research agents' does: a tool loop that
pauses after using any of its budget is not resumed (max_uses resets on each
request); one call without tools writes the map from what it already read.
"""

from __future__ import annotations

import json

from . import llm, research
from .common import Run, StageError, listener_profile, load_prompt, prompt_name


def run(r: Run, cfg: dict):
    seed = r.read("seed")
    r.check_writable("map")
    searches, fetches = cfg["scout"]["searches"], cfg["scout"]["fetches"]
    prompt = load_prompt("scout", seed=seed, profile=listener_profile(cfg), searches=searches, fetches=fetches,
                         source_tiers=load_prompt(prompt_name(r, "source_tiers")),
                         target_minutes=cfg["chapter"]["target_minutes"])
    llm.guard(r, cfg, "scout", research.agent_estimate(cfg, searches, fetches, "scout"))
    res = llm.call(r, cfg, "scout", role="researcher", label="scout",
                   messages=[{"role": "user", "content": prompt}],
                   tools=research.web_tools(cfg, searches, fetches), expected_searches=searches,
                   preapproved=True, resume_if=lambda u: u.web_searches == 0 and u.web_fetches == 0)
    got = research.retrieved(res.content)
    text, cost = res.text, res.usage.cost_usd
    if res.paused:
        w = llm.call(r, cfg, "scout_writeup", role="researcher", label="scout write-up", preapproved=True,
                     messages=[{"role": "user", "content": load_prompt(
                         "scout_writeup", instructions=prompt, notes=text or "(none)",
                         material=research.material_text(got))}])
        text, cost = w.text, cost + w.usage.cost_usd
    r.path("scout_sources").write_text(json.dumps({
        **got, "searches": res.usage.web_searches, "fetches": res.usage.web_fetches,
        "budget": {"searches": searches, "fetches": fetches}, "written_up": res.paused,
        "cost_usd": round(cost, 4)}, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        territory = research.extract_tagged(text, "map")
    except StageError:
        partial = r.root / "_partial_scout.md"
        partial.write_text(text, encoding="utf-8")
        raise StageError(f"scout: no <map> block; raw output saved to {partial.name}.") from None
    out = r.write("map", territory)
    print(f"  01_map.md: the territory, from {res.usage.web_searches} searches and {len(got['fetched'])} pages "
          f"read, ${cost:.2f}")
    return out
