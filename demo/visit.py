"""A tester's whole visit as one timeline, for the admin's Transcript view (Ian, 2026-11-21: "could we show that
in the admin view not only the debrief transcript?").

In time order: the interviewer's intro, each conversation with the guide (all of it, rests and resumes included),
the chapter (asked for, begun, its opening ready, read aloud, questions asked while it was read, stopped), the
tester's button presses and the page's audio_route records (the phone, its audio inputs, the mic: page/speaker.js),
then the debrief. Who speaks is a speaker (Interviewer, Guide, the tester's name);
everything else is an event in plain words. The earpiece's looks and whispers are left out: the Earpiece history
(voice/gpt_live/earpiece_history.py, /admin/earpiece) shows them.

- `build(...)` is pure: rows in, the timeline out (tests call it on fixture rows).
- `read(folder, name)` reads a listener's folder and calls it: study/intro-*.jsonl and debrief-*.jsonl (the
  interviewer, demo/interviewer.py), sessions/<id>.jsonl (the guide, stages/guide.py; the library's voice
  sessions, overnight/live.py), study/events.jsonl (demo/debrief.py), and per chapter status.json,
  fast_timeline.jsonl, fast.json, 12_asks/asides.jsonl and live/reading.jsonl.
- `text(visit)` is the same timeline as plain text, for the download.

Each transcript file is parsed by earpiece_history.timeline_of, the parser the Earpiece history uses. Each file is
a block (a part, under its header), the blocks in the order they began; presses and chapter markers go into the
block under way at their time. Rows are written when a line ends, so a line's time is its end; times are local
(the transcripts' naive times; a time with an offset is converted).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from overnight import profiles
from voice.gpt_live import earpiece_history as EH

from . import debrief, state

PARTS = {"intro": "Intro with the interviewer", "conversation": "Conversation with the guide",
         "debrief": "Debrief with the interviewer"}
LIBRARY = {"intro": "Library: the guide's intro to a chapter", "change": "Library: changing tonight's chapter",
           "ask": "Library: a question about a chapter"}
ENDED = {"handover": "Intro ended: handed over to the guide", "done": "Debrief ended",
         "pagehide": "Ended: the page was closed", "timeout": "Ended: it timed out"}
RESUMED = {"tap": "they tapped the circle", "wake": "a sound woke it", "ready": "the chapter was ready",
           "play": "they pressed play", "paused": "they pressed play after a pause",
           "paused-ask": "they tapped to ask while paused", "dropped": "the connection had dropped"}
PRESSES = {"end_demo": "Pressed End demo", "play": "Pressed play", "pause": "Pressed pause",
           "seek": "Moved the slider", "ask": "Tapped to ask a question", "tap_talk": "Tapped the circle to talk"}
PHASES = {"talk": "while talking", "making": "while the chapter was being made", "resting": "while the guide rested",
          "reading": "while the chapter was read", "aside": "during a question", "paused": "while paused",
          "ended": "after the chapter ended"}
ASIDE_ENDED = {"guide": "the guide went back to the chapter", "button": "they pressed play",
               "unasked": "nothing was asked", "ready": "the next part was ready", "dropped": "the connection dropped"}
EARLIER = "Chapter records with no conversation recorded"
PARAGRAPH = re.compile(r"^s(\d+)-p(\d+)$")
# Order of things written in the same second: what was read, then a press, then a question's start, then the
# transcripts' rows, then the chapter's own markers.
PRIO = {"reading": 0, "press": 1, "aside": 2, "row": 3, "chapter": 4}


def _local(s) -> datetime | None:
    """A naive local time from an ISO string (one with an offset is converted to local time)."""
    try:
        t = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _clip(s, n: int = 160) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _mss(secs) -> str:
    try:
        s = int(float(secs))
    except (TypeError, ValueError):
        return "?"
    return f"{s // 60}:{s % 60:02d}"


def _paragraph(pid: str) -> str:
    m = PARAGRAPH.match(str(pid or ""))
    return f"part {int(m.group(1))}, paragraph {int(m.group(2))}" if m else str(pid or "?")


def tool_words(name: str, inp: dict | None, res: dict | None) -> str:
    """A tool call in plain words."""
    inp, res = inp or {}, res or {}
    if name == "start_chapter":
        topic = _clip(inp.get("topic"), 240)
        if res.get("started") is False or res.get("error"):
            return f"Tried to start a chapter on “{topic}”: " + _clip(res.get("error") or res.get("message")
                                                                    or "it didn't start", 160)
        mins = res.get("first_audio_minutes")
        return f"Chapter asked for: “{topic}”" + (f" (opening expected in about {mins} min)" if mins else "")
    if name == "start_reading":
        return "Started reading the chapter"
    if name == "resume_chapter":
        return "Went back to the chapter"
    if name == "get_status":
        bits = [str(res[k]) for k in ("stage", "state") if res.get(k)][:1]
        if res.get("starts_playing_in_minutes"):
            bits.append(f"opening in about {res['starts_playing_in_minutes']} min")
        return "Checked on the chapter" + (f": {', '.join(bits)}" if bits else "")
    if name == "step_back":
        return "The guide chose to step back"
    if name == "add_note":
        return f"Noted for the chapter: “{_clip(inp.get('note'), 200)}”"
    if name == "save_seed":
        return "Saved a topic for later" + (f": “{_clip(inp.get('topic') or inp.get('seed'), 160)}”"
                                            if inp.get("topic") or inp.get("seed") else "")
    if name == "handoff_to_guide":
        return "Handed over to the guide"
    if name == "end_debrief":
        return "Ended the debrief"
    return f"Tool: {name}"


def press_words(ev: dict) -> str:
    out = PRESSES.get(ev.get("event"), f"Pressed {ev.get('event')}")
    if ev.get("phase") in PHASES:
        out += f" {PHASES[ev['phase']]}"
    if ev.get("chapter_t") is not None:
        out += f" ({'to' if ev.get('event') == 'seek' else 'at'} {_mss(ev['chapter_t'])} into the chapter)"
    return out


AUDIO_REASONS = {"mic": "mic opened", "devicechange": "devices changed", "playing": "voice playing"}
AUDIO_RESULT_REASONS = {"speakerphone": "Speakerphone retry", "headset": "Headset connected"}
# The gate opens the mic in the tap, a moment before the intro's transcript writes its first row (consent, the
# offer's exchange with the voice service): a mic or Speakerphone record this soon before the first part opens it.
LEAD_IN_SECS = 30
LEAD_IN_REASONS = ("mic", "speakerphone")


def _version(v) -> str:
    """A version without its trailing zero parts: Chrome's client hints give Android as "16.0.0" ("16"); "13.1.0"
    stays "13.1"."""
    return re.sub(r"(?:\.0+)+$", "", str(v)) or str(v)


def _device(data: dict) -> str:
    """The phone or computer and its browser: from Chrome's client hints, else the user agent string."""
    h = data.get("ua_hints") or {}
    ua = str(data.get("ua") or "")
    bits = []
    model = h.get("model")
    if model:
        bits.append(str(model))
    elif m := re.search(r"\b(iPhone|iPad)\b", ua):
        bits.append(m.group(1))
    if h.get("android"):
        bits.append(f"Android {_version(h['android'])}")
    elif m := re.search(r"Android ([\d.]+)", ua):
        bits.append(f"Android {_version(m.group(1))}")
    elif m := re.search(r"(?:iPhone|CPU) OS ([\d_]+)", ua):
        bits.append("iOS " + m.group(1).replace("_", "."))
    elif "Macintosh" in ua:
        bits.append("Mac")
    elif "Windows" in ua:
        bits.append("Windows")
    chrome = str(h.get("chrome") or "").split(".")[0]
    if not chrome and (m := re.search(r"(?:Chrome|CriOS)/(\d+)", ua)):
        chrome = m.group(1)
    if chrome:
        bits.append(f"Chrome {chrome}")
    elif m := re.search(r"Version/([\d.]+).*Safari", ua):
        bits.append(f"Safari {m.group(1)}")
    elif m := re.search(r"Firefox/(\d+)", ua):
        bits.append(f"Firefox {m.group(1)}")
    return ", ".join(bits) or "unknown device"


def audio_words(ev: dict) -> str:
    """An audio_route record (page/speaker.js) as one short line: "Audio (mic opened): Pixel 8, Android 16,
    Chrome 142; inputs Speakerphone, Headset earpiece; mic Speakerphone"."""
    d = ev.get("data") or {}
    if d.get("reason") in AUDIO_RESULT_REASONS:
        why = AUDIO_RESULT_REASONS[d["reason"]] + ": " + str(d.get("result") or "?")
    else:
        why = AUDIO_REASONS.get(d.get("reason"), str(d.get("reason") or "?"))
    out = [_device(d)]
    if d.get("inputs"):
        out.append("inputs " + ", ".join(map(str, d["inputs"])))
    if d.get("outputs"):
        out.append("outputs " + ", ".join(map(str, d["outputs"])))
    if d.get("track_label"):
        out.append(f"mic {d['track_label']}")
    s = d.get("settings") or {}
    if s.get("echoCancellation") is False:
        out.append("echo cancellation off")
    if d.get("live_mic_tracks") and d["live_mic_tracks"] > 1:
        out.append(f"{d['live_mic_tracks']} mic tracks live")
    return _clip(f"Audio ({why}): " + "; ".join(out), 400)


def _block(kind: str, sid: str, rows: list[dict], speaker: dict) -> dict | None:
    """One transcript file as a block: its header and its items (each with a datetime `_t`)."""
    got = EH.timeline_of(rows, "", sid)
    first = next((r for r in rows if _local(r.get("t"))), None)
    if first is None:
        return None
    label = PARTS[kind]
    mode = next((r.get("mode") for r in rows if r.get("who") == "session" and r.get("mode")), None)
    if kind == "conversation" and mode in LIBRARY:
        label = LIBRARY[mode]
    items = []
    for x in got["items"]:
        t = _local(x.get("t"))
        if t is None:
            continue
        k = x["kind"]
        if k == "line":
            items.append({"_t": t, "kind": "line", "who": x["who"], "speaker": speaker.get(x["who"], x["who"]),
                          "text": x["text"]})
        elif k == "note":
            if x.get("whisper"):
                continue   # the earpiece's: the Earpiece history shows it
            text = x["text"]
            if text == debrief.STEPPED_BACK:
                items.append({"_t": t, "kind": "event", "type": "rest", "text": "The guide stepped back (resting)"})
            elif m := debrief.NEW_SESSION.match(text):
                why = RESUMED.get(m.group(1), m.group(1))
                items.append({"_t": t, "kind": "event", "type": "resume", "text": f"The guide came back: {why}"})
            else:
                items.append({"_t": t, "kind": "event", "type": "note", "text": "App note to the voice",
                              "detail": text})
        elif k == "tool":
            items.append({"_t": t, "kind": "event", "type": "chapter" if x["name"] == "start_chapter" else "tool",
                          "text": tool_words(x["name"], x.get("input"), x.get("result"))})
        elif k == "session" and x.get("ended"):
            items.append({"_t": t, "kind": "event", "type": "ended",
                          "text": ENDED.get(x["ended"], f"Ended: {x['ended']}")})
        # GPT-Live session rows (the header and "came back" say it) and the earpiece's looks are left out
    return {"kind": kind, "sid": sid, "label": label, "_t": _local(first.get("t")), "items": items}


def _chapter_items(ch: dict, sessions: set) -> list[dict]:
    """A chapter's own markers: begun, opening ready, read aloud, questions asked while read, how it ended."""
    out = []
    title = ch.get("title")
    status = ch.get("status") or {}
    named = f"“{title}”" if title else "the chapter"
    if t := _local(status.get("started")):
        out.append({"_t": t, "_p": "chapter", "type": "chapter",
                    "text": f"Chapter begun: {named}" + (" (a rehearsal: a finished chapter replayed)"
                                                         if ch.get("rehearsal") else "")})
    for rec in ch.get("timeline") or []:
        if rec.get("event") == "first_audio" and (t := _local(rec.get("ts"))):
            out.append({"_t": t, "_p": "chapter", "type": "chapter", "text": "The chapter's opening was ready to hear"})
            break
    for rec in ch.get("reading") or []:
        if (t := _local(rec.get("t"))) and rec.get("id"):
            out.append({"_t": t, "_p": "reading", "kind": "reading", "run": ch.get("run"), "id": rec["id"],
                        "sentence": rec.get("sentence") or 0, "cut": bool(rec.get("cut"))})
    for a in ch.get("asides") or []:
        t = _local(a.get("started"))
        if t is None:
            continue
        where = f" at {_paragraph(a.get('paragraph'))}" if a.get("paragraph") else ""
        what = "A check-in while the next part was written" if a.get("kind") == "check_in" \
            else f"Question asked mid-chapter{where}"
        out.append({"_t": t, "_p": "aside", "type": "aside", "text": what})
        if a.get("session") not in sessions:   # not in a transcript we have: its own lines
            for i, line in enumerate(a.get("transcript") or []):
                if str(line.get("text") or "").strip():
                    out.append({"_t": t + timedelta(milliseconds=i + 1), "_p": "aside", "kind": "line",
                                "who": line.get("who"), "text": str(line["text"]).strip()})
        try:
            secs = float(a.get("seconds"))
        except (TypeError, ValueError):
            secs = None
        if secs is not None:
            why = ASIDE_ENDED.get(a.get("ended"), a.get("ended") or "")
            what = "Check-in over" if a.get("kind") == "check_in" else "Question over"
            out.append({"_t": t + timedelta(seconds=secs), "_p": "chapter", "type": "aside",
                        "text": f"{what} ({round(secs)} s)" + (f": {why}" if why else "")})
    st = status.get("state")
    # "ready" is when the last part was written: the timeline's own `ready` event. status.json's `updated` moves
    # later for a live run (the archive rewrites it once the chapter has been heard, often in the debrief).
    ready_at = next((t for rec in ch.get("timeline") or [] if rec.get("event") == "ready"
                     and (t := _local(rec.get("ts")))), None) if st == "ready" else None
    if st in ("stopped", "problem", "ready") and (t := ready_at or _local(status.get("updated"))):
        text = {"stopped": "Chapter stopped before it was finished", "problem": "Chapter hit a problem",
                "ready": "The whole chapter was written"}[st]
        if st != "ready" and status.get("message"):
            text += f": {_clip(status['message'], 200)}"
        out.append({"_t": t, "_p": "chapter", "type": "chapter", "text": text})
    return out


def _collapse_reading(items: list[dict]) -> list[dict]:
    """Paragraphs read in a row, one event: "Read aloud: part 1, paragraphs 1 to 5 (5 cut off)"."""
    out: list[dict] = []
    run: list[dict] = []

    def flush():
        if not run:
            return
        parts: dict[int, list[str]] = {}
        cut = []
        for r in run:
            m = PARAGRAPH.match(r["id"])
            sec, para = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            label = str(para) if m else r["id"]
            if r["sentence"]:
                label += f" (from sentence {int(r['sentence']) + 1})"
            parts.setdefault(sec, []).append(label)
            if r["cut"]:
                cut.append(str(para) if m else r["id"])
        bits = []
        for sec, paras in parts.items():
            if len(paras) > 2 and all(p.isdigit() for p in paras) and \
                    [int(p) for p in paras] == list(range(int(paras[0]), int(paras[0]) + len(paras))):
                bits.append(f"part {sec}, paragraphs {paras[0]} to {paras[-1]}")
            else:
                bits.append(f"part {sec}, paragraph{'s' if len(paras) > 1 else ''} {', '.join(paras)}")
        text = "Read aloud: " + "; ".join(bits) + (f" ({', '.join(cut)} cut off)" if cut else "")
        out.append({**run[0], "kind": "event", "type": "reading", "text": text})
        run.clear()

    for x in items:
        if x.get("kind") == "reading":   # one paragraph, not yet collapsed
            if run and run[-1].get("run") != x.get("run"):
                flush()
            run.append(x)
        else:
            flush()
            out.append(x)
    flush()
    return out


def build(*, name: str | None = None, intros=(), conversations=(), debriefs=(), events=(), chapters=()) -> dict:
    """The visit's timeline from rows (pure).

    intros, conversations, debriefs: [(id, rows)] (a transcript file each); events: study/events.jsonl rows;
    chapters: [{"run", "title", "rehearsal", "status", "timeline", "asides", "reading"}] (status.json,
    fast_timeline.jsonl, 12_asks/asides.jsonl and live/reading.jsonl as read).

    Returns {"name", "started", "ended", "items"}: items in order, each {"kind": "part"|"line"|"event", "t",
    "clock", "secs" (since the visit began), "text"}, a line with "who" and "speaker", an event with "type"
    (note, tool, chapter, rest, resume, ended, press, reading, aside) and for an app note "detail" (its words),
    audio (page/speaker.js's audio_route records: the device, its audio inputs, the mic in use), a part with "part"
    (intro, conversation, debrief, or records: chapter markers and presses older than every
    transcript file, one part per day; the first part starts at the tap's mic records just before it, LEAD_IN_SECS),
    "id" (the file's) and "date"."""
    speaker = {"interviewer": "Interviewer", "guide": "Guide", "listener": name or "Tester"}
    blocks = [b for kind, pairs in (("intro", intros), ("conversation", conversations), ("debrief", debriefs))
              for sid, rows in pairs if (b := _block(kind, sid, rows, speaker))]
    blocks.sort(key=lambda b: b["_t"])
    sessions = {b["sid"] for b in blocks if b["kind"] == "conversation"}

    free = [{"_t": t, "_p": "press", "kind": "event", "type": "audio", "text": audio_words(ev),
             "_reason": (ev.get("data") or {}).get("reason")}
            if ev.get("event") == debrief.AUDIO_ROUTE else
            {"_t": t, "_p": "press", "kind": "event", "type": "press", "text": press_words(ev)}
            for ev in events if (t := _local(ev.get("t")))]
    for ch in chapters:
        free += [{"kind": "event", **x} for x in _chapter_items(ch, sessions)]
    for x in free:
        if x.get("kind") == "line":
            x["speaker"] = speaker.get(x["who"], x["who"])
    # The mic's records from the tap that opened the first part start that part, rather than an EARLIER part of
    # their own: the visit begins with the tap. (Later parts keep their own start: a press such as End demo, just
    # before the debrief, stays in the part it was pressed in.)
    for x in sorted((x for x in free if x.get("_reason") in LEAD_IN_REASONS), key=lambda x: x["_t"], reverse=True):
        if blocks and x["_t"] < blocks[0]["_t"] and (blocks[0]["_t"] - x["_t"]).total_seconds() <= LEAD_IN_SECS:
            blocks[0]["_t"] = x["_t"]
    if not blocks and free:
        blocks = [{"kind": None, "sid": None, "label": None, "_t": min(x["_t"] for x in free), "items": []}]
    elif blocks:
        # Chapter markers and presses older than every transcript file (Ian's own folder: days of chapters, few
        # sessions files) get blocks of their own, one per day, rather than the first part with negative times.
        days: dict = {}
        for x in free:
            if x["_t"] < blocks[0]["_t"]:
                days.setdefault(x["_t"].date(), []).append(x)
        blocks = [{"kind": "records", "sid": None, "label": EARLIER, "_t": min(x["_t"] for x in xs), "items": []}
                  for xs in days.values()] + blocks
        blocks.sort(key=lambda b: b["_t"])
    for x in free:
        home = next((b for b in reversed(blocks) if b["_t"] <= x["_t"]), blocks[0])
        home["items"].append(x)

    items: list[dict] = []
    t0 = blocks[0]["_t"] if blocks else None
    for b in blocks:
        ordered = sorted(enumerate(b["items"]), key=lambda iv: (iv[1]["_t"], PRIO[iv[1].get("_p", "row")], iv[0]))
        body = _collapse_reading([x for _, x in ordered])
        if b["label"]:
            items.append({"_t": b["_t"], "kind": "part", "part": b["kind"], "id": b["sid"], "text": b["label"],
                          "date": b["_t"].strftime("%a %d %b")})
        items += body
    times = [x["_t"] for x in items]
    out = []
    for x in items:
        t = x["_t"]
        drop = ("_t", "_p", "_reason", "run", "sentence", "cut") + (() if x["kind"] == "part" else ("id",))
        out.append({**{k: v for k, v in x.items() if k not in drop},
                    "t": t.isoformat(timespec="seconds"), "clock": t.strftime("%H:%M:%S"),
                    "secs": int((t - t0).total_seconds())})
    return {"name": name, "started": min(times).isoformat(timespec="seconds") if times else None,
            "ended": max(times).isoformat(timespec="seconds") if times else None, "items": out}


def read(folder: Path, name: str | None = None) -> dict:
    """The visit of the listener in `folder` (a tester's, or Ian's own)."""
    folder = Path(folder)
    study = folder / "study"

    def files(pattern: str) -> list:
        return [(p.stem, EH._rows(p)) for p in sorted(study.glob(pattern))] if study.is_dir() else []

    chapters = []
    for root in state.chapters(folder):
        fast = profiles.read_json(root / "fast.json") or {}
        chapters.append({"run": root.name, "title": state.chapter_title(root),
                         "rehearsal": bool(isinstance(fast, dict) and fast.get("rehearsal")),
                         "status": profiles.read_json(root / "status.json") or {},
                         "timeline": state.read_jsonl(root / "fast_timeline.jsonl"),
                         "asides": state.read_jsonl(root / "12_asks" / "asides.jsonl"),
                         "reading": state.read_jsonl(root / "live" / "reading.jsonl")})
    return build(name=name, intros=files("intro-*.jsonl"), debriefs=files("debrief-*.jsonl"),
                 conversations=[(p.stem, EH._rows(p)) for p in sorted(EH._files(folder))],
                 events=debrief.events(folder) + debrief.audio_routes(folder), chapters=chapters)


def text(visit: dict, title: str | None = None) -> str:
    """The timeline as plain text: a header per part, "[HH:MM:SS] Speaker: words" per line, "  · ..." per event
    (an app note with its words)."""
    head = f"Transcript: {title or visit.get('name') or 'listener'}"
    if visit.get("started"):
        s, e = _local(visit["started"]), _local(visit.get("ended"))
        head += f", {s:%a %d %b %Y}, {s:%H:%M:%S}" + (
            "" if not e else f" to {e:%H:%M:%S}" if e.date() == s.date() else f" to {e:%a %d %b %Y}, {e:%H:%M:%S}")
    out = [head, "Times are when each line ended. Earpiece notes are in the Earpiece history."]
    days = len({x.get("date") for x in visit.get("items") or [] if x["kind"] == "part"}) > 1
    for x in visit.get("items") or []:
        if x["kind"] == "part":
            out += ["", f"== {x['text']} ({x['date'] + ', ' if days and x.get('date') else ''}{x['clock']})"]
        elif x["kind"] == "line":
            out.append(f"[{x['clock']}] {x['speaker']}: {x['text']}")
        else:
            out.append(f"[{x['clock']}]   · {x['text']}" + (f": {' '.join(x['detail'].split())}" if x.get("detail") else ""))
    if not visit.get("items"):
        out += ["", "Nothing recorded yet."]
    return "\n".join(out).rstrip() + "\n"
