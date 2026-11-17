# What the page and the code tell the interviewer (demo/interviewer.py notes())

One `key: text` per line below the `---`. {name}, {when}, {words} are filled by demo/interviewer.py before the
page gets them. `opened_*` go as speakable context the moment a session starts (GPT-Live has no opening field;
live.js greets the same way), and once more as an instruction if it has said nothing after
demo.interviewer.page.greet_fallback_ms; `opened_intro_named` when the tester's name is known (given with the
link, or saved before). `waiting` when they finished speaking demo.interviewer.page.reply_wait_ms ago and it has
said nothing but a backchannel since (Ian's debrief: "Mm-hmm", "Hmm.", then 77 seconds of nothing).
`handover_now` near demo.interviewer.intro_max_minutes; `time_up` at debrief_target_minutes, `wrap_now` at
debrief_max_minutes. The rest build the debrief's {{asking}}, {{moments}}, {{heard}} and the intro's {{meeting}}
and {{first_step}}. Never put product wording in the intro's lines: the intro must not say what the thing is.
---
opened_intro: Begin now: say hello warmly and ask their name. Nothing else yet.
opened_intro_named: Begin now: say hello warmly and check who it is: "Hi, is this {name}?" Nothing else yet.
opened_debrief: Begin now: welcome {name} back in a few words and thank them for staying on, then ask your first question.
waiting: [They've finished speaking and are waiting for you. Speak now: answer what they asked or respond to what they said, in a sentence, then carry on.]
handover_now: [It's time to hand them over: say "I'll hand you over now." and hand them over.]
time_up: [Eight minutes have passed. Ask your closing question now, then thank them by name.]
wrap_now: [Time is up. Thank {name} by name now and end.]
meeting_first: You haven't met them before and you don't know their name.
meeting_named: You haven't spoken with them yet. Ian told you their name: {name}.
step_first: Say hello and ask their name. Say it back to confirm it ("Sam. Did I get that right?"). If they correct you, use the corrected name and confirm again. Never ask them to spell it. Once it's confirmed, save it (see the Delegation policy).
step_named: Say hello and check who it is: "Hi, is this {name}?" If they say yes, carry on. If they give a different name or correct you, say it back ("Roland. Did I get that right?"), use it from then on and save it (see the Delegation policy). Never ask them to spell it.
asking_moment: About {when} in, they asked "{words}". Ask how that went, naming the moment: when it was and what they asked.
asking_none: They didn't stop it to ask anything. Ask whether anything made them want to.
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
sim_situation_intro: A friend, Ian, asked you to try something new he built and you've just tapped to begin; you don't know what it is. An interviewer's voice greets you first.
sim_situation_debrief: You've just finished trying it: you talked with a voice, then heard an audio piece made for you, which you could interrupt with questions. Now the interviewer who welcomed you earlier asks how it went.
