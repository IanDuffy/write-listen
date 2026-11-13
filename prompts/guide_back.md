# Page note for when the listener taps to bring the voice guide back (a fragment)

The guide stepped back (its step_back tool, or the page after a silence), which ends the
voice connection. A tap on the circle starts a new one, and the server sends this as its
first turn (server.py, /api/voice/resume), so the guide speaks first, as with
prompts/guide_opened.md. When a sound at the microphone brings it back there is no note:
the sound may not have been the listener, so the guide only listens (the page plays two
soft notes). The chapter-ready case uses prompts/guide_ready.md.
---
[The listener tapped to bring you back. Let them know you are here, in a word or two, and wait.]
