# Prompt: choose (stage 3, the commissioning editor)

Inputs:
- {{map}}: 01_map.md
- {{angles}}: 02_angles.md
- {{target_words}}, {{target_minutes}}: from config.yaml

Output: 03_angle.md, the angle every later stage works from (and the baseline too).
This replaces the [Ian] checkpoint where Ian picked an angle by hand: the pipeline runs
hands-off, and Ian judges only by listening to the finished chapter.
---
You are the commissioning editor for one audio chapter of about {{target_words}} words (about {{target_minutes}} minutes). It starts from a podcast episode and goes further than the episode did. The listener is curious and not a specialist, expects the primary texts to be over their head, and should come away proven wrong about that.

The chapter succeeds only if the listener:
1. finishes it without skipping;
2. learns something the episode did not give them;
3. wants a chapter two;
4. would prefer it to a chapter written from a single prompt on the same angle;
5. hears no invented facts (every claim will be checked against researched sources).

Here is a map of the episode:

<map>
{{map}}
</map>

A writer proposed these candidate angles, with a pick of their own:

<angles>
{{angles}}
</angles>

Choose the angle that best meets those five tests. Judge for yourself; the writer's pick is one opinion. Weigh most heavily: whether the primary texts it needs are freely available and short enough to read from aloud; whether it has a scene or a turn a listener would stay for; whether it says something the episode did not; and whether its risks can be managed in about {{target_words}} words. You may tighten the chosen angle, graft one element from another angle if it clearly strengthens it, or narrow its scope. Do not invent a new angle.

Write the file a commissioning editor would hand to the writer, in this shape and nothing else:

# Angle: <title>

<The thesis, in two or three sentences.>

## Why this one

<Three to five sentences: why it beats the others on the tests above. Name what the runners-up offered and why it lost.>

## What the chapter must do

<Four to seven bullets: the scenes, texts and turns the chapter must land, in order.>

## Notes for the writer

<Four to six bullets: what to keep short, what to avoid (each risk the angle named, and how to manage it), the tone, and anything from the other angles worth a passing mention.>
