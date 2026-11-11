# Add-on: Ledger-Driven Research (v2)

Add-on to the Commissioned Audio Chapter MVP. It replaces the single research stage with a ledger of ideas and one research agent per ledger item. A coverage check after drafting reuses the same ledger.

Build this before the full run. It changes what gets written, so it must not be added between the full run and the blind comparison.

## Goal

Research should be aimed by the chosen angle, not by general reading. Each idea the chapter needs becomes a ledger row. Each row gets its own research agent, which brings back both facts and tellable material. The same ledger later shows which ideas the script actually landed.

Depth beats breadth: a 40-minute chapter lands 5-7 ideas well, not 10 thinly.

## Where it fits in the pipeline

```
01 map ─┐
        ├─> 03 angle [Ian] ─> 03b ledger [Ian] ─> 04 research (one agent per row) ─> 04b ledger update
00 src ─┘                                                                              │
                                                        05 brief <─────────────────────┘
                                                           │
                                         06 draft ─> 07 assemble ─> 07b coverage check ─> 08 edit
```

New stages: `03b ledger`, `04b ledger update`, `07b coverage`. Stage 04 is rewritten.

## The ledger

Source of truth: `runs/<run>/ledger.json`. Render `ledger.md` (a table) from it after every stage so Ian can read it; Ian hand-edits the JSON if needed.

**5-7 active rows, ranked by importance to the angle.** Good ideas that don't make the cut go in a `parked` list in the same file, with one line each. Parked ideas are not researched; they are seeds for a later chapter.

| Column | Written by | Meaning |
|---|---|---|
| `id` | 03b | L01, L02, ... |
| `idea` | 03b | One sentence: what the chapter must land |
| `rank` | 03b | 1 = most important |
| `type` | 03b | `deep-dive` (the episode raises it but skims it), `missing` (the angle needs it, the episode doesn't cover it), or `covered` (the episode already gives enough) |
| `episode_has` | 03b | What the transcript already says, with a short pointer to where |
| `research_question` | 03b | The specific question research must answer. For `covered` rows: the episode's factual claims to verify |
| `tier_needed` | 03b | Minimum source tier to settle it (see Source tiers) |
| `status` | 04b | `filled`, `partial` or `open` |
| `dossier_refs` | 04b | Dossier entry numbers that serve it, e.g. D04, D07 |
| `best_tier` | 04b | Best source tier found |
| `coverage` | 07b | `covered`, `thin`, `missing` or `repeated` |
| `segments` | 07b | Segment numbers where it appears |
| `note` | 07b | One line for Ian: what to fix, if anything |

## Source tiers

| Tier | Sources |
|---|---|
| 1 | Primary texts (Project Gutenberg, Perseus, publisher editions) |
| 2 | Academic references: Stanford Encyclopedia of Philosophy, Internet Encyclopedia of Philosophy, university presses, peer-reviewed papers |
| 3 | Serious magazines, published lectures, interviews with the author |
| 4 | Everything else |

Every dossier entry records its tier. Any claim a segment's argument rests on needs tier 1 or 2. Tier 4 may be used for color only, never for a factual claim.

## Stage 03b: ledger

- **Input:** `00_source/transcript.txt`, `01_map.md`, `03_angle.md`.
- **Model:** Opus 5.5. Full transcript plus the map; no summarizing step.
- **Output:** `ledger.json`, `ledger.md`.
- **Prompt asks for:**
  - The 5-7 ideas the angle needs, ranked, plus a parked list.
  - For each, what the episode already gives and whether it is `covered`, `deep-dive` or `missing`.
  - For `deep-dive` and `missing` rows, one research question each, narrow enough to answer within its search budget, and the tier needed.
  - For `covered` rows, the specific factual claims from the episode that the chapter would repeat.
- **[Ian] checkpoint:** stop. Ian reviews the ledger and can add, cut, re-rank, park or rewrite rows before research runs.

## Stage 04: research (rewritten)

- **Input:** `ledger.json`, `03_angle.md`, `01_map.md`.
- **One agent per active row.** Model: Opus 5.5 with the Claude API's web search and web fetch tools.
- **Each agent's brief, built from its row:**
  - The research question and why the chapter needs it (the `idea` plus the angle thesis).
  - The tier needed.
  - What the episode already says, so it isn't repeated.
  - The one-line `idea` of every other active row, so the agent stays in its lane and can flag connections.
- **Search budget by rank and type** (total cap 40 searches; stop and ask Ian before exceeding):

  | Row | Searches | Fetches |
  |---|---|---|
  | `deep-dive` or `missing`, rank 1-3 | 8 | 4 |
  | `deep-dive` or `missing`, rank 4-7 | 4 | 2 |
  | `covered` (verify only) | 2 | 1 |

- **What each agent must return** (`04_research/L0N.md`), as numbered entries:
  - **Claims:** the factual points, each with source URL and tier.
  - **Tellable material:** at least one concrete scene, story, example or image that makes the idea land for a listener, with source. A row with claims but nothing tellable is `partial`.
  - **Primary quote:** one short quote worth reading aloud, if one exists.
  - **Tension:** the strongest objection or complication to the idea, with source.
  - **Connections:** any link noticed to another row's idea, one line each.
- **Quote rule:** a direct quote must be copied from a page the agent actually fetched and marked `verbatim: yes` with its URL. Anything recalled or reconstructed is written as paraphrase and marked `verbatim: no`. The script may only quote `verbatim: yes` entries.
- **`covered` rows get a verify-only agent:** check the episode's factual claims against tier 1-2 sources and record `confirmed`, `corrected` (with the correction) or `unverified`. Speakers in a live conversation misremember details; the chapter must not repeat an error.
- **Run agents in parallel** where the rate limit allows; otherwise in rank order.
- **Merge:** concatenate into `04_dossier.md`, renumber entries D01, D02, ..., keep the row tag and entry kind (claim, material, quote, tension, connection) on each, and drop exact duplicates across agents.

## Stage 04b: ledger update

- **Input:** `ledger.json`, `04_dossier.md`.
- **Model:** Opus 5.5. Deciding whether a question is really answered is a judgment call, and the cost is small.
- **Sets** `status`, `dossier_refs` and `best_tier` for each row. `filled` requires: the question answered at the needed tier, and tellable material present.
- **Writes** a short `connections` section at the top of `ledger.md`: the cross-row links the agents flagged, merged and deduplicated. The brief uses these for transitions and the chapter's through-line.
- **Follow-up round:** any row still `open` or `partial` gets one more agent run with the gap stated explicitly (4 searches, 2 fetches). One follow-up round at most. Whatever is still open is flagged in `ledger.md` for Ian.

## Stage 05: brief (changes)

- The brief states the chapter's **through-line** first: the one question or tension that carries the listener from the first segment to the last. Ledger rows serve the through-line; the chapter is an argument with a shape, not a tour of the rows.
- Every active row is assigned to at least one segment, with the dossier entries that segment should use, including its tellable material and, where it helps, its tension.
- `corrected` facts from verify agents are listed explicitly so the draft uses the corrected version.

## Stage 07b: coverage check

- **Input:** `07_script_draft.md`, `ledger.json`, `05_brief.md`.
- **Model:** Opus 5.5, fresh context.
- **Sets** `coverage`, `segments` and `note` for each row:
  - `covered`: the idea is clearly landed, with something concrete.
  - `thin`: mentioned but not developed, or stated abstractly with no example.
  - `missing`: not in the script.
  - `repeated`: made more than once without adding anything.
- **Also reports:**
  - Drift: any substantial idea in the script that maps to no ledger row.
  - Whether the through-line is audible from start to finish, in two or three sentences.
  - Any direct quote in the script not backed by a `verbatim: yes` entry.
- **Output:** updated `ledger.json` and `ledger.md`, plus `07b_coverage.md`, which the stage 08 editor reads alongside the draft.
- **No score.** This is a report for Ian and the editor, not a number to optimize.

## Not in scope

- A 0-100 score or any automatic revise-until-it-passes loop.
- A separate summarizing pass before the ledger. Add one only when the source grows past what fits comfortably in one call (several long episodes).
- Domain authority or backlink metrics.
- Researching parked ideas.

## Build order

1. Ledger data model (`ledger.json` schema with active and parked rows, render to `ledger.md`).
2. Stage 03b and the Ian checkpoint. Run it on the Scarry transcript with the chosen angle; stop for Ian.
3. Stage 04 per-row agents with budgets, the quote rule and cost logging. Run the rank-1 agent first and show Ian its output before running the rest.
4. Stage 04b with connections and the follow-up round.
5. Brief changes in stage 05.
6. Stage 07b, wired into the stage 08 editor's inputs.

Log every call to `log.jsonl` with stage, row id, model, tokens and cost, as in the main build.
