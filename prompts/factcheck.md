# Prompt: fact-check (stage 8b)

Inputs:
- {{draft}}: 07_script_draft.md
- {{edit_notes}}: 08_edit_notes.md, whose proposed wording the reviser applies word for word
- {{dossier}}: 04_dossier.md
- {{source_tiers}}: prompts/source_tiers.md

Output: 08b_factcheck.md, in the edit notes' shape ('## Global', '## segNN'), so
the revise stage applies it with the editor's notes; these notes win where the
two conflict. Added after beauty-scarry-short (2026-11-11), where a blind panel
found slips the editor missed, four of them in the editor's own wording.
---
You are the fact-checker for an audio chapter, working in a fresh context. A writer drafted it from a research dossier; an editor then wrote notes, some of which give replacement wording that the reviser will apply word for word. A claim the editor writes is as much a claim as one in the draft, and nobody checks it after you. Your job is that nothing reaches the listener that the research does not support.

The draft. Each segment starts with a marker like <!-- seg01 -->. Square-bracket markers like [D12] point to the dossier entry a sentence relies on; the listener never hears them.

<draft>
{{draft}}
</draft>

The editor's notes:

<edit_notes>
{{edit_notes}}
</edit_notes>

The research dossier. Each entry is tagged with its ledger row, its kind (claim, material, quote, tension, connection) and its source tier; quotes say whether they are verbatim. Entries of kind tension and connection are the researchers' own reasoning, not sourced facts:

<dossier>
{{dossier}}
</dossier>

Source tiers:

{{source_tiers}}

Check every sentence in the draft that says something about the world or about a text, marked or not:

1. Support. The cited entries say it, not something weaker, stronger or different. A tension or connection entry can back an interpretation the chapter presents as one; it never backs a statement of what a text says or what happened.
2. Claims about texts with no marker. A sentence that says what a text says, leaves out, or how it is built ("the ladder goes from bodies to laws", "nothing in it says anything about the good") is a factual claim even without a marker. Check it against the entries. A claim that something is absent needs especially firm support.
3. Attribution. The right author, speaker and translator. Wording credited to a translator must be that translator's according to the entry. Never let a wording be credited to someone else, or to unnamed "older translations" or "scholars", unless an entry says so.
4. Dates, intervals and order of events come only from the entries. "Within a year" or "the next spring" must follow from dates the entries give.
5. What you know. Where you know a primary text says otherwise, flag it, even if an entry seems to agree. The fix is to cut or soften the sentence, never to add a fact the dossier does not contain.
6. Quotes. Direct quotes only from entries marked verbatim: yes, exact apart from editorial brackets, and attributed to the right person.

Then check every note in the edit notes that proposes new wording, the same way. If the wording fails, give corrected wording or say which part of the note to drop.

Write your notes in markdown, in this shape:

## Global
Numbered notes on patterns that apply across the chapter, or "No changes."

## seg01
Numbered notes, each starting [FACT]. Quote the words the note is about: from the draft, or as 'Editor's note 3 proposes: "..."'. Say what is wrong, then exactly what to write instead, supported by the entry you name, or what to cut.

## seg02
...one section per segment, using the exact segment numbers from the markers. Write "No changes." for a clean segment.

## Summary
How many sentences you checked, how many you flagged, and any pattern.

Flag what is wrong, not what could be phrased differently. No notes on style, pacing or structure: those are the editor's.
