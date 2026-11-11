"""Forecast what the rest of a run will cost, from config.yaml and the files so far.

Rough by design: token counts are estimated from text length, and each stage's
output size from the expected_* settings in config.yaml. The point is to see
the order of magnitude before spending, and where the money goes.
"""

from __future__ import annotations

from . import ledger, llm, research
from .common import Run, StageError


def _tokens(r: Run, key: str, default_words: int) -> int:
    p = r.path(key)
    if p.exists() and p.is_file():
        return llm.estimate_tokens(p.read_text(encoding="utf-8"))
    return int(default_words * 1.4)


def forecast(r: Run | None, cfg: dict) -> list[tuple[str, float, bool]]:
    """[(stage, estimated USD, already done)]"""
    st, models, ch = cfg["stages"], cfg["models"], cfg["chapter"]
    opus = models["writer"]

    def done(key: str) -> bool:
        return r is not None and r.path(key).exists()

    def tok(key: str, words: int) -> int:
        return _tokens(r, key, words) if r is not None else int(words * 1.4)

    transcript = tok("transcript", 9000)
    episode_map = tok("map", 3000)
    dossier = tok("dossier", 12000)
    brief = tok("brief", 2500)
    led_tokens = tok("ledger", 2000)
    draft = int(ch["target_words"] * 1.4)
    n_seg = ch["max_segments"]

    # The ledger's rows if it exists; otherwise the most rows the add-on allows, the top ranks
    # deep-dive or missing and one covered row.
    rows = None
    if r is not None and r.path("ledger").exists():
        try:
            rows = ledger.active(ledger.load(r, cfg, render=False))
        except StageError:
            rows = None
    if rows is None:
        n = cfg["research"]["max_rows"]
        rows = [{"rank": i, "type": "covered" if i == n else "deep-dive"} for i in range(1, n + 1)]
    todo = [x for x in rows if r is None or "id" not in x or not research.row_paths(r, x["id"])[0].exists()]

    def est(stage: str, input_tokens: int, model: str = opus, searches: int = 0) -> float:
        return llm.estimate_call(cfg, model, input_tokens, st[stage]["expected_output_tokens"], searches)

    style = 600
    judge = est("ledger_update", dossier + led_tokens + 1500)
    rows_out = [
        ("map", est("map", transcript + 800), done("map")),
        ("angles", est("angles", episode_map + 600), done("angles")),
        ("ledger", est("ledger", transcript + episode_map + 2500), done("ledger")),
        ("research (upper end)", research.estimate(cfg, [(x, False) for x in todo]), done("dossier")),
        # Upper end: every row needs a follow-up, so the judge runs twice.
        ("ledger update + follow-ups (upper end)",
         2 * judge + research.estimate(cfg, [(x, True) for x in rows]), done("ledger_update")),
        ("brief", est("brief", dossier + led_tokens + style + 2500), done("brief")),
        # Each segment call re-reads the cached context at the cache-read rate.
        ("draft", n_seg * est("draft", 0) + llm.token_cost(
            cfg, opus, cache_write_5m=dossier + brief + style,
            cache_read=(n_seg - 1) * (dossier + brief + style) + n_seg * draft // 2), done("script_draft")),
        ("coverage", est("coverage", draft + brief + led_tokens + 3000), done("coverage")),
        ("edit", est("edit", draft + brief + dossier + style + 2000), done("edit_notes")),
        ("revise", n_seg * est("revise", 0) + llm.token_cost(
            cfg, opus, cache_write_5m=dossier + brief + style + draft,
            cache_read=(n_seg - 1) * (dossier + brief + style + draft) + n_seg * draft // 2), done("script_final")),
        ("baseline", est("baseline", st["baseline"].get("expected_input_tokens", transcript),
                         searches=cfg["baseline"]["max_searches"]), done("baseline_script")),
    ]
    chars = ch["target_words"] * 6
    per_chapter = chars * cfg["narration"]["price_per_1000_chars"] / 1000
    rows = rows_out + [
        ("narrate pilot (2 segments)", per_chapter * 2 / n_seg, r is not None and (r.path("audio_dir") / "pilot.mp3").exists()),
        ("narrate chapter", per_chapter, done("chapter_mp3")),
        ("narrate baseline", per_chapter, done("baseline_mp3")),
    ]
    return rows


def run(r: Run | None, cfg: dict) -> None:
    rows = forecast(r, cfg)
    spent = sum(rec.get("cost_usd", 0) or 0 for rec in r.read_log()) if r is not None else 0.0
    print(f"  {'stage':40} {'estimate':>9}")
    for stage, usd, is_done in rows:
        print(f"  {stage:40} {'$' + format(usd, '.2f'):>9}{'   (done)' if is_done else ''}")
    remaining = sum(usd for _, usd, d in rows if not d)
    claude = sum(usd for s, usd, d in rows if not d and not s.startswith("narrate"))
    print(f"\n  remaining: about ${remaining:.2f} (Claude ${claude:.2f}, ElevenLabs ${remaining - claude:.2f})"
          + (f"; spent so far ${spent:.2f}" if r is not None else ""))
    print("  Research and the follow-ups are upper ends: most rows need no follow-up.")
