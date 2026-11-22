"""Listener Signals (Ian's build plan, 2026-11-21): plain measurements of how each listener turn sounded,
compared with that listener's own norm, for the admin's timeline and, behind a flag, the earpiece's look.
The code measures and never names a feeling; nothing here calls a model.

    record.py   the record, the norm, the change from the norm, the trend, the note (pure)
    store.py    the signals/ folder of a listener: records, marks, recordings
    measure.py  the replay measurer: a two-channel recording and its transcript in, turn records out
    fetch.py    keeps the GPT-Live recordings (OpenAI deletes them after 30 days)
    live.py     a record from the page's measurements (the live line routes)
    routes.py   the admin's /admin/signals and /api/admin/signals/*
    cli.py      wl signals fetch | run | report
"""
