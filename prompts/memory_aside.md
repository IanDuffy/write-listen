# Lines for the memory pass's {{aside}} (stages/memory.py, docs/library-timeline.md "Capture")

One `key: text` per line. `aside` is used when the conversation was an ask during a chapter (the listener tapped
while it played); {title} and {at} are filled by code. `none` for every other conversation.
---
aside: This conversation was an aside: the listener tapped to ask something {at} into the chapter "{title}", which paused while they talked. List its moments (see **moments**).
none: This was not an aside during a chapter: return moments as an empty list.
