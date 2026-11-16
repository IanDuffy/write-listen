# Spec: Library Timeline and Listening Notes

Ian, 2026-11-15. Copied from the Claude Docs spec of the same name
(https://claude.ai/artifact/HhsBtxPmXMqEaTGfzDQjou); the reference screens are the
"Library Timeline Sketches" canvas (https://claude.ai/artifact/VWQMct59Ea9hJRDhWu6cGX),
artboards *B · Strip* and *D · Chapter + listening notes*. What was built, and where the
code differs from what this spec assumed: `PROGRESS.md`.

## Overview

This builds the library as two screens: a chapter strip with a time scrubber (sketch B), and a chapter
page that shows the listener's moments (sketch D). Behind the chapter page is the listening notes loop:
every question or mention during a chapter is saved at its place in the audio, and the app shows what
became of it.

**Reference screens:** Library Timeline Sketches, artboards *B · Strip* and *D · Chapter + listening
notes*. Build to these. Sketches A and C are not part of this build.

**Decisions**

| Decision | Setting |
| --- | --- |
| Library screen | Sketch B. Replaces the two-card library page from the overnight spec; Up next moves into the strip |
| Chapter screen | Sketch D |
| Time runs | Left to right: oldest on the left, Tonight at the far right |
| Opens on | The latest chapter |
| Devices | Designed at phone width (390 px), one centered column up to 480 px on a laptop. Works with touch, mouse and keyboard |
| Playable | Latest chapter only, as in the overnight spec. Older chapters show their page, transcript and moments |
| Demo | A separate demo listener with seeded content. Nothing demo goes into `listeners/ian/` |

**Reused:** library capture (already started), the overnight build's chapter folders and `up_next.json`,
the ask-by-voice sessions, `save_seed`, the episode page for transcript and references, and the morning
playback flow.

## Data

Three plain files per listener feed both screens. All are append-only or rewritten whole; no database yet.

**`library.jsonl`** (exists; capture already started). Each line is one item: `id`, `kind` (chapter,
conversation, question, mention), `title`, `creator`, `date`, `topics`, `source` (chapter id or session
id plus position), `link` (for checked works), `relation` in the listener's words ("loved it", "want to
read").

**`chapters/<id>/moments.jsonl`** (new). One line per moment in that chapter:

| Field | Example |
| --- | --- |
| `id` | `m_0142` |
| `kind` | question, mention, note |
| `position_s` | 402 (seconds into the chapter) |
| `paragraph_id` | `p_031` |
| `text` | the listener's words, short: "Why do they stand on one leg?" |
| `reply` | one line summarising the guide's answer, if it gave one |
| `outcome` | answered, saved, became_chapter, in_library |
| `outcome_ref` | the chapter id or library item id it points to |
| `session` | the ask-by-voice session it came from |

**`chapters/<id>/playback.json`** (new). `furthest_s`, `finished` (true past 95%), `last_played`,
`plays`. Written by the player every 10 seconds and on pause.

The screens read only these files plus `up_next.json` and each chapter's `episode.json`.

## Library screen (sketch B)

Three parts, top to bottom: the chapter strip, the scrubber, and "From this chapter". The strip and
scrubber always show the same chapter; moving one moves the other.

**Chapter strip**
- One card per chapter, oldest on the left. The last card is Up next (dashed, "Tonight"), so the future
  sits to the right of the present.
- Opens on the latest chapter, with the next card peeking at the right edge.
- Card: date and length, title, a status line (Finished, "12 of 21 min", or Not started) and counts
  ("2 questions · 1 mention"). Background color from the chapter's main topic (see Visual language). No
  generated cover art in this build.
- Tap a card to open its chapter page. The latest card also shows Play, which starts the morning flow.
- The Up next card shows the title, overview and summary, and the Change button from the overnight spec.
- Moving between cards: swipe on touch, arrow buttons and left/right keys on a laptop. Cards snap.

**Scrubber**
- A thin line spanning the listener's whole history, from the first conversation to Tonight.
- Tall tick for each chapter, short tick for each question or mention, a dashed ring at the end for Up next.
- A round handle marks the chapter in view. Drag it, or tap a tick, and the strip jumps to that chapter.
- Positions follow real dates, but any gap longer than 7 days is squeezed to a fixed width with a small
  break mark, so a quiet month doesn't swallow the bar.
- Labels under it: the first date, month changes, "This week", "Tonight". At most five labels; drop the
  oldest when crowded.

**From this chapter**
- Up to three moments from the chapter in view: time, kind and text, plus the outcome when it points
  somewhere ("→ Tonight").
- "See all" opens the chapter page.
- With no moments: one line, "Nothing saved from this one yet."

## Chapter screen (sketch D)

One page per chapter: what it was, where you were in it, and what you did while listening.

- **Header:** back to Library, then date, length and the guide's name, the title, and the card's summary.
- **Play and progress:** the Play button (latest chapter only; hidden on older ones) and a progress bar
  filled to `furthest_s`, labelled Finished or with minutes heard.
- **Moment markers:** one marker on the bar per moment, at `position_s`. Ring for a question, diamond for
  a mention, accent-colored ring for a moment that became a chapter. Tapping a marker scrolls to that
  moment in the list. On the playable chapter it also offers to play from 10 seconds before it.
- **Your moments:** every moment in time order. Each shows time and kind, the listener's words, the
  guide's one-line reply if there was one, and the outcome on the right: Answered, Saved for later, In
  your library, or Became tonight's chapter (or a past chapter's title, as a link).
- **Transcript and references:** a link to the existing episode page.
- **No moments:** the list section shows "Ask anything while you listen and it'll show up here."

## Listening notes loop

Moments come from what the listener already does: tapping to ask during a chapter. There is no new
note-taking UI. The loop is capture, then outcome, then use.

**Capture.** When an ask-by-voice session inside a chapter ends, the after-conversation pass (the one
that already updates the profile and library) also writes moments:
- A **question**: the listener's question in their words, shortened, at the chapter position where they tapped.
- A **mention**: a book, person, place or work the listener names. It also goes into `library.jsonl`,
  checked against the web as now.
- A **note**: the listener asks the guide to remember or save something ("save that for later"). This
  is what `save_seed` already does; the seed now also writes a moment.
- One session can produce several moments; all share the tap position.

**Outcome.** Set when the moment is written, updated later:
- Answered: the guide answered in the session. Store the one-line reply.
- Saved: the guide called `save_seed`, or the question couldn't be answered then.
- In your library: a mention that became a library item.
- Became a chapter: when the overnight planner picks a seed for `up_next.json`, it sets the source
  moment's outcome to `became_chapter` with the chapter id. If the listener changes Up next away from
  it, the outcome goes back to Saved.

**Use.** Saved moments are already planner input (seeds). The morning intro already names the source of
a chapter; when that source is a moment, the intro can say so in the listener's words ("you asked
whether gardens make us notice like this").

**Playback progress.** The player writes `playback.json` as above. Finished and unfinished chapters feed
the library display only in this build; the planner doesn't use them yet.

## Demo content

A demo listener with about three weeks of history, so the strip has something to swipe and the scrubber
has something to scrub. It follows one curious thread, like the sketches.

**Where it lives:** `listeners/demo-library/`, made by `wl demo seed`. Running it again rebuilds the
folder from scratch. It never touches `listeners/ian/`.

**The seed file:** `demo/library_demo.json`, committed, so the demo is the same every time. The command
writes the normal files from it (chapter folders, `moments.jsonl`, `playback.json`, `library.jsonl`,
`up_next.json`) so the screens need no demo code.

**What's in it**
- **Anchors from the sketches, kept exactly:** the first conversation ("the heron by the pond"); *What
  Beauty Asks of Us* (38 min, finished); *Herons: Hunting by Standing Still* (21 min, finished); the
  moments shown in sketch D; and Up next, *Borrowed Scenery: Why Japanese Gardens Hide the View*.
- **Real chapters:** the first chapter and the herons chapter already exist. Copy them in with their
  real audio, transcripts and episode pages.
- **Filler, generated once:** 6 to 8 more chapters between Nov 3 and Nov 14, written by the overnight
  planner as Up next cards (title, overview, summary), each with 1 to 3 moments and a mix of finished and
  partly heard. Have Claude review them for a believable thread before committing. They have no audio;
  their cards show no Play, and their pages say "Demo chapter" where the transcript link would be.
- **Mentions:** use real works with correct titles and authors (for example, Elaine Scarry's *On Beauty
  and Being Just*, 1999), each linked as the library does now.

**Switching:** the existing reset and `active.json` choose the listener, so showing the demo is one
switch and going back to Ian is another.

## Visual language

Take these from the sketches. Match them exactly unless the existing app already sets a token, in which
case keep the app's and note it in `PROGRESS.md`.

| Token | Value | Use |
| --- | --- | --- |
| Font | Instrument Sans 400/500/600 | Everything |
| Ground | #F5F2ED | Page background |
| Surface | #FFFFFF | Moment rows, cards |
| Ink | #1E1A17 | Text, chapter ticks and dots |
| Muted | #6B635C | Dates, labels, secondary text |
| Line | #DDD6CC | Scrubber line, dividers |
| Accent | #C8553D | Up next, became-a-chapter markers, guide circle |
| Accent text | #9C4330 | Accent-colored words (passes contrast on ground) |
| Topic colors | #24585B teal, #C8553D coral, #B7791F amber | Card backgrounds by main topic; amber diamond for mentions |

- Card corners 22 px, rows 14 px. Title 24 px semibold on cards, 26 px on the chapter page.
- Markers: filled circle for a chapter, ring for a question, rotated square for a mention, dashed accent
  ring for Up next.
- Touch targets at least 44 px. Real buttons and links, labelled for screen readers.
- No gradients, no emoji, no generated imagery in this build.

## Build order, checks and stop rules

Build on branch `feature/library-timeline`, off the merged overnight work, in this order.

1. **Data:** `moments.jsonl`, `playback.json`, and the reader the screens use.
2. **Demo seed:** `demo/library_demo.json` and `wl demo seed`. Building the screens against it means
   there's always something to look at.
3. **Library screen:** strip, scrubber, From this chapter, Up next card with Change.
4. **Chapter screen:** progress bar, markers, moments list.
5. **Capture:** moments written by the after-conversation pass and `save_seed`; outcome updates from the
   planner; `playback.json` from the player.

**Acceptance checks**

1. `wl demo seed` builds `listeners/demo-library/` twice in a row with identical results;
   `listeners/ian/` is unchanged.
2. Library opens on the latest chapter; the Up next card is the rightmost card.
3. Swiping, arrow buttons and arrow keys each move one card; the scrubber handle follows.
4. Dragging the handle and tapping a tick each move the strip to the right chapter.
5. A gap of more than 7 days in the seed shows as a squeezed break, not a long empty stretch.
6. From this chapter shows up to three moments for the chapter in view, and the empty line when there are none.
7. Chapter page markers sit at the right positions (within 2% of the bar) and tapping one scrolls to its moment.
8. Play appears only on the latest chapter, on both screens.
9. A real chapter with two asks produces two moments with correct positions, words and outcomes.
10. When the planner picks a saved moment for Up next, that moment shows "Became tonight's chapter";
    changing Up next away reverts it to Saved.
11. `playback.json` updates while playing and survives a page reload; the card's status line matches it.
12. Both screens look right at 390 px and at laptop width, and work with keyboard alone.
13. Side by side with sketches B and D, the screens match their layout, type and colors.

Checks 9, 12 and 13 need a person; list them in `NEEDS_HUMAN.md` rather than marking them passed.

**Stop rules:** commit after each passing check and keep `PROGRESS.md` current. If existing code doesn't
match what this spec assumes (session files, `save_seed`, the planner), stop and write down the gap
rather than working around it.

## Out of scope

- The topic lanes view (sketch C) and the curriculum coverage map.
- Playing older chapters.
- Generated cover art for chapter cards.
- Search and filters.
- Editing or deleting moments from the screen (the files can be edited by hand for now).
- Planner use of playback progress (finished, skipped).
