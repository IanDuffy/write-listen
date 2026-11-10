# Prompt: research synthesis (stage 4, step 4, Opus)

Inputs:
- {{angle}}: 03_angle.md
- {{claims}}: every topic's claims, already renumbered [C1], [C2], ... by code,
  with the agents' conflicts and gaps

Output: the overview at the top of 04_dossier.md. The claims themselves are
copied into the dossier by code, unchanged, so this prompt only writes the guide
to them.
---
You are writing the guide at the top of a research dossier for an audio chapter. The claims below were gathered by several research agents and numbered by code. They will be copied into the dossier exactly as they are. The brief-writer and the drafter will read your guide first to find their way through them.

The chapter's angle:

<angle>
{{angle}}
</angle>

The claims, with the agents' notes on conflicts and gaps:

<claims>
{{claims}}
</claims>

Write these sections in markdown, citing claims by number like [C12] or [C3, C7], never as a range:

## Overview
What the research established, thread by thread, in plain prose: the story and argument the evidence supports, in two to four paragraphs.

## Strongest material
The scenes, images and primary-text quotes most likely to carry a listener, each with its claim number and one line on what it could do in the chapter.

## Duplicates and conflicts
Claims that say the same thing (name the stronger source of each pair), and claims that contradict each other, with which is better supported and why.

## Weak spots
Important points that rest on weak sources, only on the episode, or on nothing, and what the writer should therefore avoid saying.

Use only the claims given. Do not add facts, sources or quotes of your own.
