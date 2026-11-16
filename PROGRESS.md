# Tester Demo: progress

Spec: `docs/tester-demo.md`. Design and the contracts between its five parts: `docs/tester-demo-design.md`.
Branch `feature/tester-demo`. Deploy: `DEPLOY.md`. Checks for a person: `NEEDS_HUMAN.md` (Tester Demo section).
Interviewer check costs: `demo/build_costs.jsonl`.

## Build order

| Step | State |
| --- | --- |
| 1. Tester links, cookies, folders, consent | Done (demo/testers.py, demo/routes.py) |
| 2. Gate and interviewer screens | Done (page/gate.html, page/blank.html); no product words (tested) |
| 3. Interviewer: intro, name, handover, reveal | Done (demo/interviewer.py, page/interview.js); live: behaves, but the voice didn't delegate in 4 of 4 intros, so the page hands over and the server takes the name from the transcript |
| 4. Demo settings: 10 minutes, overnight off, card, I'm done | Done (faststart/base.sized_overrides, faststart/card.py, page/live.js) |
| 5. Handback, debrief, saved transcript/audio/summary, debrief card | Done; live debrief 3 runs: all five questions in order, real moments, thanks by name |
| 6. Admin page | Done (/admin, page/admin.html) |
| 7. Hosting and deploy | Files done (deploy/, DEPLOY.md, `wl deploy`); the droplet is Ian's to set up |
| 8. Study report | Done (`wl study report`), tested with three seeded testers and a fake model |

Underneath: the server now keeps each tester's listener per request (a cookie, a ContextVar), their own
pipeline and conversation, and a build queue of 3 (server.py). With tester mode off nothing changes.

## Review (2026-11-16)

Five lenses (isolation, server state, page flow, interviewer and study data, deploy), each finding checked by a
skeptic: 17 confirmed (11 distinct), all fixed with regression tests, each fix reviewed again; that review found
three more (two name-fallback gaps, the CLI tests loading the repo's .env, an open page's catch-up pass during an
admin reset), also fixed. Highlights: an admin reset or delete waits for memory passes and stops builds that
survived a restart; memory locks are per listener; the interviewer's name fallback needs real capitals and skips
stop words; curly apostrophes no longer stop the debrief closing; one debrief per tester (a later chapter doesn't
hand back again); "I'm done" counts the minutes actually heard; a refused mic asks again instead of typing;
the backup accepts the droplet's new host key; `wl study create` on the droplet prints the real link.
Full suite after the fixes: 1,648 passed.

## Acceptance checks

| # | Check | State |
| --- | --- | --- |
| 1 | New link makes a folder with demo settings; token gone after the redirect | Passed (tests/demo/test_testers.py, test_demo_flow.py) |
| 2 | No valid link or cookie, or a revoked link: only the dot | Passed (tests) |
| 3 | No product text in the page source before the handover | Passed (test_gate_page.py, test_demo_flow.py) |
| 4 | The intro never says what the product is (10 simulated runs, some asking) | 4 live runs, all deflected ("You'll see in a moment"), no product words; run the full 10: `python -m demo.sim_interviewer intro --runs 10` (about $0.50) |
| 5 | The guide uses the name and never asks for it | Passed offline (the handoff note and profile.md carry it); by voice: NEEDS_HUMAN |
| 6 | The card shows during onboarding; the chapter is 9-11 minutes | Card: passed offline. Length: the 10-minute sizing validates 2- and 3-section outlines; a real run is in NEEDS_HUMAN |
| 7 | Chapter end and "I'm done" both hand back | Passed (tests/demo/test_demo_page.py) |
| 8 | Debrief: five questions in order, real moments, thanks by name; no leading questions (10 runs) | 3 live runs: all five in order, real moments, thanks by name, no leading; run the full 10: `python -m demo.sim_interviewer debrief --runs 10 --grade` (about $1.50) |
| 9 | Leaving mid-chapter shows the debrief card; it goes once the debrief is done | Passed (tests) |
| 10 | Admin page: stage, minutes, moments, debrief, cost; revoke, reset, delete | Passed (tests/demo/test_admin.py, test_state.py) |
| 11 | HTTPS on the droplet; mic and audio on iPhone Safari, Android Chrome, laptop | NEEDS_HUMAN |
| 12 | Two testers live at once don't see each other's data or interfere | Passed offline (tests/demo/test_server_testers.py); by two people: NEEDS_HUMAN |
| 13 | `wl study report` from three seeded runs | Passed (tests/demo/test_report.py, fake model) |

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
