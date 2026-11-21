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
| 4. Demo settings: 10 minutes, overnight off, card, End demo (it was I'm done) | Done (faststart/base.sized_overrides, faststart/card.py, page/live.js); the card is no longer shown while the chapter is made (Ian, 2026-11-16): it is the library's, on the finished chapter |
| 5. Handback, debrief, saved transcript/audio/summary, End demo for a returner (it was the library's debrief card) | Done; live debrief 3 runs: all five questions in order, real moments, thanks by name (superseded 2026-11-18 by the short debrief, below) |
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

## After Ian's second tester run (2026-11-18)

His notes: a shorter intro, a stray "tap" at the handover, too many questions from the guide and not eager
enough, an End demo button in place of "Got five minutes to tell Ian how it went?", a much shorter debrief that
is told what the tester did. Built by three agents, reviewed (four lenses, each finding checked by a skeptic),
and the confirmed findings fixed with regression tests. What changed: docs/tester-demo-design.md, "After Ian's
second tester run"; the guide's results: voice/gpt_live/REPORT.md.

| Note | State |
| --- | --- |
| 1. Intro: one question, one turn, "any questions or ready?" | Done; 2 live intros (38 s; 73 s with a tester who asked twice what it is). The second-person wording tuned afterwards is not checked live |
| 2. The "tap" label at the handover | Fixed (the gate's state classes moved off `<body>`, which live.js rewrites); tested |
| 3. The guide: fewer questions, eager, uses silence | Done; 5 live conversations (3 of 13 and 3 of 7 turns asked something after tuning; pitch at 2:07 and 1:24, Ian's was 4:50). The review's wording fixes (not in its first two turns; ask about one particular thing) are not checked live. Whether it sounds peppier: Ian's ear |
| 4. End demo | Done; every phase tested offline, the handback checked live (9 short runs). Not tried on a phone or with a real microphone |
| 5. Debrief: far fewer questions, told what they did | Done; 3 live debriefs (86 to 114 s). One asked three more questions, not two: the prompt now says to keep count and the exact check counts new topics; not rerun live |
| 6. Debrief: a clear exit (his note on that debrief: "the interviewer kept asking questions"; "let's end the conversation here" got the closing question) | Done: the debrief ends itself on one farewell ("Thank you, <name>. That really helps Ian. That's everything from me."), the closing question is optional, the page and the server key on the farewell; tested offline (his debrief is a fixture that fails the new `exit` check). 2 live debriefs (72 s and 73 s, both ended by the tool, no closing question): one ended after two answers, one after the first. A spoken "let's end here" was not reached live (it ended first); that path is tested offline only. Review follow-ups (offline tests only, not rerun live): a debrief nobody answered is never marked done (End demo comes back); the debrief's own `waiting` note names the farewell (no "carry on"); "sound finished" needs one specific comment first and a farewell spoken over is said again; the `asking_*` lines owe no question; the sim's `DONE` pattern, moment rule and new `too_soon` check |

Live checks for this round: $0.46 interviewer (demo/build_costs.jsonl) and $0.15 for the two exit checks, about
$1.09 guide and page (voice/gpt_live/build_costs.jsonl, now about $17.3 of its $20 cap).

## After Ian's third tester run (2026-11-19)

His notes: the guide less engaging than in his conversations with Pooja and Purnima; End demo should stop the
chapter being made; the debrief stalled and only asked questions; the intro talked too much; try a listening
agent over GPT-Live that decides when to do things. Built by three agents, reviewed (four lenses, 23 findings
confirmed by skeptics, all fixed with tests), then small live checks. Contracts: CLAUDE.md, "After Ian's third
tester run".

| Note | State |
| --- | --- |
| The guide's warmth | Persona and talk rules restored from 720f7d5 (the commit Pooja's conversation ran on); pep and pace kept; starts only on a clear yes, a question about the pitch answered first; the offer to chat after the start is real (the page waits for the answer). 4 live persona conversations: warm, playful lines ("You cook like you're patient. Am I right?"); one wrong day ("Friday" on a Thursday) |
| End demo stops the chapter | Done (`server.stop_for_debrief`); tested offline, including that a stop never signals the server's own pid. Not run live |
| The debrief | No delegation, so nothing to wait on; stall lines don't count as replies; a "left hanging" note. 2 live debriefs: no stalls, both ended on the farewell (57 s, 105 s). One turned twice on an acknowledgement alone; "what else?" and "see what I mean?" not reached live |
| A briefer intro | 47 words after "Hi, is this ...?"; 28 s live, handed over on the words |
| The director | Built and on by default (`live.director`); replayed on 25 moments (21 acceptable, no wrong active move). Live, the persona mirror only asks after guide lines: 12 decisions, all `wait`. With it on, the offer clock is off, which removed the mid-story cut-in seen with it off; the offer came later (2:25 against 1:17). Its own moves are untested live |

| The earpiece (2026-11-20; replaces the director) | Ian: a background earpiece with a plan it checks off and changes; the guide keeps all its style. Built (prompt, server, page, persona mirror, four scripted probes), reviewed (four lenses, 17 findings confirmed, all fixed with tests). Live, $0.77: probes B, C, D used the whisper at the guide's 2nd or 3rd turn (21-40 s), at a lull or wind-down, never voiced, never over the listener; A (mid-story) gave no pitch in the gaps, the probe closed too soon to say more; a replacing whisper won. Natural runs (evening, reserved): 22 looks, 0 whispers. The director's own whisper is not yet heard live |

| Rehearsal chapters (2026-11-20) | Built (`faststart/rehearsal.py`): a tester set to rehearsal gets the heron chapter replayed on its own timeline (opening 90 s, part 2 at 5:44), nothing paid; per tester (`--rehearsal`, admin page). Reviewed (12 findings confirmed, all fixed with tests); checked offline on the real server and page: steps, countdowns, reading, End demo stopping it, the real path unchanged |
| The guide's peek (2026-11-20) | Built (`faststart/peek.py`): get_status carries a short peek from the run's files (about, question, the part being written, research under way, one finding), no model call. Live ($0.05): asked "how's it coming along?", the guide teased the refraction idea; "what are you working on?", the herons' learning |
| After run 4: checked against what they said (2026-11-20) | Built: conversation.md saved at the start, one rule in the writer's and editors' prompts, a code list of "you said" sentences for the opening edit and lock, a code backstop on listener_words. Reviewed (7 findings confirmed, all fixed with tests). Two real builds ($4.09): no invented claims; "argued" cut to "discussed"; "today" lines led to a when/where clause. The lock's rewording not exercised live |
| After run 4: one question before a thin pitch (2026-11-20) | Built: earpiece rule, an `offered` look at the guide's offer, gap exemption, `moved_on`. Replay ($0.25): question for Pooja, Purnima, reserved; none for Ian's runs. Persona run ($0.23): whisper at 1:06, asked after the explanation, pitch on her answer. Not heard by Ian |

Live checks for this round: $0.70 guide and director (voice/gpt_live/build_costs.jsonl, now $18.34 of its $20
cap), $0.20 interviewer (demo/build_costs.jsonl). Full suite: 1840 passed before the last two fixes.

## Acceptance checks

| # | Check | State |
| --- | --- | --- |
| 1 | New link makes a folder with demo settings; token gone after the redirect | Passed (tests/demo/test_testers.py, test_demo_flow.py) |
| 2 | No valid link or cookie, or a revoked link: only the dot | Passed (tests) |
| 3 | No product text in the page source before the handover | Passed (test_gate_page.py, test_demo_flow.py) |
| 4 | The intro never says what the product is (10 simulated runs, some asking). Since Ian's first run (2026-11-16) it knows the name, says Ian built it and how the session will go (60-90 s), and hands over only on "I'll hand you over now." | 4 live runs before the rewrite and 2 after, all deflected ("You'll see in a moment"), no product words; the handover words and their guard: passed offline (test_gate_page.py, test_interviewer.py); run the full 10: `python -m demo.sim_interviewer intro --runs 10` (about $0.50) |
| 5 | The guide uses the name and never asks for it | Passed offline (the handoff note and profile.md carry it); by voice: NEEDS_HUMAN |
| 6 | No card during onboarding (Ian, 2026-11-16, after his tester run: it changes the spec's "the card shows during onboarding"): while the chapter is made the page shows the steps to the opening, then the chapter's parts (from the opening being written), and the play button on the fixed bottom bar once it is ready; the library shows the card's overview on the finished chapter (not its summary). The chapter is 9-11 minutes | Page: passed offline (tests/demo/test_demo_page.py, tests/live/test_live_page.py). Length: the 10-minute sizing validates 2- and 3-section outlines; a real run is in NEEDS_HUMAN |
| 7 | Chapter end and End demo (it was "I'm done") both hand back; End demo from every phase goes to the debrief | Passed (tests/demo/test_demo_page.py); the handback checked live through the real page (2026-11-18) |
| 8 | Debrief (since Ian's first run, 2026-11-16: a conversation around his topics that replies to each answer, not five questions in order): at least three topics, their real moment named, direct questions answered, the closing question, thanks by name; no leading questions (10 runs) | 3 live runs of the new debrief: passed, no silences; run the full 10: `python -m demo.sim_interviewer debrief --runs 10 --grade` (about $1.50) |
| 9 | A returning tester whose debrief isn't done sees End demo (it was the library's debrief card); it goes once the debrief is done | Passed (tests) |
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
