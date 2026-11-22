# The debrief's earpiece (Ian, 2026-11-22)

Ian, after testing with friends: "build in the earpiece workflow into the post demo interview to help guide the flow and
questions. Right now the questions are a little generic, not bad, but not connecting or acting like a real product
interview." His choices (2026-11-22):
- **Length:** the same ~2 minutes, sharper (the ceiling stays: the opening, at most two more questions, one follow-up).
- **What it may know:** the tester's own words and the chapter's outline: what they asked for and why, their lines to the
  guide, the outline's titles and what each part covers (not the script), their mid-chapter questions with the guide's
  answers.
- **Seeing it:** the floating panel during the debrief (testers with the earpiece view on), and Earpiece history in admin.

Why both a brief and live looks: a debrief has 2 to 4 interviewer turns in about two minutes, and a whisper worded to be
used "when it fits" took two or three turns to land with the guide (voice/gpt_live/REPORT.md). So (1) the interviewer gets a
code-built brief of the visit in its instructions from the start (no model call, no wait), which makes its own questions
specific; and (2) the earpiece looks *while the tester is still answering*, so its note for the next question is in place
as they finish, and a note whose turn has been taken is dropped rather than used a turn late.

## 1. The visit brief (server, code only)

`demo/debrief.py` `visit_brief(listener_dir, cfg) -> str` (at most `BRIEF_CHARS` = 3500 characters; empty-safe), from the
newest live chapter (`latest_chapter`) and the guide's transcripts:
- **What they asked for:** `00_source/seed.md` Topic, "What the listener said", "Why it matters" (sections parsed by
  heading; "(not said)" / placeholder values dropped).
- **What they said to the voice:** their own lines (`who: listener`) from the guide conversation(s) of this visit
  (`sessions/*.jsonl`, the newest conversation that has listener lines, plus the one before it if the chapter was
  started in it), oldest first, each cut to 200 characters, the oldest dropped first past 1200 characters in all.
- **The piece:** the outline's title and each part's title + `lands` (05_outline.json), each marked heard / partly heard /
  not heard from live/reading.jsonl paragraph ids (`s<n>-p<kk>`); just the card's title and summary if there is no
  outline yet; "stopped before it was finished" when it was.
- **Their questions mid-piece:** each `ask` aside (12_asks/asides.jsonl): their first line and the voice's first answer
  line, each cut to 200 characters, with `debrief.moments`' "about N minutes in".
- **Rehearsal** (fast.json `rehearsal`): one line saying the piece was a stand-in replay, not made from their request,
  and the outline is left out (they never heard a piece about what they asked).

It uses the debrief prompt's own words ("the piece", "the voice"), and is filled into a new `{{visit}}` in
`prompts/interviewer_debrief.md` under THEIR VISIT, with the rules: for choosing and wording questions; never recite it;
quote at most a few of their own words back; ask about one specific thing from it in their terms.
`values_from` gains `visit` (default "" for the sims), and the debrief session row records `visit`.

## 2. The earpiece (server)

`demo/debrief_earpiece.py` (a sibling of voice/gpt_live/director.py: it reuses `director.shape`'s cleaning through
parameters, `director.cap_words`, `director.merge_recent` and the plan model; the guide's code paths are unchanged):
- `SCHEMA`: `{read, plan: [{item, state: open|done|dropped}], next, changed, kind: next_turn|now|null, about:
  follow_up|thread|moment|idea|unanswered|wrap|null, note}`. `shape(got)` = director.shape with these KINDS/ABOUT.
- `look(...)`: the user message, `prompts/interviewer_director_moment.md`: elapsed, trigger (`opened`, `hearing`,
  `turn_end`), whether the tester is mid-answer (their words so far), the count of the interviewer's questions so far
  (lines with "?"), whether `time_up` was sent, the `did` account, the visit brief, its previous plan and read
  (`memory` from its own rows), its whispers so far, the debrief transcript (interviewer/listener lines with secs).
- System prompt `prompts/interviewer_director.md`: Ian's earpiece in a two-minute product debrief. Its default plan:
  the fixed opening; follow the thread in their first answer (the moment behind it, in their words); then the one thing
  from their visit most worth asking (a moment, something they said to the voice that the piece did or didn't pick up,
  a part they heard, an idea of theirs) or one of Ian's topics; close once there are two or three specific comments.
  Product-interview craft (specific past moments, not hypotheticals; their words; surprise, confusion, delight; one
  open question; never leading). It keeps the ceiling and WHEN TO END; never suggests asking about what didn't happen;
  `wrap` once there is enough (the note says to say the farewell, word for word); nothing after `time_up` but `wrap`.
- `ask(cfg, s, req, write, lock=None)`: caps from config `demo.interviewer.director` (`max_calls`, `max_usd`), the call on
  a thread with `timeout_ms`, stage `interviewer_director` (config `stages.interviewer_director`: Sonnet 5.5, effort low),
  cost logged to study/log.jsonl as `{"stage": "interviewer_director", "kind": "interviewer", "session": sid, ...}`
  (demo/state.cost reads it), and one row in the debrief transcript:
  `{"who": "director", "id", "trigger", "read", "plan", "next", "changed", "kind", "about", "note", "ms", "cost_usd",
  "error"?}` (never `text`). Any failure is no whisper plus `error`, never an exception.
- `whisper_in(text)`: the page note as delivered (`prompts/interviewer_notes.md` `direct_debrief` wrapper) -> note, or None.
  `voice/gpt_live/earpiece_history.py` treats a page note as a whisper if `director.whisper_in` or this matches.

Routes (in `demo/interviewer.py` `mount`, debrief sessions only, `get(sid)` scoping as the others):
- `POST /api/interview/direct` `{sid, id, trigger, recent: [{who, text}], hearing: str, page: {seconds, questions,
  time_up}}` -> `{id, read, plan, next, changed, kind, about, note, ms, cost_usd, error?}`.
- `POST /api/interview/direct/fate` `{sid, id, fate: sent|held|dropped, reason?, detail?, secs?, held_ms?}`, reasons from
  `director.FATE_REASONS` plus `moved_on`; recorded as `{"who": "director_fate", ...}` (no `text`).
- `/api/interview/start` (debrief) answers `notes.direct` (the wrapper with `{note}`) and `settings.direct` (the page
  knobs below, `enabled` false when off or no Anthropic key path is configured), and `earpiece_view`
  (`director.view_on(cfg)`, the tester's flag) so live.js can show the panel.

Config `demo.interviewer.director`: `enabled: true`, `hear_words: 8`, `hear_every_ms: 5000`, `turn_merge_ms: 1500`,
`timeout_ms: 5000`, `max_calls: 20`, `max_usd: 0.30`.

## 3. The earpiece (page: page/interview.js, debrief mode only, when `settings.direct.enabled`)

No product words (tests/demo/test_gate_page.py PRODUCT): names like `direct`, `look`, `whisper`, `onLook` are fine.
- **Looks** (one in flight; `I.look = {id, ctl, askedAt, ...}`):
  - `opened`: once, right after the opening is sent.
  - `hearing`: while the tester speaks (`I.inp` growing): when their current turn (lines since the interviewer's last
    line, plus `I.inp`) reaches `hear_words` words, then again every `hear_every_ms` while they keep talking.
  - `turn_end`: their line flushed and `turn_merge_ms` passed with no new words, unless a `hearing` look was asked
    in this turn after its last `hear_words` words (it already covers the turn).
  - Never once `I.closing`, `I.farewellAt`, `I.act`, `I.pending`, `I.cues.time` or `I.cues.wrap`, or after 2 failures.
  - Body: the last 10 lines (`I.recent`, kept as lines flush), `hearing: I.inp`, `page: {seconds, questions, time_up}`.
- **Delivery** of a look with `changed && note`, at once by `instruct(notes.direct with {note})` (bypasses `gapOk`, but
  sets `I.noteAt`/`I.noteKind = "direct"` so `waiting`/`hanging` wait their gap after it). Fates:
  - `dropped/moved_on`: the interviewer finished a line with a question, or of `reply_words`+ words, after the look was
    asked (its turn was taken: the note would be used a turn late).
  - `dropped/replaced`: a newer look's note came first. `dropped/not_here` (detail: closing, farewell, time) at the
    unsafe states above. `dropped/down`: two failures. `sent` otherwise.
  - Each fate is posted to `/api/interview/direct/fate` (fire-and-forget; never "ended").
- **The panel:** `Interview.start("debrief", {..., onLook(ev)})`: interview.js calls `onLook` with the same event shapes
  live.js's `dirEmit` uses (`conversation`, `ask`, `look`, `fail`, `down`, `fate`, `line` with who `guide` for the
  interviewer's lines and `listener` for theirs, `note`), wrapped in try/catch, never awaited. live.js passes an `onLook`
  that stamps `session: "debrief-<sid>"` and feeds `L.dirEvents` / `window.Earpiece.event` when the panel is loaded
  (`L.earpieceLoaded`, or `got.earpiece_view` from the debrief's start, which loads it). page/earpiece.js stops hiding
  under `body.debrief` while it has debrief events, and labels that conversation "Debrief".

## 4. Admin history

`voice/gpt_live/earpiece_history.py`: `conversations(folder)` also lists `study/debrief-*.jsonl` as session
`debrief-<sid>` (label "Debrief"); `timeline(folder, "debrief-<sid>")` reads that file. The id pattern accepts the prefix.
demo/visit.py keeps leaving whispers and looks out (it reads `whisper` from timeline_of, which now knows both wrappers).

## 5. Checks

Offline: tests for the brief (seed, lines, outline heard marks, asides, rehearsal, size cap), the earpiece module (shape,
look text, caps, rows without text, cost log), the routes (409, fate validation), the page (looks, delivery, moved_on,
fates, panel events, no product words), the history listing debrief files. Live, small (demo/build_costs.jsonl): a
simulated debrief with a visit, the earpiece on (demo/sim_interviewer.py), to see the whispers land.
