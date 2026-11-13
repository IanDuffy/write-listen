"""Ask mid-chapter, by voice (docs/ask-voice.md): the listener taps the circle while a chapter plays,
talks with the guide, and the chapter picks up again.

    aside.py      the chapter as the guide is told it, where playback restarts, the log
    routes.py     the server's /api/ask/* routes (mounted by server.py)
    scenarios.py  the scripted conversations (checks 6-19) and their grading
    live.py       the live timing runs (check 20)
"""
