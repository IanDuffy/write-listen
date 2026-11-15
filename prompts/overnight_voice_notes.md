# What the library page tells the guide (page/library.js), one `key: text` per line

The first note goes as speakable context the moment the session starts (GPT-Live has no opening
parameter; a developer message alone did not reliably make it speak first, live-voice branch). The
bracketed notes go as instructions with the mic open (GPT-Live ignores them while muted). {title} is
filled by the page. The opening notes say what to do first in the situation (the session's {{situation}}),
never just "say hello" (round 2: a returning listener was greeted "good to meet you").
---
intro_opened: Begin the intro to their chapter, "{title}", now, as your instructions say for this moment: a greeting that fits who they are to you, then the chapter.
change_opened: Begin now, as your instructions say for this moment: open with why they're here, the plan by its title, and ask what they'd like instead.
ask_opened: [The listener tapped to ask something: the recording is paused. If they say nothing, open with a word or two, then listen.]
intro_wrap: [Time to move into the chapter: one natural lead-in line now, then start it.]
silence: [A few seconds of silence since you spoke. If you have answered their question, lead back in a few words and go back to the chapter now.]
read: Read this passage aloud now, word for word, exactly as written, with nothing before or after:
read_stop: [The listener tapped: stop reading now, even mid-sentence, and say nothing more.]
