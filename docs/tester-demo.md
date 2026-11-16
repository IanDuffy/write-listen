# Spec: Tester Demo

Ian Duffy, 2026-11-16. Copied from the Claude doc "Spec: Tester Demo"
(https://claude.ai/artifact/4DPdMCRyohUhke287hsXqA); the doc is the source if they differ.

## Overview

Each of 10–15 testers gets a private link. It opens to a dot and "Tap to begin". A plain-voiced interviewer
thanks them and gets their name, then hands them to the guide. The guide's onboarding, a short chapter and the
live questions are the product demo. At the end the guide hands them back to the interviewer for a short
debrief, and the interviewer thanks them by name.

**Decisions**

| Decision | Setting |
| --- | --- |
| Testers | Pilot with 2 for 3–4 days, then the rest (10–15 total) |
| Accounts | None. The link is the tester |
| Interviewer | A separate agent with its own instructions and voice. Never describes the product. Thanks them, gets their name, runs the debrief |
| Guide | Unchanged instructions, same narrator voice for every tester |
| Voice reveal | None claimed. The handoff from interviewer to guide is the reveal |
| Chapter length | 10 minutes for testers (`chapter.minutes: 10`) |
| Overnight chapters | Off for testers (`overnight.enabled: false`) |
| Screen | Only the dot and "Tap to begin" until the handoff to the guide |
| Hosting | One DigitalOcean droplet, on a subdomain such as listen.ianduffy.com |

**Reused:** onboarding, fast start (sections, check-in), ask by voice, the library and chapter screens, listener
folders and reset, the episode page.

## The demo flow

1. **Open the link.** The gate screen: the dot, "Tap to begin", and one line on recording (see Tester links and
   consent).
2. **Tap.** The interviewer connects and speaks first. It thanks them for their time, asks their name, and says
   it would like a short chat with them afterwards. About a minute. It says nothing about what the product is
   or does.
3. **Handoff to the guide.** The interviewer says it's handing them over, then its session closes. The dot
   changes into the guide's breathing circle during the connecting state, the product screen appears, and the
   guide says hello and names itself. The guide already has the tester's name and doesn't ask for it again.
4. **Onboarding,** exactly as built. Once the topic is set, the chapter card (title, overview, summary) appears
   on screen while the conversation continues, then the chapter starts as fast start already does.
5. **The chapter,** 10 minutes, with tap to ask.
6. **Handoff back.** When the chapter ends, the guide says a sentence or two to close, then that it's handing
   them back to the interviewer. If the tester taps "I'm done" (shown during the chapter), the guide does the
   same right away.
7. **Debrief,** 5–8 minutes (see The interviewer).
8. **Thanks.** The interviewer thanks them by name and the session ends. The screen shows the library, which
   they can come back to.

**If they leave early:** the next time they open their link, they land on the library with one card: "Got five
minutes to tell Ian how it went?" Tapping it starts the debrief. The card stays until the debrief is done or
Ian turns it off.

## Screens

Until the handoff to the guide, the page is a dot and three words. Nothing else is rendered at all, not merely
hidden, so there's nothing to scroll to, select or find in the page source.

**Gate**
- Full-screen ground color (#F5F2ED). One dot centered, "Tap to begin" beneath it, and the one-line recording
  notice small at the bottom.
- The whole screen is the tap target. No logo, title, menu or scroll.
- The tap also unlocks audio and asks for the microphone, which phones require.

**Interviewer**
- The same dot, now in a neutral color (#6B635C) with a slow, even pulse while it speaks. "Tap to begin" fades
  out. Nothing else appears.
- If the mic is refused: one line, "I need your microphone to talk with you", and a button to try again.

**The reveal**
- At the handoff the neutral dot warms into the guide's breathing circle (coral, the existing connecting
  animation) over about two seconds.
- The product screen fades in around it only once the guide starts speaking.
- From here on, screens are the existing onboarding, chapter card, chapter and library screens.

**During the debrief** the guide's circle cools back to the neutral interviewer dot, and the product screen fades
to the same plain ground. The library appears after the thank-you.

## The interviewer

A separate GPT-Live session with its own instructions file (`prompts/interviewer.md`) and a different preset
voice from the guide's: plain, calm, unhurried. The guide's instructions are not changed.

**Intro (about a minute)**
- Thanks them for making the time.
- Asks their name and confirms it; saves it with `set_listener_name(name)` so the guide has it.
- Says it would like a few minutes with them afterwards to hear how it went.
- Hands over: "I'll hand you over now." Then `handoff_to_guide()`.
- Never says what the product is, what it does, who made it, or what to expect. If asked, it says they'll see
  in a moment.

**Debrief (5–8 minutes)**

The same five core questions for every tester, in this order, so answers compare. Follow-ups vary.
1. "Take me through what that was like, from the start."
2. "Was there a moment you were most into it? And a moment you drifted, or something felt off?"
3. If they asked questions during the chapter: "At [time] you asked [their words]. How did that go?" If they
   didn't: "Did anything make you want to stop it and ask something?"
4. "How would you describe it to a friend?"
5. "If another one were waiting for you tomorrow morning, would you listen? What would make the difference?"

Close with "Is there anything I didn't ask that I should have?", then thank them by name.

**How it asks**
- Open questions. Never suggests an answer, never names a feature they didn't mention.
- Asks for specific moments and stories, not ratings.
- One follow-up at most per answer: "What made it feel that way?" or "Say more about that."
- Leaves pauses. Doesn't fill silence with options.
- Never defends, explains or apologises for the product. Thanks them for criticism the same way as praise.
- Keeps to time: if past 8 minutes, skips to question 5 and the close.

**What it receives:** the tester's name, the chapter title and summary, how much they heard, and their moments
(time and words). Not the chapter script or the guide's instructions.

**What it saves,** in `listeners/<tester>/study/`: the intro and debrief transcripts and audio, and
`debrief_summary.md` (a Claude summary: answers to each core question in the tester's words, with notable quotes
and timestamps).

## Tester links and consent

**Links**
- Each tester gets `https://listen.ianduffy.com/t/<token>`: 32 random URL-safe characters, made from the admin
  page with a label ("Purnima", "Pilot 1").
- Opening it sets a long-lived cookie tying that browser to the tester's listener folder, then redirects to `/`
  so the token isn't left in the address bar. Opening the link again on another device works the same way.
- Every tester's folder is `listeners/t-<label>/`, created on first open, with `chapter.minutes: 10` and
  `overnight.enabled: false`.
- Revoking deletes the token. The folder stays until Ian deletes it.
- Without a valid token or cookie, the site shows only the ground color and the dot, with no text.

**Consent**
- One line on the gate screen, under "Tap to begin": *"Your conversations are recorded so Ian can learn from
  them. Ask him to delete yours any time."*
- Tapping to begin records the consent with a timestamp in the tester's folder.
- Ian deletes a tester's data from the admin page on request.

**Keys stay on the server.** The browser never holds the OpenAI or Anthropic keys. The server mints a short-lived
GPT-Live session token for each conversation. Check how the local app does this today; if any key reaches the
browser, fix it before deploying.

## Hosting

One DigitalOcean droplet runs everything: the web app, the live-session token service and any background builds.
- **Size:** Basic Droplet, 4 GB RAM, 2 vCPU, Ubuntu LTS. The aligner and transcript checks need the memory.
  Resize later if needed.
- **Domain:** a subdomain of ianduffy.com pointed at the droplet (for example `listen.ianduffy.com`).
- **HTTPS:** Caddy in front of the app, with automatic certificates. Browsers only allow the microphone over HTTPS.
- **Processes:** the app and the job runner as systemd services that restart on failure. Logs to the journal plus
  one app log per day.
- **Secrets:** API keys in an environment file readable only by the app's user, never in the repository.
- **Data:** `listeners/` on the droplet's disk. Turn on DigitalOcean weekly backups, and copy `listeners/` off the
  server nightly (a simple `rsync` to Ian's machine is enough for the study).
- **Deploy:** `wl deploy` pulls the branch, installs, migrates and restarts. Written up in `DEPLOY.md` so Ian can
  run it.
- **Concurrency:** several testers may be live at once. Each conversation is its own GPT-Live session; chapter
  builds queue with at most 3 running at a time, so API rate limits aren't hit. Log any rate-limit errors.

## Admin page and study report

**Admin page** at `/admin`, behind a username and password set in Caddy. Phone-friendly, one table.

| Column | Shows |
| --- | --- |
| Tester | Label, with a copy-link button |
| Stage | Not opened, intro, onboarding, chapter, debrief, done |
| Last active | Date and time |
| Chapter | Title and minutes heard of 10 |
| Questions asked | Count of moments |
| Debrief | Done or not, with a link to the summary and transcript |
| Cost | Claude plus GPT-Live, to date |

Actions per tester: create link, revoke link, reset (fresh start, same link), show or hide the debrief card,
delete all data. A total cost line sits at the top.

**Study report:** `wl study report` reads every tester's debrief summaries, transcripts and usage, and writes
`study/report.md`:
- For each core question: the common answers, how many testers gave each, and two or three quotes in their words.
- Where people were most into it and where they drifted, matched to chapter positions where possible.
- Drop-off: where testers stopped (intro, onboarding, chapter, debrief).
- Bugs and confusions mentioned, listed separately.
- Three or four testers worth a follow-up call from Ian, with the reason for each.

It quotes and counts; it doesn't score or rank testers.

## Build order, checks and stop rules

Build with Opus 5.5 on branch `feature/tester-demo`, in this order.
1. **Tester links, cookies, folders and consent.**
2. **Gate and interviewer screens,** with nothing else rendered.
3. **Interviewer:** intro, `set_listener_name`, `handoff_to_guide`, the reveal transition.
4. **Demo settings:** 10-minute chapter, overnight off, chapter card shown during onboarding, "I'm done".
5. **Handoff back and debrief,** with the transcript, audio and summary saved; the debrief card for testers who
   left early.
6. **Admin page.**
7. **Hosting and deploy** on the droplet.
8. **Study report.**

**Acceptance checks**
1. A new link creates a tester folder with the demo settings; the token is gone from the address bar after
   redirect.
2. Without a valid link or cookie, the page shows only the dot. A revoked link does the same.
3. Before the handoff, the page source contains no product text (no chapter, library or guide wording).
4. The interviewer's intro says nothing about what the product is, across 10 simulated runs, including runs where
   the tester asks.
5. The guide uses the tester's name from the interviewer and never asks for it.
6. The chapter card appears during onboarding; the chapter is 9–11 minutes.
7. Chapter end and "I'm done" both hand back to the interviewer.
8. The debrief asks all five core questions in order, uses the tester's real moments in question 3, and thanks
   them by name. Check 10 simulated runs for leading questions.
9. A tester who leaves mid-chapter sees the debrief card on return; it disappears once the debrief is done.
10. Admin page shows correct stage, minutes, moments, debrief and cost for a full run; revoke, reset and delete
    all work.
11. Deployed on HTTPS; mic and audio work on iPhone Safari, Android Chrome and a laptop.
12. Two testers live at the same time don't see each other's data or interfere.
13. `wl study report` produces a report from three seeded test runs.

Checks 5 (by voice), 11 and 12 need a person; list them in `NEEDS_HUMAN.md`.

**Stop rules:** commit after each passing check and keep `PROGRESS.md` current. Stop and write down the blocker if
an API or the existing code behaves differently from this spec. Never put an API key in a file the browser can
load.

## Out of scope

- Overnight chapters for testers (the setting exists; it stays off).
- Voice matching, the voice bank, or any claim that the voice was made for the tester.
- Accounts, logins, passkeys or voice recognition.
- Email or text invitations: Ian sends links himself.
- Curriculum mode and the topic lanes view.
- Payments.
