# Tester Demo: design and contracts

How the spec (docs/tester-demo.md) is built on the existing code. Written 2026-11-16 from a map of the code at
9b9d14a. Five parts are built in parallel; this file is the contract between them. Change a contract here first.

## What the code does today, and what has to change

- **One listener, one conversation, one chapter at a time.** `common.listener_dir(cfg)` resolves
  `listeners/<active.json id>/`; server.py has one `Pipeline` (one run, `busy()` refuses a second) and one
  `chat` (one current `guide.Session`; `new_session()` ends the previous one). Several testers live at once
  needs the listener per request, a Pipeline and a chat per listener, and a server-wide build limit.
- **Keys:** clean today. Only the SDP goes through our server (`session.create_webrtc`); the browser gets the
  SDP answer, never a key or a client secret. Media flows browser to OpenAI directly. Keep it that way.
- **Fast runs ignore `chapter.minutes`** and their floors (opening 6 min + 2 x 3 min) make 10 minutes invalid.
- **The outline's title arrives about 3.5 min after start_chapter**; the card needs its own call at t = 0.
- **`memory.meeting()`** treats any `sessions/*.jsonl` with a listener line as a past conversation, so
  interviewer transcripts must never go in `sessions/` (the guide would skip introducing itself).
- **The guide's voice is fixed per GPT-Live session** (and instructions are immutable): interviewer, guide and
  debrief are three successive sessions on one page, sharing the mic stream.
- **GPT-Live voices** (probed 2026-11-16): gleam, ash, sage, verse, cedar, marin, alloy, coral all start a session;
  a made-up name is refused ("Voice session access denied"). The interviewer uses `cedar` (config).

## Parts and owners

| Part | Module(s) | Owns |
| --- | --- | --- |
| A. Per-tester server | stages/common.py, server.py, stages/guide.py, stages/memory.py, voice/gpt_live/routes.py, overnight/live.py, overnight/routes.py, faststart/guide_hooks.py, ask/routes.py | listener per request, per-listener Pipeline + chat, build limit of 3 with a queue, thread pinning, the 10-minute run sizing hook, per-tester reset |
| B. Testers, admin, study | demo/testers.py, demo/state.py, demo/admin.py, demo/report.py, demo/cli.py, page/admin.html | token registry, /t/<token>, cookie, folders, consent, admin page and actions, per-tester stats, `wl study report` |
| C. Interviewer | demo/interviewer.py, demo/debrief.py, prompts/interviewer*.md, prompts/interviewer_tools.json, page/gate.html, page/interview.js | the interviewer's GPT-Live sessions (intro, debrief), its routes, moments and chapter context for the debrief, transcripts/audio/summary in study/, the gate screen, the handoff into the guide |
| D. Demo chapter | page/live.js, page/live.html, page/library.js, voice/gpt_live/notes.md, faststart/ (sizing), prompts/fast_card.md | the chapter card, the 10-minute fast sizing, "I'm done", the guide's handback at chapter end, tester mode on the product page, the debrief card in the library, the reveal and cool-down transitions |
| E. Hosting | deploy/ (Caddyfile, systemd units, env example, backup), DEPLOY.md, `wl deploy`, server host/port flags | the droplet setup Ian runs tonight |

## Contracts

### Config (config.yaml, new top-level `demo:` block; each part adds only its own keys)

```yaml
demo:
  enabled: false            # tester mode: cookies required, gate, interviewer. Off: the app as today (Ian's own)
  cookie: wl_tester         # the cookie holding the tester's token
  cookie_days: 365
  secure_cookie: auto       # auto: Secure when the request came over https (X-Forwarded-Proto from Caddy)
  ground: "#F5F2ED"         # the gate's ground color
  dot_neutral: "#6B635C"    # the interviewer's dot
  max_builds: 3             # chapter builds running at once, server-wide; more wait in a queue (part A)
  chapter_minutes: 10       # testers' chapter length (written into their settings.json)
  interviewer:              # part C
    voice: cedar
    intro_max_minutes: 3
    debrief_max_minutes: 9
    debrief_target_minutes: 8
```

The tester folder's own settings come from `profiles.save_settings(cfg, {"chapter": {"minutes": 10}, "overnight":
{"enabled": False}}, listener=<dir>)`.

### Identity (A provides, everyone uses)

- `stages/common.py`: `REQUEST_LISTENER: ContextVar[str | None]`; `active_listener_id()` checks it first (then
  `$WRITE_LISTEN_LISTENER`, then active.json). `cfg["listener_dir"]` still wins (tests, pinned runs).
- `common.listener_scope(lid)`: a context manager setting `REQUEST_LISTENER`; `common.pinned(cfg)` returns
  `{**cfg, "listener_dir": <the current listener's folder as an absolute path>}` for code that hands cfg to a
  thread.
- A pure ASGI middleware in server.py sets the scope per request from `demo.testers.listener_for_request(scope)`
  (B), which reads the cookie and returns `"t-<slug>"` or None. With `demo.enabled` and no valid tester, only
  these answer: `GET /` (the blank dot page), `GET /t/{token}`, `/admin*` (B guards it), `/healthz`. Everything
  else returns 404 with an empty body. With `demo.enabled: false`, nothing changes.
- Request state: `request.state.tester` is the tester record (dict) or None.

### Per-listener server state (A)

- `server.state_for(lid) -> State(pipe: Pipeline, chat: dict(session, lock, passes))`, created on first use,
  behind a registry lock. The routes mounted with `pipe`/`chat` get thin proxies that dispatch to the current
  listener's state, so existing route code keeps working.
- `Pipeline.start()` always sets `WRITE_LISTEN_LISTENER` to its listener id for the child.
- Build limit: `demo.max_builds` runs in state making at once, server-wide (count live pids over
  `listeners/*/chapters/*/status.json`). A start beyond it is queued: the run folder is created with a
  server-written `status.json` `{state: "making", stage: "queued", label: "Waiting its turn", queued: true}` and
  started when a slot frees (a watcher thread). `guide.plain_status` says it is waiting its turn. The queue is
  persisted in `shared/build_queue.json`.
- Threads started from a request pin their listener (memory passes, the early scout, cost savers).
- `/api/reset` for a tester: refused (403); the admin's reset (B) does it. For Ian's own use (demo off), unchanged.
- **10-minute sizing hook:** `Pipeline.start_seed` writes `<run>/config.yaml` with `fast:` overrides when the
  listener's `chapter.minutes` differs from the default. The override values come from
  `faststart.base.sized_overrides(minutes) -> dict` (D owns the values).

### Testers (B provides)

- `demo/testers.py`:
  - `create(cfg, label) -> {token, lid, label, link}`; tokens are `secrets.token_urlsafe(24)` (32 characters).
  - registry `shared/testers.json` `{"tokens": {token: {"lid", "label", "created"}}}`; tester record
    `listeners/<lid>/tester.json` `{label, lid, token, created, opened_at, consent_at, name, debrief_card:
    "auto"|"on"|"off", stage_hint}`.
  - `lookup(token) -> record | None`, `listener_for_request(scope) -> lid | None` (cookie), `revoke(lid)`,
    `reset(lid)` (moves the folder aside to `listeners/_cleared_<time>/<lid>` and recreates it, same token),
    `delete(lid)` (removes folder and token), `set_name(lid, name)` (tester.json, and one line in profile.md:
    "Their name is <name> (they told the interviewer just now).", or "(Ian gave it when he invited them)." for a
    name given at creation), `record_consent(lid)`, `set_debrief_card`,
    `update(lid, **fields)` (merge into tester.json; C sets `intro_done` at the handoff).
  - lid is `t-<slug(label)>`, unique (a numeric suffix on collision).
- Routes: `GET /t/{token}` sets the cookie (HttpOnly, SameSite=Lax, Secure per `secure_cookie`), creates the folder
  on first open with the demo settings, redirects 302 to `/`. Unknown or revoked: the blank dot page.
  `POST /api/demo/consent` records consent (the gate's tap calls it).
- `GET /` with `demo.enabled`: no tester → `page/blank.html` (ground + dot, no text); a tester whose intro isn't
  done (no `intro_done` in tester.json) → `page/gate.html` (C); otherwise `page/live.html` with `class="tester"`
  on body.
- `GET /app` (tester only): `{"style": <live.html's <style> text>, "body": <live.html's body markup without its
  <script> tags>}` for the gate to inject at the handoff (one document keeps the tap's audio and mic unlock).
- Admin `GET /admin` (page/admin.html) and `/api/admin/*` JSON: list, create, revoke, reset, card, delete, and the
  study files (summary, transcripts). Guarded: loopback always allowed; otherwise HTTP basic auth against
  `ADMIN_USER`/`ADMIN_PASSWORD` from the environment (Caddy also guards it on the droplet), else 403.
- `demo/state.py`: `tester_state(cfg, lid) -> {stage, last_active, title, minutes_heard, chapter_minutes,
  questions, debrief: {done, summary, transcript}, cost: {claude, gpt_live, total, estimated}}`; stages:
  not opened, intro, onboarding, chapter, debrief, done (and "left early" as a flag).
- `wl study report` (demo/cli.py, `wl study ...` dispatched from cli.py) writes `study/report.md` at the repo
  root (gitignored).

### Interviewer (C provides)

- Storage: `listeners/<lid>/study/` (never `sessions/`): `intro-<sid>.jsonl`, `debrief-<sid>.jsonl` (same line
  format as session transcripts: `{"t", "who", "text", ...}`), `log.jsonl` (usage and Claude costs, same record
  shape as sessions/log.jsonl), `<live_id>.wav` / `.mp3` (fetched from OpenAI after the session, store: true),
  `debrief_summary.md`, `debrief.json` `{done, finished_at, topics: [...], closing}` (the topics that came
  up, `interviewer.TOPICS`; before Ian's first run it was `questions_asked: [1..5]`).
- Routes `/api/interview/{start,tool,line,usage,end}`: `start {mode: "intro"|"debrief", sdp}` → `{sid, live_id,
  sdp}`; tools: `set_listener_name {name}`, `handoff_to_guide {}` (intro), `end_debrief {}` (debrief).
  `end {sid}` closes, fetches audio in the background, and for a debrief writes the summary (Claude, cost in
  study/log.jsonl).
- `demo/debrief.py`: `moments(listener_dir) -> [{at_s, clock, words}]` (from the newest live chapter's
  `12_asks/asides.jsonl`, kind ask, listener lines; at_s estimated from the paragraph's position at
  `live.page.words_per_second`), `chapter_context(listener_dir) -> {title, summary, minutes_heard, minutes}`.
- Page: `page/gate.html` (served by B's `/`): the dot, "Tap to begin", the consent line, and
  `<script src="/interview.js">`. **No product words anywhere in gate.html or interview.js** (chapter, library,
  guide, listen, story, audio-lesson...): use neutral words (session, handover, welcome).
- `window.Interview = { start(mode, {stream, onNear, onHandoff, onDone}), end(), busy() }`. During the intro the
  gate fetches `GET /app` (the product page's body markup, cookie-gated, served by B/D) and the scripts
  /api/interview/start named (`next`) into memory, never into the document. When the handover's words are heard
  the page calls `handoff_to_guide` itself (the voice rarely does) and `onNear` fires: the gate injects the
  markup under itself, runs the scripts, calls `Live.handoff({stream, hold: true})` (D: the guide's session
  opens, silent and deaf) and grows its dot into `Live.spot()`. Once both sides are quiet (`handover_quiet_ms`)
  the interviewer's session closes and `onHandoff({name, stream})` calls `Live.handoff({name, stream})`.
  (Ian's notes, 2026-11-16: it was about 20 s from the interviewer's last words to the guide's first; now about 2.)

### Demo chapter (D provides)

- `Live.handoff({name, stream, hold})` in live.js: reuses the stream, connects the guide with `/api/live/start
  {sdp, handoff: {...}}`; the server sends and records no opening for it. With `hold` the session is opened
  ahead (mic not sent, muted, nothing said to it) until the call without `hold`; then the page sends the notes
  key `handoff` ("This session picks up from the person who welcomed them; they just told them their name is
  {name}...") as speakable context, with the name as known then, and records it (/api/live/line). A held
  session never let go of (`hold_max_ms`) is closed and its conversation ended: with no line of the listener's
  it is not a meeting. `Live.spot()`: the circle's place, size and colour, for the gate's dot.
  The reveal: the neutral dot warms to the coral circle while connecting; the screen fades in at the guide's
  first word.
- Chapter card: at `start_chapter` (server side, part A's start_seed hook calls `faststart.card.start(run_root,
  cfg)` in a thread) one Sonnet call (prompts/fast_card.md, schema {title, overview, summary}) writes
  `<run>/fast/card.json`; `GET /api/live/card/{run}`. The page no longer shows it (see "After Ian's first tester
  run" below); the debrief brief and the admin page read it, and the library shows its overview on the
  finished chapter.
- 10-minute sizing: `faststart.base.sized_overrides(minutes)`; prompts/fast_outline*.md wording that assumes
  three or more sections is softened.
- Tester mode (`body.tester`, injected when the request is a tester's): hides Start fresh, Stop making this
  chapter, typing, the library's Settings and overnight wording.
- "I'm done" (`#done`, shown during the chapter, tester mode only) and chapter end: the guide gets the notes key
  `handback_done` / `handback_end` (close in a sentence or two, then say it's handing them back), then the page
  tears the guide session down (usage, memory pass, archive as now) and calls `Interview.start("debrief",
  {stream, onDone})`; the screen cools (product fades to ground, coral to neutral) during the debrief, and the
  library appears after.
- The library's debrief card: `library_state` adds `debrief: {show, line}` for a tester whose debrief isn't done
  (and whose card isn't turned off); its button calls `Interview.start("debrief", ...)`.

### Hosting (E provides)

- `deploy/Caddyfile` (reverse proxy to 127.0.0.1:8765, automatic HTTPS, basic auth on /admin*),
  `deploy/write-listen.service` (uvicorn, restart on failure, EnvironmentFile), `deploy/env.example`,
  `deploy/backup.sh` (rsync listeners/ to Ian's machine, run from Ian's machine), `DEPLOY.md`, `wl deploy`
  (git pull, uv sync, restart). server.py takes `--host/--port` (env `WL_HOST`/`WL_PORT`).

## Tests

Each part adds tests under `tests/demo/` (pytest, fakes as in tests/fakes.py, no network). Page parts use headless
Chromium like tests/live/test_live_page.py. Run the whole suite before committing.

## After Ian's first tester run (2026-11-16)

What changed from the spec (`docs/tester-demo.md`) and from the contracts above:

- **The intro.** The interviewer is given the tester's name (the admin form, `wl study create --name`, or a label
  that reads as a first name; labels like "Pilot" or "Design Team" are not names, and it asks as before). It
  opens "Hi, is this Rolando?", says Ian built what it is about to show them and how the session will go, takes
  questions, and hands over. About 60 to 90 seconds, not "about a minute" of three lines.
- **The handover.** The page acts on the words "hand you over now" as they are said, only once the tester has
  spoken and the intro is `handover_min_ms` old; the prompt keeps "hand you over" for its final line. The
  guide's session opens ahead of the handover, silent and deaf, and is let go of once both sides are quiet:
  about 2 s between the two voices. If that session fails after the handover the screen comes up and says why.
- **The debrief** is a conversation around topics (what stood out, would they use it, where it could go, the
  voice, asking questions, anything confusing), not five fixed questions in order. It replies to each answer and
  answers direct questions; the closing question and the thanks by name are kept.
- **No chapter card while the chapter is made.** The page shows the steps to the opening, then the chapter's
  parts. The card is still written at the start for the debrief and the admin page.
- **The play button** is on a bottom bar fixed to the screen, not on the circle. It shows whenever this
  conversation's chapter is ready and not yet started; a tap on the circle talks or rests.
- **The pitch nudge waits for real interest**: held after the guide says what it does and while its latest
  question is answered, sent in a lull; after a no the guide asks which of the other things they mentioned
  they'd rather hear about.
