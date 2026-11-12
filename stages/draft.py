"""Stage 6: brief + dossier + earlier segments -> 06_segments/segNN.md

One call per segment, in order. Each call sees the style guide, the full brief,
the full dossier, this segment's dossier entries (its 'Dossier:' line), and the
full text of every earlier segment. The style guide, brief and dossier are identical across calls
and sit in a cached system prompt; the earlier segments get a second cache
breakpoint so each call reuses the one before.

Existing segment files are kept, never overwritten, unless --force is given.
"""

from __future__ import annotations

from . import llm
from .brief import parse_segments
from .common import (Run, StageError, dossier_entries, entry_id, load_prompt, parse_range, prompt_name,
                     word_count)


def context_block(r: Run, style_guide: str, brief: str, dossier: str) -> str:
    return load_prompt(prompt_name(r, "draft_context"), style_guide=style_guide, brief=brief, dossier=dossier,
                       source_tiers=load_prompt(prompt_name(r, "source_tiers")))


def run(r: Run, cfg: dict, segments: str | None = None):
    brief = r.read("brief")
    dossier = r.read("dossier")
    style_guide = load_prompt("style_guide")
    segs = parse_segments(brief)
    if not segs:
        raise StageError("05_brief.md has no '## Segment N: Title' headings.")
    entries = dossier_entries(dossier)
    wanted = parse_range(segments, [s["n"] for s in segs])
    by_n = {s["n"]: s for s in segs}

    system = context_block(r, style_guide, brief, dossier)
    seg_words = cfg["chapter"]["segment_words"]

    # Stage-level cost guard, ignoring cache savings so it errs high.
    todo = [n for n in wanted if r.force or not r.segment_path(n).exists()]
    st = cfg["stages"]["draft"]
    est = sum(llm.estimate_stage(cfg, "draft", llm.estimate_tokens(system) + (n - 1) * 1100,
                                 st["expected_output_tokens"]) for n in todo)
    llm.guard(r, cfg, f"draft ({len(todo)} segments)", est)
    written = []
    for n in wanted:
        out = r.segment_path(n)
        if out.exists() and not r.force:
            print(f"  seg{n:02d}: exists, keeping it (use --force to redraft)")
            continue
        earlier = []
        for k in range(1, n):
            p = r.segment_path(k)
            if not p.exists():
                raise StageError(f"Draft seg{k:02d} before seg{n:02d}: each segment builds on the ones before it.")
            earlier.append(p.read_text(encoding="utf-8").strip())

        seg = by_n[n]
        if not seg["entries"]:
            print(f"  seg{n:02d}: warning, the brief lists no dossier entries for this segment")
        missing = [c for c in seg["entries"] if c not in entries]
        if missing:
            print(f"  seg{n:02d}: warning, brief cites entries not in the dossier: "
                  f"{', '.join(entry_id(c) for c in missing)}")
        entries_text = "\n\n".join(entries[c] for c in seg["entries"] if c in entries) or "(none listed)"

        content = []
        if earlier:
            content.append({
                "type": "text",
                "text": load_prompt("draft_earlier", earlier="\n\n".join(
                    f"<segment n=\"{k}\">\n{t}\n</segment>" for k, t in enumerate(earlier, 1))),
                "cache_control": {"type": "ephemeral"},
            })
        content.append({"type": "text", "text": load_prompt(
            "draft",
            n=n, total=len(segs), segment_brief=seg["text"], entries=entries_text,
            target_words=seg["target_words"] or f"{seg_words[0]}-{seg_words[1]}",
            first_or_later=("This is the first segment: open with the hook from the brief, and orient the "
                            "listener within the first minute, as the brief's opening describes."
                            if n == 1 else
                            "Continue directly from where the previous segment ended. Do not recap it."),
            last=("This is the last segment: end on the closing line from the brief. The chapter stands "
                  "alone: no preview of another chapter, no 'where we go next', no sign-off."
                  if n == len(segs) else "Do not wrap up the chapter; more segments follow."),
        )})

        res = llm.call(r, cfg, "draft", system=system, messages=[{"role": "user", "content": content}],
                       label=f"draft seg{n:02d}")
        text = res.text.strip()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        words = word_count(text)
        print(f"  seg{n:02d}: {words} words -> {out.relative_to(r.root)}")
        written.append(out)
        if r.force:
            later = [k for k in by_n if k > n and r.segment_path(k).exists() and k not in wanted]
            if later:
                print(f"  note: seg{later[0]:02d} onward were drafted against the old seg{n:02d}.")
    return written
