# Prompt fragment: the card's one retry when its summary runs past four sentences

The spec says the summary is three or four sentences; the first live passes (2026-11-15) wrote five
once in three. Appended to overnight_plan_card.md for its one retry (planner.write_card).
Inputs:
- {{n}}: how many sentences the summary had
---
Your first try's summary had {{n}} sentences. Write the card again with a summary of three or four sentences: the question, the angle, and why it follows from what the listener heard or said. Keep the title if it was right.
