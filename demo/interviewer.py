"""The interviewer (docs/tester-demo.md, "The interviewer"; contracts in docs/tester-demo-design.md, part C).

A separate GPT-Live session from the guide's, in its own voice (demo.interviewer.voice), twice per tester:

    intro     checks who it is by the name given with the link ("Hi, is this Rolando?", its one question;
              asks the name when there is none; set_listener_name for a correction), then in one turn says
              Ian built what they're about to try, how this will go, and asks "Any questions, or are you
              ready to try it?"; then "I'll hand you over now." (handoff_to_guide), and the page brings in
              the guide. About 30-40 seconds.
    debrief   a short conversation gathering product feedback for Ian: the opening question, at most two
              more picked from what the tester did (demo/debrief.py: their moments and the account of what
              they did) and from TOPICS, and it ends itself with one fixed farewell (end_debrief) as soon
              as it has a few real comments, they sound finished or they ask to stop. Three minutes at
              most, often less.

Ian's notes after his own run (2026-11-16) made both what they were: the intro had taken 15 seconds, and in the
debrief the interviewer answered him with "Mm-hmm", "Mm.", "Hmm." and then nothing for 77 seconds. Nothing
muted it (his words were transcribed throughout): its instructions told it to leave pauses, not to fill a
silence and to keep to five fixed questions, and no page cue covered a tester left waiting. The prompts now
have it reply, and the page sends `waiting` (page/interview.js) when it hasn't.
His notes after his second run (2026-11-18): the intro "spoke a little too much" (49 seconds over six turns,
stopping after each sentence for an "okay"), the debrief had too many questions, and it asked about things
that hadn't happened ("Earlier, you chose not to cut in": he had heard none of the piece). So the intro is one
question and one turn, the debrief is three or four questions, and it is told what the tester did
(`account`, from demo/debrief.activity) and asks only about that.
His note on that debrief (2026-11-18): "there was no clear exit... the interviewer kept asking questions". He
had given long answers from the first one, said "let's end the conversation here" and got the closing question.
So the questions are a ceiling, the closing question is optional (never after they've said they're done), and
the end is one farewell (prompts/interviewer_notes.md `farewell`) that the page and `covered` key on (FAREWELL,
PAGE_DEFAULTS["farewell"]): once it is said the debrief is finished, with or without end_debrief, provided the
tester said something (a debrief nobody answered stays not done, so it can be taken again). The debrief has its
own `waiting` note (`waiting_debrief`): the intro's ends "then carry on", which after "let's end here" is the
wrong push.

The session is opened like the guide's (voice/gpt_live/session.py): the page's WebRTC offer goes to OpenAI with
the configuration and the key stays here. The tools run through GPT-Live's delegation to a Responses backend
(prompts/interviewer_backend.md), which the voice only uses when its instructions carry a Delegation policy
naming them (prompts/interviewer_intro.md, _debrief.md); the page runs them at /api/interview/tool.

Everything is kept in listeners/<lid>/study/, never sessions/: memory.meeting() counts any sessions/*.jsonl
the tester spoke in as a past conversation with the guide, which would then skip introducing itself.

    intro-<sid>.jsonl, debrief-<sid>.jsonl   transcripts ({"t", "s", "who", "text", ...}; who: session, page,
                                             listener, interviewer, tool); a debrief's first record keeps what
                                             it was told the tester did (`did`, `activity`)
    log.jsonl                                costs (GPT-Live per session, kind "interviewer"; the summary's Claude call)
    events.jsonl                             the tester's button presses on the product page (POST /api/demo/event,
                                             demo/routes.py; demo/debrief.py reads them for the debrief's account)
    <live_id>.mp3 (or .wav if ffmpeg fails)  the session's recording from OpenAI (store: true), fetched after
                                             live.archive_wait_seconds in a background thread
    debrief.json                             {done, finished_at, topics, closing, farewell, session, how}: the
                                             first completed debrief's, never overwritten by a later one
                                             (debrief-<sid>.json); closing: the optional closing question was
                                             asked; farewell: its last words were said; how "no answer": it
                                             ended with no word from the tester, and is not done
    debrief_summary.md                       one Claude call (prompts/debrief_summary.md); the first completed
                                             debrief's, a later one writes debrief_summary-<sid>.md
    After the end, nothing is written once the folder is deleted or reset (tester.json's stamps changed).

Routes (mount(app, cfg); part A's middleware scopes each request to the tester's listener folder):

    POST /api/interview/start  {mode, sdp} -> {sid, live_id, sdp, notes, settings}
    POST /api/interview/tool   {sid, name, args} -> the tool's result, with PAGE_ONLY fields the page strips
                               before the backend: `act` (name, handover, close) and, at the handover, what
                               the page loads next (`scripts`, `entry`)
    POST /api/interview/line   {sid, who: in|out|page, text}: a transcript line (in: what GPT-Live heard of
                               them; out: what the interviewer said)
    POST /api/interview/usage  {sid, live_id, seconds, backend}: the session's cost (once per live_id)
    POST /api/interview/end    {sid, how}: closes; fetches the audio, and for a debrief writes the summary
    GET  /interview.js         page/interview.js
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from stages import common
from stages.common import PROMPTS_DIR, ROOT, Run, StageError, load_prompt

INTERVIEW_JS = ROOT / "page" / "interview.js"
TOOLS_FILE = PROMPTS_DIR / "interviewer_tools.json"
MODES = ("intro", "debrief")
MODE_TOOLS = {"intro": ("set_listener_name", "handoff_to_guide"), "debrief": ("end_debrief",)}
WHO = {"in": "listener", "out": "interviewer", "page": "page"}
# A tool result's fields for the page alone, stripped before the backend sees it: what the page does next
# (`act`), and what it loads at the handover. The page acts on `act`, never on a tool's name, and learns the
# names it may call itself from its settings (`acts`): the gate's source carries no product wording.
PAGE_ONLY = ("act", "scripts", "entry")
ACTS = {"handover": "handoff_to_guide", "close": "end_debrief"}
NAME_MAX = 60

DEFAULTS = {
    "voice": "cedar", "store": True,
    "intro_max_minutes": 2.5, "debrief_max_minutes": 6, "debrief_target_minutes": 4,
    "stale_minutes": 15,              # a session the page never ended is closed after this long without a line
    "audio_tries": 6,                 # the recording: tries after live.archive_wait_seconds, live.archive_retry_seconds apart
    "summary_role": "researcher", "summary_effort": "medium",
    # What the page loads at the handover (part D's product page), in order, and the object it hands over to.
    # Here, not in the gate's source, which must carry no product wording.
    "scripts": ["/align.js", "/live.js", "/library.js"], "entry": "Live",
    "page": {},
}
PAGE_DEFAULTS = {
    "line_quiet_ms": 900,             # a transcript line ends after this much quiet
    "close_quiet_ms": 1400,           # after handoff/end: close once both sides have been quiet this long
    "close_min_ms": 800,
    "close_max_ms": 9000,             # ... or after this long whatever happens
    "greet_fallback_ms": 4000,        # nothing said this long after the start: the opening note again, as an instruction
    "start_timeout_ms": 10000,
    "speaking_level": 0.02,           # the remote voice's level above which the dot pulses
    "act_grace_ms": 3500,             # a handover or the farewell said with no tool call: the page acts after this
    "handover_quiet_ms": 900,         # the handover's last words: the next voice comes in once both sides are this quiet
    # The page acts on the handover's words only once the tester has spoken and the intro is this old (a
    # tester never heard: three times this): the words can never end the intro in its opening (the tool, and
    # the clock at intro_max_minutes, still can). 10 s since the intro became 30-40 s (2026-11-18): a tester
    # whose mic isn't heard is then handed over on the words from 30 s, not left until the clock.
    "handover_min_ms": 10000,
    "reply_wait_ms": 6000,            # they finished this long ago and it has said nothing but a backchannel: `waiting`
    "reply_words": 3,                 # a line of the voice's with fewer words than this is a backchannel, not a reply
    # Only the final "I'll hand you over now.": the intro also explains that a handover is coming ("before I
    # hand you over, any questions?"), and the page acts on these words the moment it hears them.
    "handover": r"\bhand(ing)? you over now\b",
    "closing_question": r"\banything I didn['’]?t ask\b",  # the page also straightens curly quotes first
    "thanks": r"\bthank(s| you)\b",
    # The debrief's last words (prompts/interviewer_notes.md `farewell`): once heard, the page ends the debrief
    # after act_grace_ms if end_debrief hasn't come, whether or not the closing question was asked.
    "farewell": r"\bthat['’]?s everything from me\b",
}

# What the debrief is for, as the interviewer's lines show it came up (prompts/interviewer_debrief.md, the opening
# question and "WHAT IAN MOST WANTS TO LEARN"; the same six in prompts/debrief_summary.md and study_report.md).
# Matching is loose on purpose: it is a conversation, and GPT-Live rarely says a line word for word. None is
# required, and since 2026-11-18 a debrief brings up three of them at most.
TOPICS = {
    "stood_out": r"\b(stood|stand|stands) out\b|stuck with you|stay(ed|s)? with you|tell Ian first",
    "use": r"\b(you'd|you would|you could|would you|could you|do you|can you)\b[^.?!]*\b(use|using|come back to|listen again)\b",
    "future": r"\bimagine\b|\bbecome\b|where (this|it) (could|might|should|would) go|want (this|it) to (be|do)\b",
    "voice": r"\bvoices?\b",
    # "you asked for quiet" and "you asked how it was coming along" are about the wait, not this
    "asking": r"\byou asked\b(?! for (some )?quiet| how (it|the piece) was)|stop(ped)? it\b[^.?!]{0,40}\bask"
              r"|\bask(ing|ed)?\b[^.?!]*\bquestions?\b|\binterrupt",
    "confusing": r"confus|(got|get|getting) in (the|your) way|unclear|didn't know what to do|trip(ped)? you up",
}
CLOSING = r"anything I didn'?t ask"  # matched, like TOPICS, on norm_quotes(text)
THANKS = r"\bthank(s| you)\b"
FAREWELL = r"\bthat'?s everything from me\b"  # the end of the farewell (prompts/interviewer_notes.md `farewell`)

BACKGROUND: list[threading.Thread] = []  # after-end work (audio, summary): tests join these
_FILE_LOCK = threading.Lock()


def icfg(cfg: dict) -> dict:
    got = ((cfg.get("demo") or {}).get("interviewer") or {})
    out = {**DEFAULTS, **got}
    out["page"] = {**PAGE_DEFAULTS, **(got.get("page") or {})}
    return out


def notes() -> dict[str, str]:
    """prompts/interviewer_notes.md: `key: text` lines."""
    out = {}
    for line in load_prompt("interviewer_notes").splitlines():
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[a-z0-9_]+", k.strip()):
            out[k.strip()] = v.strip()
    return out


def _fill(text: str, **fields) -> str:
    for k, v in fields.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def farewell(name: str | None, n: dict | None = None) -> str:
    """The debrief's last words, by name when there is one (prompts/interviewer_notes.md `farewell`): the one
    wording the prompt gives it ({{farewell}}), and what PAGE_DEFAULTS["farewell"] and FAREWELL key on."""
    n = n or notes()
    name = (name or "").strip()
    return _fill(n["farewell"], name=name) if name and name != n["name_unknown"] else n["farewell_unnamed"]


# The interviewer confirms a name as "Sam. Did I get that right?" (prompts/interviewer_notes.md step_first,
# step_named); a correction gets a second confirmation, so the last one counts. Failing that, the tester's
# answer to the name question ("Sam.", "It's Sam Lee", or "No, it's Roland" after "Hi, is this Rolando?"), then
# (only when no name is known yet) their own "I'm Sam" anywhere. Only the phrases are case-insensitive: a name
# needs a real capital, and a word in NOT_NAMES is never one ("Sam. Got it. Did I get that right?" once saved
# "Got it"). Matched on norm_quotes(text).
NAME = r"[A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+)?"
CONFIRM_RE = re.compile(r"\b(" + NAME + r")[.,!]?\s+"
                        r"(?:(?i:got it|okay|ok|right|great|lovely|perfect|nice|thanks|thank you)[.,!]?\s+)?"
                        r"(?i:did i get that right|is that right|have i got that right|did i say that right)")
SELF_RE = re.compile(r"\b(?i:i'?m|i am|my name is|my name's|this is|it'?s|call me)\s+(" + NAME + ")")
NAME_ASKED_RE = re.compile(r"(?i:\byour name\b|\bcall you\b|\bis this\b|\b(talking|speaking) (to|with)\b)")
# With a name known, an answer that opens with a yes confirms it: nothing in that line replaces it ("Yes, it's
# Orlando" is the transcript mishearing Rolando more often than a correction; a real one is said back and
# confirmed by the interviewer, which CONFIRM_RE takes).
YES_RE = re.compile(r"^\s*(?i:yes|yeah|yep|yup|that's me|speaking|it is|correct|sure|mm-?hm+|uh-?huh)\b")
ANSWER_RE = re.compile(r"^\s*(?:(?i:it'?s|i'?m|i am|my name is|my name's|call me|just|oh|um|uh|yeah|yes|sure|"
                       r"hi|hello|hey)[,.]?\s+)*(" + NAME + r")\s*[.!,]?\s*$")
NOT_NAMES = {"Yes", "Yeah", "Yep", "No", "Nope", "Sorry", "Okay", "Ok", "Alright", "All", "Hi", "Hello", "Hey",
             "Thanks", "Thank", "Right", "Great", "So", "And", "Well", "Um", "Uh", "Oh", "Ah", "Hmm", "Sure",
             "Just", "Not", "Here", "Fine", "Good", "Ready", "Curious", "Happy", "Got", "It", "It's", "Its",
             "That", "That's", "This", "Lovely", "Perfect", "Nice", "Cool", "Wonderful", "Brilliant", "Awesome",
             "Excellent", "Glad", "Exactly", "Correct", "Totally", "Did", "Is", "Have", "I", "I'm", "My", "The",
             "A", "An", "What", "Wait", "Pretty", "Really", "Very", "Busy", "Tired", "Doing",
             "Absolutely", "Definitely", "Certainly", "Please", "Of", "Course", "Indeed", "Yup", "Mhm", "Uhm",
             "Fair", "True", "Agreed", "Sounds", "Morning", "Afternoon", "Evening", "Today", "Tonight",
             "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def norm_quotes(text) -> str:
    """Curly apostrophes as straight ones: GPT-Live's transcripts may write didn\u2019t and I\u2019m."""
    return str(text or "").replace("\u2019", "'").replace("\u2018", "'")


def _a_name(found: str | None) -> str | None:
    """A matched name without its leading stop words ("Okay Sam" is Sam), up to its next one; None when no
    word of it is a name."""
    words = [w.strip(".,!?") for w in (found or "").split()]
    while words and words[0] in NOT_NAMES:
        words = words[1:]
    if not words:
        return None
    out = []
    for w in words:
        if w in NOT_NAMES or w.endswith("'s"):  # "it's Ian's thing" is not a name
            break
        out.append(w)
    return " ".join(out) or None


def _who_check(known: str) -> re.Pattern:
    return re.compile(r"(?i:\byour name\b|\bis this\s+" + re.escape(known.strip()) + r"\b)")


def confirmed_name(lines: list[dict], known: str | None = None) -> str | None:
    """The name from an intro's transcript lines ({who, text}): the interviewer's last confirmation, else the
    tester's answer right after it asked their name or checked who they are, else (with no name known) their
    own introduction anywhere. With a name known, a tester's line counts only as the answer to the who-check
    itself ("is this <known>", "your name"); anything later must come through the interviewer's confirmation. None when none is there: a known name then stays, and with none the guide asks,
    as it always could. `known` is the name given with the link: it is never replaced by a weaker guess (a
    line that opens with a yes, or an "it's ..." somewhere later in the talk)."""
    said = [(x.get("who"), norm_quotes(x.get("text"))) for x in lines]
    found = None
    for who, text in said:
        if who == "interviewer":
            for m in CONFIRM_RE.finditer(text):
                found = _a_name(m.group(1)) or found
    if found:
        return found
    asked = False
    for who, text in said:
        if who == "interviewer":
            # With a name known, only the who-check itself asks ("Hi, is this Rolando?", "your name"): the
            # intro's "you'll be talking with someone else" once let "It's Ian who sent me" replace the name.
            asked = bool(_who_check(known).search(text) if known else NAME_ASKED_RE.search(text))
        elif who == "listener" and asked:
            asked = False  # only the answer right after the question
            if known and YES_RE.match(text):
                continue
            m = ANSWER_RE.match(text)
            name = _a_name(m.group(1)) if m else None
            for m in SELF_RE.finditer(text) if not name else ():
                name = name or _a_name(m.group(1))
            if name:
                return name
    if known:
        return None
    for who, text in said:
        if who == "listener":
            for m in SELF_RE.finditer(text):
                name = _a_name(m.group(1))
                if name:
                    return name
    return None


def tools(mode: str) -> list[dict]:
    """The mode's tools as Responses function tools for the delegation backend."""
    raw = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    return [{"type": "function", "name": t["name"], "description": t["description"],
             "parameters": t["input_schema"], "strict": False} for t in raw if t["name"] in MODE_TOOLS[mode]]


# --- what the interviewer is told ------------------------------------------------------------------

def tester_record(listener_dir: Path) -> dict:
    """tester.json (part B), read directly: the interviewer only needs the name."""
    try:
        return json.loads((Path(listener_dir) / "tester.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _when(seconds: float | None, n: dict) -> str:
    if seconds is None or seconds < 45:
        return n["when_seconds"]
    m = int(round(seconds / 60))
    return n["when_minute"] if m <= 1 else _fill(n["when_minutes"], n=m)


def intro_values(listener_dir: Path) -> dict:
    """The intro's placeholders: with a name (given with the link, or saved by an earlier try) it checks who
    it is; with none it asks."""
    return intro_values_for((tester_record(listener_dir).get("name") or "").strip())


def intro_values_for(name: str | None) -> dict:
    n = notes()
    if name:
        return {"meeting": _fill(n["meeting_named"], name=name), "first_step": _fill(n["step_named"], name=name),
                "_name": name}
    return {"meeting": n["meeting_first"], "first_step": n["step_first"], "_name": None}


def debrief_values(cfg: dict, listener_dir: Path) -> dict:
    """The debrief's placeholders: name, the chapter (title, summary, how much they heard), their moments and
    what they did (demo/debrief.py). The topic about asking questions names their first moment, asks what
    happened when they tapped to ask (no words of theirs recorded), asks whether they tried it, or (when they
    heard none of the piece, or none was made) is not to be asked at all."""
    from demo import debrief
    name = (tester_record(listener_dir).get("name") or "").strip()
    return values_from(name, debrief.chapter_context(listener_dir, cfg), debrief.moments(listener_dir, cfg),
                       debrief.activity(listener_dir, cfg, now=time.time()))


def _times(k, n: dict) -> str:
    k = int(k)
    return n["times_1"] if k <= 1 else n["times_2"] if k == 2 else _fill(n["times_n"], n=k)


def _span(seconds: float, n: dict) -> str:
    m = int(round(seconds / 60))
    return n["talk_short"] if seconds < 45 else n["talk_minute"] if m <= 1 else _fill(n["talk_minutes"], n=m)


def heard_none(ctx: dict, act: dict | None) -> bool:
    """They heard none of the piece: no position past its start, and no record that any of it was read."""
    return float(ctx.get("minutes_heard") or 0) <= 0 and not (act or {}).get("listened")


def _heard_line(ctx: dict, n: dict) -> str:
    minutes, heard = ctx.get("minutes"), float(ctx.get("minutes_heard") or 0)
    if minutes and heard >= minutes - 0.5:
        return _fill(n["heard_all"], minutes=round(minutes))
    if heard < 1:
        return n["heard_little"]
    return _fill(n["heard_some"], heard=round(heard), minutes=round(minutes or 10))


def account(ctx: dict, got: list[dict], act: dict | None, n: dict | None = None) -> list[str]:
    """What the tester did, as the lines the interviewer (and the summary) reads: only what the records show
    (demo/debrief.activity), in the words of prompts/interviewer_notes.md `did_*`. With no activity (`act`
    None: the app kept no record), the piece, how much they heard and their moments, as before."""
    n = n or notes()
    a = act or {}
    piece = bool(ctx.get("run")) or bool(a.get("started"))
    phase = (a.get("end") or {}).get("phase")
    if phase == "making" and a.get("ready"):
        phase = "talk"  # the opening was ready and offered: they were talking, not waiting on it (the page says "making" until it is read)
    ended = [n.get(f"did_end_{phase}") or n["did_end"]] if a.get("end") else []
    out = []
    if a.get("talk_s") is not None:
        out.append(_fill(n["did_talk_started" if piece else "did_talk_only"], talk=_span(a["talk_s"], n)))
    if not piece:
        return (out + ended) or [n["did_unknown"]]
    unheard = act is not None and heard_none(ctx, act)
    if ctx.get("title") and (unheard or not ctx.get("summary")):
        # the title alone: no summary of a piece they never heard (nothing to ask about it), or none written
        out.append(_fill(n["did_piece_title"], title=ctx["title"]))
    elif ctx.get("summary") and not unheard:
        out.append(_fill(n["did_piece"], title=ctx.get("title") or n["title_unknown"], summary=ctx["summary"]))
    if a.get("quiet_asks"):
        out.append(_fill(n["did_quiet_asked"], times=_times(a["quiet_asks"], n)))
    elif a.get("step_backs"):
        out.append(n["did_quiet_given"])
    if a.get("taps"):
        out.append(_fill(n["did_tap_talk"], times=_times(a["taps"], n)))
    if a.get("status_asks"):
        out.append(_fill(n["did_status"], times=_times(a["status_asks"], n)))
    if unheard:
        ready = a.get("ready")
        out.append(n["did_none_ready"] if ready else n["did_none"] if ready is None else n["did_none_not_ready"])
    else:
        out.append(_heard_line(ctx, n))
        out += [_fill(n["did_moment"], when=_when(m["at_s"], n), words=m["words"]) for m in got]
        if not got:  # a tap to ask with no words of theirs recorded is told as a tap, never as "never stopped it"
            out.append(_fill(n["did_ask_taps"], times=_times(a["asks"], n)) if a.get("asks") else n["did_no_moments"])
        if a.get("pauses"):
            out.append(_fill(n["did_pauses"], times=_times(a["pauses"], n)))
        if a.get("seeks"):
            out.append(_fill(n["did_seeks"], times=_times(a["seeks"], n)))
    return out + ended


def recorded_account(study: Path) -> str | None:
    """The account the newest debrief was given (its transcript's first record), for the study report: what
    was true when they ended, not what the folder shows later (a piece that finished after they left)."""
    for p in sorted(Path(study).glob("debrief-*.jsonl"), reverse=True):
        for rec in read_lines(p)[:1]:
            if rec.get("who") == "session" and rec.get("did"):
                return str(rec["did"])
    return None


def values_from(name: str, ctx: dict, got: list[dict], act: dict | None = None) -> dict:
    """The debrief's placeholders from a name, a chapter_context, moments and an activity (demo/debrief.py;
    also the simulated checks'). `did` is the account of what they did, one "- " line each; `asking` says how
    the topic about asking questions may come up, from what they did."""
    n = notes()
    bye = farewell(name, n)
    name = name or n["name_unknown"]
    if got:
        asking = _fill(n["asking_moment"], when=_when(got[0]["at_s"], n), words=got[0]["words"])
    elif act is not None and not ctx.get("run") and not act.get("started"):
        asking = n["asking_no_piece"]  # End demo in the conversation: no piece, no wait
    elif act is not None and heard_none(ctx, act):
        asking = n["asking_unheard"]
    elif (act or {}).get("asks"):
        asking = _fill(n["asking_tapped"], times=_times(act["asks"], n))
    else:
        asking = n["asking_none"]
    return {"person": name, "title": ctx.get("title") or n["title_unknown"],
            "summary": ctx.get("summary") or n["summary_unknown"], "heard": _heard_line(ctx, n), "asking": asking,
            "did": "\n".join("- " + x for x in account(ctx, got, act, n)), "farewell": bye,
            "_moments": got, "_context": ctx, "_activity": act}


def instructions(mode: str, values: dict) -> str:
    from voice.gpt_live import session as live_session
    shared = load_prompt("interviewer")
    fields = {k: v for k, v in values.items() if not k.startswith("_")}
    if mode == "debrief":  # a run saved before the farewell (demo/sim_interviewer.py grade) has none
        fields.setdefault("farewell", farewell(fields.get("person")))
    text = load_prompt(f"interviewer_{mode}", shared=shared, **fields)
    if live_session.approx_tokens(text) > live_session.INSTRUCTION_TOKENS:
        raise StageError(f"The interviewer's {mode} instructions are too long for GPT-Live.")
    return text


def session_config(cfg: dict, mode: str, values: dict, *, transport: str = "webrtc") -> dict:
    """The GPT-Live session: the guide's voice model and backend (config live), the interviewer's voice,
    instructions and tools. No startup message: the page sends the opening as speakable context once the
    session starts (live.js does the same for the guide; a startup message alone did not make it speak)."""
    L, I = cfg["live"], icfg(cfg)
    session = {
        "model": L["model"],
        "instructions": instructions(mode, values),
        "audio": {"output": {"voice": I["voice"]}},
        "input": [],
        "delegation": {"type": "responses", "responses": {
            "model": L["backend_model"], "instructions": load_prompt("interviewer_backend"),
            "tools": tools(mode), "tool_choice": "auto", "reasoning": {"effort": L.get("backend_effort", "low")}}},
        "store": bool(I.get("store", True)),
    }
    if transport == "websocket":
        session["audio"]["format"] = {"type": "audio/pcm", "rate": 24000}
    return session


def page_notes(mode: str, values: dict) -> dict:
    """The notes the page sends during this session, filled: `opened`, `waiting`, and the mode's clock notes.
    The debrief's `waiting` is its own (`waiting_debrief`): it never says to carry on, and names the farewell."""
    n = notes()
    if mode == "intro":
        name = values.get("_name") or ""
        got = {"opened": n["opened_intro_named"] if name else n["opened_intro"], "handover_now": n["handover_now"],
               "waiting": n["waiting"]}
    else:
        name = values.get("person") or ""
        got = {"opened": n["opened_debrief"], "time_up": n["time_up"], "wrap_now": n["wrap_now"],
               "waiting": n["waiting_debrief"]}
    return {k: _fill(v, name=name) for k, v in got.items()}


def page_settings(cfg: dict, mode: str) -> dict:
    I = icfg(cfg)
    return {**I["page"], "mode": mode, "acts": dict(ACTS), "intro_max_minutes": I["intro_max_minutes"],
            "debrief_target_minutes": I["debrief_target_minutes"], "debrief_max_minutes": I["debrief_max_minutes"]}


# --- the transcript ---------------------------------------------------------------------------------

def covered(lines: list[dict]) -> dict:
    """What a debrief's lines show: {"topics": [keys of TOPICS, in the order they first came up], "closing":
    the closing question was asked (it is optional), "answered": they said something after it, "thanked": the
    interviewer thanked them after that, "farewell": it said its last words (FAREWELL), "said": the tester said
    anything at all}. A debrief whose farewell was said is finished, and so is one with the first three, whether
    or not end_debrief came; but never one the tester said nothing in (`finished`)."""
    topics: list[str] = []
    closing = answered = thanked = bye = spoke = False
    for x in lines:
        text = norm_quotes(x.get("text"))
        if x.get("who") == "interviewer":
            topics += [k for k, pat in TOPICS.items() if k not in topics and re.search(pat, text, re.I)]
            bye = bye or bool(re.search(FAREWELL, text, re.I))
            if re.search(CLOSING, text, re.I):
                closing, answered, thanked = True, False, False
            elif closing and answered and re.search(THANKS, text, re.I):
                thanked = True
        elif x.get("who") == "listener" and text.strip():
            spoke = True
            answered = answered or closing
    return {"topics": topics, "closing": closing, "answered": answered, "thanked": thanked, "farewell": bye,
            "said": spoke}


def finished(q: dict) -> bool:
    """A debrief's lines (`covered`) show it ended itself: the farewell was said, or (the older shape) the
    closing question was asked and answered and it thanked them after. Never when the tester said nothing: a
    debrief nobody answered (they walked away, or their mic wasn't heard) reaches the farewell on the clock's
    notes alone, and marked done it could not be taken again (End demo is gone for good)."""
    return bool(q.get("said") and (q.get("farewell") or (q.get("closing") and q.get("answered") and q.get("thanked"))))


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def format_transcript(lines: list[dict]) -> str:
    """'[m:ss] who: text' for the summary (times from the session's start)."""
    from demo.debrief import clock
    out = []
    for x in lines:
        if x.get("who") in ("listener", "interviewer") and x.get("text"):
            who = "them" if x["who"] == "listener" else "interviewer"
            out.append(f"[{clock(float(x.get('s') or 0))}] {who}: {x['text']}")
    return "\n".join(out) or "(none)"


def write_json_atomic(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def read_json(p: Path) -> dict:
    try:
        got = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


# --- the debrief's state: the first completed one is kept ---------------------------------------------

def state_path(study: Path, sid: str) -> Path:
    """Where session `sid` keeps its debrief state: debrief.json, unless another session already completed
    it; a later debrief (a second chapter's, if one happens anyway) then writes debrief-<sid>.json."""
    first = read_json(study / "debrief.json")
    if first.get("done") and first.get("session") != sid:
        return study / f"debrief-{sid}.json"
    return study / "debrief.json"


def debrief_done(listener_dir: Path) -> bool:
    """Whether this tester's debrief is done (study/debrief.json): the page then never hands back again."""
    return bool(read_json(Path(listener_dir) / "study" / "debrief.json").get("done"))


# --- whose folder: after-end work never writes into a deleted or reset tester's folder -------------------

def identity(listener_dir: Path) -> tuple | None:
    """The tester a folder holds now: tester.json's created and reset stamps (a reset makes a fresh folder with
    the same created time and a new reset_at), or None for a folder without one."""
    p = Path(listener_dir) / "tester.json"
    if not p.exists():
        return None
    rec = read_json(p)
    return (rec.get("created"), rec.get("reset_at"))


def still_theirs(listener_dir: Path, who: tuple | None) -> bool:
    """The folder still exists and holds the same tester as when the session started."""
    return Path(listener_dir).is_dir() and identity(listener_dir) == who


# --- after a session --------------------------------------------------------------------------------

def _into(study: Path, src: Path, check) -> Path | None:
    """Move a finished file into study/, only while `check()` holds; study/ is made inside the listener folder,
    never the folder itself (a deleted tester's stays deleted)."""
    if check and not check():
        return None
    if not study.parent.is_dir():
        return None
    study.mkdir(exist_ok=True)
    dest = study / src.name
    shutil.move(str(src), str(dest))
    return dest


def fetch_audio(cfg: dict, study: Path, live_id: str, *, sleep=time.sleep, client=None, check=None) -> Path | None:
    """The session's recording from OpenAI (store: true) into study/<live_id>.mp3 by ffmpeg (study/<live_id>.wav
    if ffmpeg fails). OpenAI makes it some time after the session ends: wait live.archive_wait_seconds, then try
    every archive_retry_seconds, demo.interviewer.audio_tries times. It is downloaded and converted in a
    scratch folder and moved in at the end; `check()` (the folder is still this tester's) is asked before every
    try and before the move, and a False stops it quietly (the tester was deleted or reset meanwhile)."""
    from stages import tts
    from voice.gpt_live import session as live_session
    L, I = cfg["live"], icfg(cfg)
    if (study / f"{live_id}.mp3").exists():
        return study / f"{live_id}.mp3"
    sleep(float(L.get("archive_wait_seconds", 120)))
    with tempfile.TemporaryDirectory(prefix="wl-interviewer-") as tmp:
        wav, mp3 = Path(tmp) / f"{live_id}.wav", Path(tmp) / f"{live_id}.mp3"
        got = None
        for k in range(int(I["audio_tries"])):
            if k:
                sleep(float(L.get("archive_retry_seconds", 10)))
            if check and not check():
                return None
            try:
                got = live_session.recording(cfg, live_id, wav, client=client)
            except Exception as e:  # noqa: BLE001 (network, no key: try again, then give up quietly)
                print(f"  interviewer: recording {live_id}: {type(e).__name__}: {e}")
                got = None
            if got:
                break
        if not got:
            print(f"  interviewer: no recording for {live_id} after {I['audio_tries']} tries")
            return None
        try:
            tts.ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)])
        except StageError as e:
            print(f"  interviewer: {live_id}.wav kept ({e})")
            return _into(study, wav, check)
        return _into(study, mp3, check)


def summarize(cfg: dict, study: Path, sid: str, values: dict, *, check=None) -> Path | None:
    """debrief_summary.md from one debrief (one Claude call; its cost goes to study/log.jsonl). Only when
    they said something. It is the first completed debrief's: this debrief writes it when it is that one, or
    when there is no summary yet and no debrief is done; any other (one left halfway after an earlier summary,
    or a later debrief after one completed) writes debrief_summary-<sid>.md, so nothing overwrites it.
    `check()` (the folder is still this tester's) is asked before the call and before the write."""
    from stages import llm
    if check and not check():
        return None
    lines = read_lines(study / f"debrief-{sid}.jsonl")
    if not any(x.get("who") == "listener" and x.get("text") for x in lines):
        return None
    out = study / "debrief_summary.md"
    state = read_json(study / "debrief.json")
    first = state.get("done") and state.get("session") == sid
    if not (first or (not out.exists() and not state.get("done"))):
        out = study / f"debrief_summary-{sid}.md"
    intros = sorted(study.glob("intro-*.jsonl"))
    prompt = load_prompt("debrief_summary", person=values.get("person") or "", title=values.get("title") or "",
                         summary=values.get("summary") or "", did=values.get("did") or notes()["did_unknown"],
                         transcript=format_transcript(lines),
                         intro=format_transcript(read_lines(intros[-1])) if intros else "(none)")
    I = icfg(cfg)
    log = Run(name="study", root=study)
    res = llm.call(log, cfg, "debrief_summary", messages=[{"role": "user", "content": prompt}],
                   role=I["summary_role"], effort=I["summary_effort"], max_tokens=16000, preapproved=True,
                   label="debrief summary", log_extra={"kind": "interviewer", "session": sid})
    text = res.text.strip()
    if not text or (check and not check()) or not study.is_dir():
        return None
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def _after_end(cfg: dict, study: Path, s: dict) -> None:
    """In the background: the summary first (it doesn't need the audio), then the recording. Both stop quietly
    once the tester's folder is gone or holds someone else (deleted or reset in /admin meanwhile)."""
    ld = s.get("listener") or study.parent
    who = s["who"] if "who" in s else identity(ld)
    check = lambda: still_theirs(ld, who)  # noqa: E731
    if s["mode"] == "debrief":
        try:
            summarize(cfg, study, s["id"], s["values"], check=check)
        except Exception as e:  # noqa: BLE001 (a summary that fails leaves the transcript, which is the record)
            print(f"  interviewer: summary for {s['id']} failed: {type(e).__name__}: {e}")
    if s.get("live_id") and icfg(cfg).get("store", True):
        try:
            fetch_audio(cfg, study, s["live_id"], check=check)
        except Exception as e:  # noqa: BLE001
            print(f"  interviewer: audio for {s['id']} failed: {type(e).__name__}: {e}")


# --- routes -----------------------------------------------------------------------------------------

class Start(BaseModel):
    mode: str
    sdp: str


class Tool(BaseModel):
    sid: str
    name: str
    args: dict | None = None
    by: str = "backend"   # "page": the page acted on a line the voice said without calling the tool


class Line(BaseModel):
    sid: str
    who: str
    text: str = ""


class Usage(BaseModel):
    sid: str
    live_id: str
    seconds: float = 0
    backend: dict | None = None


class End(BaseModel):
    sid: str
    how: str = "ended"


def mount(app, cfg: dict) -> None:
    from fastapi import HTTPException
    from fastapi.responses import Response

    open_: dict[str, dict] = {}
    ended: dict[str, dict] = {}  # closed sessions, for a usage report that lands after /end (a beacon)
    lock = threading.Lock()

    def here() -> Path:
        return common.listener_dir(cfg)

    def study_of(ld: Path) -> Path:
        d = ld / "study"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def new_sid(study: Path) -> str:
        base = time.strftime("%Y%m%d-%H%M%S")
        sid, n = base, 2
        while any(study.glob(f"*-{sid}.jsonl")) or sid in open_:
            sid, n = f"{base}-{n}", n + 1
        return sid

    def write(s: dict, rec: dict) -> None:
        line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "s": round(time.monotonic() - s["t0"], 2), **rec}
        with _FILE_LOCK, open(s["path"], "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def get(sid: str) -> dict:
        """The open session, only for the tester it belongs to (the middleware scoped this request)."""
        s = open_.get(sid)
        if s is None or s["listener"] != here().resolve():
            raise HTTPException(409, "That session has ended.")
        s["last"] = time.monotonic()
        return s

    def log_usage(s: dict, live_id: str, seconds: float, backend: dict | None) -> float:
        from voice.gpt_live import session as live_session
        usd = live_session.cost(cfg, seconds, backend)
        log = Run(name="study", root=s["study"])
        with _FILE_LOCK:
            if any(x.get("live_id") == live_id and x.get("provider") == "openai" for x in log.read_log()):
                return usd  # reported twice (a beacon and the end)
            log.log({"stage": "voice", "provider": "openai", "kind": "interviewer", "mode": s["mode"],
                     "session": s["id"], "live_id": live_id, "seconds": round(seconds, 1), "backend": backend,
                     "cost_usd": usd})
        s["usage_logged"] = True
        return usd

    def mark_done(s: dict, how: str) -> None:
        q = covered(read_lines(s["path"]))
        write_json_atomic(state_path(s["study"], s["id"]), {
            "done": True, "finished_at": datetime.now().isoformat(timespec="seconds"), "topics": q["topics"],
            "closing": q["closing"], "farewell": q["farewell"], "session": s["id"], "how": how})

    def close(s: dict, how: str) -> dict:
        """End a session: its cost if the page never reported it (wall clock), the debrief's state, then the
        summary and the recording in the background."""
        s["closed_at"] = time.monotonic()
        if not s.get("usage_logged"):
            log_usage(s, s["live_id"], s["closed_at"] - s["t0"], None)
        write(s, {"who": "session", "ended": how})
        out: dict = {"ended": True}
        if s["mode"] == "debrief":
            q = covered(read_lines(s["path"]))
            out["topics"] = q["topics"]
            state_p = state_path(s["study"], s["id"])
            state = read_json(state_p)
            if not state.get("done"):
                # Delegation misses tools: a debrief whose farewell was said (or, the older shape, whose
                # closing question was asked and answered and that thanked them after) is finished whether
                # or not end_debrief came. The closing question is optional: a debrief without it is not
                # unfinished. One the tester said nothing in is never done, farewell and tool or not: it can
                # be taken again.
                if (s.get("ended_by_tool") or finished(q)) and q["said"]:
                    mark_done(s, "complete" if not s.get("ended_by_tool") else "end_debrief")
                else:
                    unanswered = not q["said"] and (q["farewell"] or s.get("ended_by_tool"))
                    write_json_atomic(state_p, {"done": False, "finished_at": None, "topics": q["topics"],
                                                "closing": q["closing"], "farewell": q["farewell"],
                                                "session": s["id"], "how": "no answer" if unanswered else how})
            out["done"] = bool(read_json(state_p).get("done"))
        t = threading.Thread(target=_after_end, args=(cfg, s["study"], s), name=f"interviewer-{s['id']}", daemon=True)
        BACKGROUND.append(t)
        t.start()
        return out

    def sweep() -> None:
        limit = float(icfg(cfg)["stale_minutes"]) * 60
        now = time.monotonic()
        with lock:
            stale = [open_.pop(sid) for sid, s in list(open_.items()) if now - s["last"] > limit]
        for s in stale:
            close(s, "stale")

    @app.get("/interview.js")
    def interview_js() -> Response:
        return Response(INTERVIEW_JS.read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})

    @app.post("/api/interview/start")
    def start(req: Start) -> dict:
        if req.mode not in MODES:
            raise HTTPException(400, "Unknown mode.")
        sweep()
        from voice.gpt_live import session as live_session
        ld = here()
        study = study_of(ld)
        try:
            values = intro_values(ld) if req.mode == "intro" else debrief_values(cfg, ld)
            sc = session_config(cfg, req.mode, values)
            got = live_session.create_webrtc(cfg, sc, req.sdp)
        except StageError as e:
            raise HTTPException(503, str(e)) from None
        except Exception as e:  # noqa: BLE001 (network)
            raise HTTPException(503, f"Voice isn't available: {e}") from None
        with lock:
            sid = new_sid(study)
            s = {"id": sid, "mode": req.mode, "live_id": got["id"], "t0": time.monotonic(), "last": time.monotonic(),
                 "listener": ld.resolve(), "study": study, "path": study / f"{req.mode}-{sid}.jsonl",
                 "values": values, "name": None, "tools": [], "who": identity(ld)}
            open_[sid] = s
        write(s, {"who": "session", "mode": req.mode, "live_id": got["id"], "voice": sc["audio"]["output"]["voice"],
                  "model": sc["model"], "backend": sc["delegation"]["responses"]["model"],
                  "instructions_sha256": hashlib.sha256(sc["instructions"].encode()).hexdigest()[:16],
                  "tools": [t["name"] for t in sc["delegation"]["responses"]["tools"]],
                  **({"moments": values["_moments"], "chapter": values["_context"],
                      "activity": values.get("_activity"), "did": values.get("did")} if req.mode == "debrief"
                     else {"name_given": values.get("_name")})})
        out = {"sid": sid, "live_id": got["id"], "sdp": got["sdp"], "notes": page_notes(req.mode, values),
               "settings": page_settings(cfg, req.mode)}
        if req.mode == "intro":  # what the page fetches into memory during the intro, ready for the handover
            I = icfg(cfg)
            out["next"] = {"scripts": list(I["scripts"]), "entry": I["entry"]}
        return out

    def name_from_transcript(s: dict, known: str | None = None) -> str | None:
        """The name, when set_listener_name never came (live check 2026-11-16: the voice delegated in none of
        four intros): the one the interviewer last confirmed, or the tester's answer to who they are, from the
        intro's transcript. `known` (the name given with the link) stays unless the transcript is clear."""
        return confirmed_name(read_lines(s["path"]), known=known)

    @app.post("/api/interview/tool")
    def tool(req: Tool) -> dict:
        s = get(req.sid)
        args = dict(req.args or {})
        lid = s["listener"].name
        if req.name not in MODE_TOOLS[s["mode"]]:
            out: dict = {"error": f"No tool named {req.name}."}
        elif req.name == "set_listener_name":
            name = " ".join(str(args.get("name") or "").split())[:NAME_MAX]
            if not name:
                out = {"saved": False, "reason": "No name was given."}
            else:
                try:
                    from demo import testers
                    testers.set_name(lid, name)
                    s["name"] = name
                    out = {"saved": True, "act": "name", "name": name}
                except Exception as e:  # noqa: BLE001 (the conversation goes on; the guide asks the name itself)
                    out = {"saved": False, "reason": f"{type(e).__name__}: {e}"}
        elif req.name == "handoff_to_guide":
            if s.get("handed_over"):
                out = {"handed_over": True, "again": True}
            else:
                out = {"handed_over": True}
                try:
                    from demo import testers
                    testers.update(lid, intro_done=datetime.now().isoformat(timespec="seconds"))
                except Exception as e:  # noqa: BLE001 (the handover happens anyway: the person is waiting)
                    out["not_saved"] = f"{type(e).__name__}: {e}"
                s["handed_over"] = True
            if out.get("handed_over") and not s["name"]:
                # No tool call saved a name in this session: a name they gave, or a correction of the one they
                # were greeted by, is taken from the transcript.
                known = (tester_record(s["listener"]).get("name") or "").strip() or None
                found = name_from_transcript(s, known)
                if found and found.lower() != (known or "").lower():
                    try:
                        from demo import testers
                        testers.set_name(lid, found)
                        s["name"] = found
                        out["name_from"] = "transcript"
                    except Exception as e:  # noqa: BLE001 (the guide asks the name itself)
                        out["name_not_saved"] = f"{type(e).__name__}: {e}"
            if out.get("handed_over"):
                name = s["name"] or (tester_record(s["listener"]).get("name") or None)
                I = icfg(cfg)
                out = {**out, "act": "handover", "name": name, "scripts": list(I["scripts"]), "entry": I["entry"]}
        else:  # end_debrief
            s["ended_by_tool"] = True
            if covered(read_lines(s["path"]))["said"]:  # never done with no word from the tester (`finished`); close() looks again
                mark_done(s, "end_debrief")
            out = {"ended": True, "act": "close"}
        s["tools"].append(req.name)
        write(s, {"who": "tool", "name": req.name, "input": args, "by": req.by,
                  "result": {k: v for k, v in out.items() if k not in PAGE_ONLY}})
        return out

    @app.post("/api/interview/line")
    def line(req: Line) -> dict:
        s = get(req.sid)
        who = WHO.get(req.who)
        if who and req.text.strip():
            write(s, {"who": who, "text": req.text.strip()})
        return {"ok": True}

    @app.post("/api/interview/usage")
    def usage(req: Usage) -> dict:
        with lock:
            s = open_.get(req.sid) or ended.get(req.sid)
        if s is None or s["listener"] != here().resolve():
            raise HTTPException(409, "That session has ended.")
        # GPT-Live bills connected seconds; the page's last usage figure lags, so the wall clock from the start
        # to now (or to the end) is used when it is longer (as overnight/live.py does).
        wall = (s.get("closed_at") or time.monotonic()) - s["t0"]
        return {"cost_usd": log_usage(s, req.live_id, max(req.seconds, wall), req.backend)}

    @app.post("/api/interview/end")
    def end(req: End) -> dict:
        with lock:
            s = open_.get(req.sid)
            if s is not None and s["listener"] == here().resolve():
                ended[req.sid] = open_.pop(req.sid)
            else:
                s = None
        if s is None:
            raise HTTPException(409, "That session has ended.")
        return close(s, req.how)
