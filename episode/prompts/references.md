# Prompt: references for one segment (episode page add-on, stage 2)

Inputs:
- {{sentences}}: the segment's sentences, one per line as `<id> | <text>`, each followed by its dossier markers if any
- {{entries}}: the dossier entries offered for this segment (the brief's assignment plus the markers), in full

Output: JSON (structured output; SCHEMA in episode/references.py). The model returns dossier ids,
never URLs: code takes every link from the dossier. Model: Claude Sonnet 5.5 (matching, not writing).
---
You are preparing reference cards for a narrated audio chapter. While the listener hears each sentence, a card can show the source it draws on, a work or person it names, or a quote it reads. Your job is matching, not writing: report what each sentence draws on and mentions, nothing more.

The segment's sentences, one per line as `id | text`. Square-bracket markers like [D12] at the end of a sentence are the writer's own citations of the dossier entries it relies on:

<sentences>
{{sentences}}
</sentences>

The dossier entries for this segment. Each starts with its id, its tags (ledger row, kind, source tier) and ends with its source:

<entries>
{{entries}}
</entries>

For every sentence that draws on an entry or names a work, person or quote, return:

- **sources**: the entries the sentence clearly draws on. Start from its markers; drop a marker the sentence does not really use, and add an entry only if the sentence plainly relies on it. For each: the entry id, a short title for the source as the entry names it (a book, dialogue, article or web page, e.g. "Symposium" or "Stanford Encyclopedia of Philosophy: Plato on Friendship"), its creator if the entry says (author, translator or publisher; else empty), and a note of at most ten words on what this sentence takes from it.
- **works**: books, dialogues, poems, essays or paintings the sentence names. Title, creator if known from the sentence or entries, the id of an entry that is about that work if there is one (else empty), and a note of at most ten words.
- **people**: people the sentence names, other than the chapter's own narrator. Name, the id of an entry that is about them if there is one (else empty), and a note of at most eight words on who they are, from the entries or the sentence.
- **quotes**: direct quotations the sentence reads aloud, copied exactly as they appear in the sentence (the words inside the quotation marks, at most twenty words; cut longer ones at a natural break), the speaker or author, and the id of the entry the quote comes from (else empty).

Rules:
- Use only entry ids from the entries above. Never write a URL.
- Leave out sentences that draw on nothing and name nothing.
- Do not repeat a person or work in the same sentence under two headings: a named dialogue is a work; its author, a person, only if the sentence names them.
