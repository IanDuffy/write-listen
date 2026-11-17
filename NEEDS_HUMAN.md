# Tester Demo: checks that need a person

The spec (`docs/tester-demo.md`) says checks 5 (by voice), 11 and 12 need a person. Set up the droplet first
(`DEPLOY.md`), or run locally for 5:

```bash
WL_DEMO=1 .venv/bin/python server.py
.venv/bin/python cli.py study create "Ian test"   # prints the link; open it in Chrome
```

(Locally the admin page is http://127.0.0.1:8765/admin.)

## After Ian's first tester run (2026-11-16): what to listen for on the next one
Make the link with a name (`cli.py study create "Ian test" --name Ian`, or the admin form, which shows what the
interviewer will say). Then:
- **The intro's length and warmth.** It should open "Hi, is this Ian?", say Ian built it and how the session
  will go, ask for questions, and take 60 to 90 seconds: welcoming, not long. It must not hand over before its
  final "I'll hand you over now."
- **The handover.** The guide's first words should come about 2 to 3 seconds after the interviewer's last, by
  name, with no glitch in the sound and no jump on screen.
- **Tap to talk after a rest.** Let the guide step back for a couple of minutes, tap the circle: it should say
  it's here and stay. With the chapter ready, the play button stays on the bottom bar while you talk.
- **The pitch.** It should explore before pitching, not pitch the first thing you mention; say no once and it
  should ask rather than guess again.
- **The debrief replying.** Answer in fragments and ask it something back: it should respond in words within a
  couple of seconds every time, never "Mm-hmm" and silence.
- **On a phone:** the bottom bar clear of the home indicator, with "I'm done" above it.

## 5. The guide uses the name from the interviewer and never asks for it
Open a tester link, tap, give your name to the interviewer, and listen to the guide's first words after the
handover: it should greet you by name and give its own, and never ask your name.

## 6 (length). A real 10-minute chapter lands at 9-11 minutes
Pick a topic with the guide and listen to the end (or check the admin page's minutes afterwards). About $3-4 of
Claude and $0.50 of GPT-Live.

## 11. On the droplet over HTTPS: mic and audio on iPhone Safari, Android Chrome and a laptop
For each: open a tester link, tap, hear the interviewer, talk, hear the guide after the handover, ask a question
mid-chapter, finish (or tap I'm done), do the debrief. Note anything that stays silent or asks for the mic twice.

## 12. Two testers at once
Two people (or two phones on two different tester links) at the same time: each hears only their own guide and
chapter, the admin page shows both, and neither's library shows the other's chapter.

# Overnight Chapters: checks that need a person

The spec (`docs/overnight-chapters.md`) says checks 10, 11 (by voice), 12 and 13 need a person. They
are listed here rather than marked passed. What each one already has in code is in `PROGRESS.md`.

Start the server from this checkout and open the library page:

```bash
cd /Users/ian/Desktop/write-listen/.claude/worktrees/spec-overnight-chapters-43fe6b
.venv/bin/python server.py
```

Then open http://127.0.0.1:8765/library (Chrome; allow the microphone).

## 10. A real scheduled 2:00 build on the laptop finishes by morning

1. Keep the laptop on power, lid open (or with an external display), and set it to wake before the build:
   System Settings → Battery → Options (or `sudo pmset repeat wakeorpoweron MTWRF 01:55:00`). This is a
   system setting, so it is yours to change; nothing here changes it.
2. Start the scheduler. **On this Mac a launchd job can't run it**: macOS refuses background jobs access to
   `~/Desktop`, where the repo lives (tried 2026-11-15: "Operation not permitted"). Instead, in a Terminal tab,
   from the main checkout, run this and leave it running. It ticks now and every quarter hour, plans at 18:00
   and builds at 02:00 on build days (following `settings.json`), and keeps the Mac from idle-sleeping:
   ```bash
   ./wl overnight wait
   ```
   (The launchd entry, `./wl overnight schedule install`, works once the repo is outside `~/Desktop`, or once the
   venv's python and `/bin/sh` have Full Disk Access in System Settings → Privacy & Security; your call.)
3. Before 18:00 on a weeknight, check the card plans: `./wl overnight status`.
4. Next morning: the library page's latest chapter is that night's, and
   `listeners/ian/chapters/<date>-<slug>/status.json` says `ready`. The build log is
   `shared/logs/overnight.log`. While it runs, the build holds off idle sleep itself (`caffeinate -i`).

Pass: the chapter is ready by morning, without anyone touching the laptop after 18:00.

## 11. Change by voice, before and after 2:00

1. Before 2:00: tap **Change** on the Up next card. The guide speaks first and names tonight's plan by
   title. Ask for something else, let it say what it will make, and stop talking.
   Pass: the card shows the new plan within about a minute (a planning pass measured 40-45 s live).
2. After 2:00, while the build runs (or with the build start set a few minutes ahead in Settings): tap
   **Change**, ask for a new topic.
   Pass: the guide says tonight's chapter can't change and that it will be the next one;
   `listeners/ian/queue.jsonl` has the request; the next planning pass uses it.

## 12. Play: the intro, then the chapter (10 runs)

Press **Play** on the latest chapter ten times, letting each intro run (talk back on some, stay quiet on
others).
Pass, each time:
- the guide speaks first, names the chapter and mentions one or two things from the intro brief;
- it says nothing from the chapter beyond `intro_brief.md`;
- the recording then starts from the beginning, either when the guide calls `start_chapter`, or after its
  lead-in line and about 1.5 s of silence, or at 90 s at the latest.

How each run ended is recorded in that session's line in `listeners/ian/sessions/<id>.jsonl` (`ended`:
`tool`, `lead-in`, `timeout` or `button`). `python -m overnight.tally` counts them.

## 13. A tap during a recorded chapter

While the recording plays, tap the button (it reads "Ask a question").
Pass:
- the recording pauses and the guide answers;
- after "OK, got it" (or silence) the guide leads back in its own words;
- the recording resumes from the interrupted sentence (under 30 s), one sentence earlier (up to 2
  minutes), or the paragraph's start (longer).

The aside is logged in the chapter's `12_asks/asides.jsonl` with its restart point.

## Costs to expect

GPT-Live bills $0.05 per connected minute: about 5 cents per intro and a few cents per Change or question.
A nightly build was about $8-10 in the live check (`overnight/REPORT.md`).
