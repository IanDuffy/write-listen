# Seed file template (not sent to a model)

Inputs:
- {{topic}}: what the chapter is about (the listener's words when typed; the guide's framing from start_chapter)
- {{said}}: what the listener actually said, word for word (from the guide's start_chapter)
- {{why}}: what they said about why it matters to them
- {{notes}}: anything to include or avoid

Written to 00_source/seed.md by the seed stage. Later stages read it whole; the
brief uses the listener's words for the chapter's opening callback. The guide's
conversation (later milestones) writes the same shape.
---
# Seed

## Topic

{{topic}}

## What the listener said, in their own words

{{said}}

## Why it matters to them

{{why}}

## Include or avoid

{{notes}}
