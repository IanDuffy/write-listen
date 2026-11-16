# Library Timeline and Listening Notes: checks that need a person

The spec (`docs/library-timeline.md`) says checks 9, 12 and 13 need a person. They are listed here rather than
marked passed; what each already has in code and tests is in `PROGRESS.md`.

Start the server from this checkout (stop the one running from the main checkout first: both use port 8765):

```bash
cd /Users/ian/Desktop/write-listen/.claude/worktrees/library-timeline-listening-notes-32f2b2
./wl demo seed --use
.venv/bin/python server.py
```

Then open http://127.0.0.1:8765/library (the library alone) or http://127.0.0.1:8765/ (below the orb). The demo
listener is `listeners/demo-library/`; `./wl listener use ian` goes back to your own (nothing of yours changes).

## 12. Both screens at 390 px and at laptop width, and with the keyboard alone

1. In Chrome, open the developer tools' device bar at 390 x 844, then a normal laptop window.
2. Library: it opens on *Herons*, with Up next peeking at the right. Swipe (or use the arrow buttons at laptop
   width); drag the scrubber's handle; tap a short tick.
3. Keyboard only: Tab to a card's title, Left and Right move one card; Tab to the scrubber, Left, Right, Home,
   End; Enter on a card opens its page; on the *Herons* page, Tab to a marker and press Enter: the moment comes
   into view with "Play from ...".

Pass: nothing overlaps or scrolls sideways, every control is reachable and visibly focused.

## 13. Side by side with sketches B and D

Open the Library Timeline Sketches canvas (artboards *B · Strip* and *D · Chapter + listening notes*) beside
the demo at 390 px. Two differences are deliberate (`PROGRESS.md`, Decisions): the app's system font and its
orange accent stand in for Instrument Sans and coral, as the spec's rule on existing tokens asks, and the real
chapters' lengths (43 and 17 min) stand in for the sketch's 38 and 21.

Pass: layout, type sizes and colours otherwise match.

## 9. A real chapter with two asks makes two moments

With your own listener (`./wl listener use ian`) and the latest chapter: press Play (or Skip the intro), tap
Ask twice at two places. Once ask something the guide answers; once ask something it can't settle or say "save
that for later". About half a minute after each ask ends (the after-conversation pass, about $0.02 an ask), open
the chapter's page (tap its card).

Pass: two moments, at the places you tapped (the markers on the bar), your words shortened, the first Answered
with a one-line reply, the second Saved for later. That saved one is in `listeners/ian/seeds.jsonl`.

# Overnight Chapters: checks that need a person (the build before this one)

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
