# Prompt: what the page tells the guide during an aside (ask mid-chapter; docs/ask-voice.md)

Square-bracket notes the page sends as user messages, one per line as `key: text`, read by
ask/aside.py `notes` and passed to the page. `opened` is sent as soon as the voice session is
ready, so the guide speaks first; the silence notes come from the page's own timers
(config.yaml ask.silence_*); `long` after ask.long_aside_minutes. prompts/guide_aside.md says
how the guide acts on each.
---
opened: [The listener tapped to ask something. The chapter is paused.]
silence_greeting: [Silence: the listener has said nothing since your opening.]
silence_answer: [Silence: a few seconds have passed since you finished, and the listener has said nothing.]
long: [This aside has run for a while now.]
