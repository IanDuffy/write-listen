# What the page and the code tell the interviewer (demo/interviewer.py notes())

One `key: text` per line below the `---`. {name}, {when}, {words} are filled by demo/interviewer.py before the
page gets them. `opened_*` go as speakable context the moment a session starts (GPT-Live has no opening field;
live.js greets the same way), and once more as an instruction if it has said nothing after
demo.interviewer.page.greet_fallback_ms. `handover_now` near demo.interviewer.intro_max_minutes; `time_up` at
debrief_target_minutes, `wrap_now` at debrief_max_minutes. The rest build the debrief's {{question_three}},
{{moments}}, {{heard}} and the intro's {{meeting}}. Never put product wording in the intro's lines: the intro
must give nothing away.
---
opened_intro: Begin now: thank them, in a sentence, for making the time, then ask their name. Nothing else yet.
opened_debrief: Begin now: welcome {name} back in a few words and thank them for staying on, then ask your first question.
handover_now: [It's time to hand them over: say "I'll hand you over now." and hand them over.]
time_up: [Eight minutes have passed. Go straight to question five if you haven't asked it, then your closing question, then thank them by name.]
wrap_now: [Time is up. Thank {name} by name now and end.]
meeting_first: You haven't met them before.
meeting_again: You met them briefly before and they told you their name is {name}: confirm it with them rather than asking again.
q3_moment: About {when} in, you asked "{words}". How did that go?
q3_none: Did anything make you want to stop it and ask something?
moment_line: about {when} in, they asked: "{words}"
moments_none: none: they didn't stop it to ask anything.
when_seconds: a few seconds
when_minute: a minute
when_minutes: {n} minutes
heard_all: They heard all of it (about {minutes} minutes).
heard_some: They heard about {heard} of its {minutes} minutes.
heard_little: They heard less than a minute of it.
name_unknown: them
title_unknown: (no title)
summary_unknown: (no summary)
sim_situation_intro: A friend asked you to try something new and you've just tapped to begin; you don't know what it is. An interviewer's voice greets you first.
sim_situation_debrief: You've just finished trying it: an audio piece made for you, which you could interrupt with questions. Now the interviewer who welcomed you earlier asks how it went.
