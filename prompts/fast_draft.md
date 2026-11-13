# Prompt: draft one section (Fast Start, stage fast_draft)

Inputs:
- {{n}}, {{total}}, {{title}}: this section's number, the section count, its marker title
- {{plan}}: this section's part of the outline
- {{rows}}: the ledger rows this section must land, each with how its research came back
  (landed, or thin and why), and any row an earlier section left thin or missing
- {{entries}}: the dossier entries for those rows, and the scout entries the outline gives this section
- {{other_entries}}: the scout's other entries
- {{notes}}: listener_notes.md as it stands, or "(none yet)"
- {{note_entries}}: the dossier entries that answer a listener's note, each under its note
  (faststart/research.py note_answers), or "(none)"
- {{target_words}}: this section's length
- {{position}}: fast_draft_middle.md or fast_draft_last.md

System prompt: fast_context.md. Sent after fast_earlier.md and one block per earlier section
holding its full text: the locked text where a section is locked, its latest draft otherwise
(acceptance check 2). The fast counterpart of draft.md.
---
Write section {{n}} of {{total}}: {{title}}.

Its plan from the outline:

<plan>
{{plan}}
</plan>

The ideas this section must land, from the research ledger, and how the research on each came back:

<rows>
{{rows}}
</rows>

The dossier entries this section is built on:

<entries>
{{entries}}
</entries>

The scout's other entries, gathered before the outline was set. Earlier sections have used some of them. They are here so that you can recall a fact the listener has already heard, or fill a gap where this section's research came back thin. They are not this section's subject:

<other_entries>
{{other_entries}}
</other_entries>

What the listener has said since the topic was set (rule 10). Notes made during a section tell you how that section landed; let them shape this one where the rule allows:

<listener_notes>
{{notes}}
</listener_notes>

Dossier entries that answer something the listener said, each under the note it answers. They may come from another section's research. If no section above has already served that note (rule 10) and this section's job has room for it, serve it here from these entries, cited like any other:

<note_entries>
{{note_entries}}
</note_entries>

{{position}}

Tell it, do not report it. Each idea arrives with its concrete material: the scene, the person, the thing someone did or wrote, taken from the entries. Where a tension entry above holds an objection to what the section lands, face the strongest one: raise it once, at the point where the listener would, give what the entries answer to it or what an entry says has not been shown, and carry on. Where a row came back thin, work with what there is: tell what the entries do give, say plainly what is not known if the section needs it, and never fill the gap from your own knowledge. Where the entries show something other than the plan expected, the section lands what they show. Where a row from an earlier section is listed as still thin or missing, take it up only if this section's job has room for it.

Length: about {{target_words}} words; stay within 10% of that when the entries can carry it. Where the research came back thin, write the section short, down to about two thirds of its target, rather than pad it or retell what an earlier section told.

Before you finish, check: every factual sentence has its [Dn] marker, and that entry says what the sentence says; every direct quote is the exact words of an entry marked verbatim: yes; nothing repeats what an earlier section already told the listener, the chapter's question and a scene already told included (rule 8); the section does its own job from the outline and leaves the next section its job; it ends as the note on its position says.

Output only the section's text. No preamble, no title, no notes after it.
