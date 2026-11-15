# Overnight Chapters: progress

Spec: `docs/overnight-chapters.md`. Branch `feature/overnight`. Report with every result, decision and cost:
`overnight/REPORT.md`. Design and contracts: `overnight/DESIGN.md`. Checks for a person: `NEEDS_HUMAN.md`.
Build costs per check: `overnight/build_costs.jsonl`.

## Build order

| Step | State |
| --- | --- |
| 1. Profiles and storage | Done |
| 2. Planner | Done; live on real data (check 3) |
| 3. Overnight runner | Done; live end to end (checks 5-7) |
| 4. Library page | Done; browser-tested |
| 5. Morning playback | Built; browser-tested with GPT-Live faked; needs a person (12, 13) |
| 6. Change by voice | Built; browser-tested with GPT-Live faked; needs a person (11) |

## Acceptance checks

| # | State |
| --- | --- |
| 1 | Pass (migration copied into `listeners/ian/`; both chapters open) |
| 2 | Pass |
| 3 | Pass, live ($0.10 a pass) |
| 4 | Pass |
| 5 | Pass, live (`2026-11-16-some-bridges-sway-wind`) |
| 6 | Pass, live, after a fix this check found: 50/50 paragraphs within two words on Whisper, 43 word for word |
| 7 | Pass, live: 19.5 minutes; the card's summary kept |
| 8 | Pass (offline kills mid-research and mid-recording; a live resume after a stop at assembly) |
| 9 | Pass |
| 10 | Needs a person (`NEEDS_HUMAN.md`) |
| 11 | Code passes; by voice needs a person |
| 12 | Code passes; the ten runs need a person |
| 13 | Code passes; needs a person |

Offline suite: see the last commit message for the count.
