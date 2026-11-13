# Prompt: what the page tells the guide during a check-in (docs/fast-start.md, "When a section isn't ready")

Square-bracket notes the page sends into the check-in's voice session, one per line as
`key: text`, read by faststart/checkin.py `page_notes` and passed to the page by
/api/ask/start. `opened` (or `reopened`, when the listener tapped "Talk while you wait") is
sent as a user message as soon as the session is ready, so the guide speaks first; with
`gap_mic: tap` the page sends `tap` with it. `silence` comes from the page's timer
(config.yaml fast.silence_seconds). `ready` is sent when the awaited part becomes playable:
at once as a contextual update, which the guide reads without speaking, and as a user message
once the listener has gone quiet. prompts/guide_checkin.md says how the guide acts on each.
---
opened: [The listener has reached a part of the chapter that is not ready yet. Speak first.]
reopened: [The listener tapped to talk with you while they wait for the next part. Speak first, in a few words.]
silence: [Silence: the listener has said nothing. Say a word or two and stop.]
ready: [The next part is ready. When the listener has finished, call resume_chapter.]
tap: [The microphone is closed until the listener taps the circle. Ask your question and stop.]
