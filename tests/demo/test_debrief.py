"""demo/debrief.py: a tester's moments and their chapter, as the debrief is told them, from a live chapter's files."""

import json
import os
import time

from demo import debrief

TEXT1 = ("Picture a woman in a meeting. [D01] Her name is Donella Meadows. [D01, D14]\n\n"
         "The meeting is about trade. It angers her. So she gets up.")
TEXT2 = "Years later engineers still argue about her list. [D02]\n\nSome call it obvious."


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def live_run(chapters, name, *, voice="live", age=0, locked=(1, 2), asides=(), player=None, card=None):
    root = chapters / name
    write(root / "fast.json", {"mode": "fast", "voice": voice})
    if age:
        t = time.time() - age
        os.utime(root / "fast.json", (t, t))
    write(root / "05_outline.json", {"title": "The List", "angle": "Why her list of levers still matters.",
                                     "sections": [{"n": 1, "title": "One", "target_words": 750},
                                                  {"n": 2, "title": "Two", "target_words": 750}]})
    for n in locked:
        write(root / "06_segments" / f"seg{n:02d}.md", {1: TEXT1, 2: TEXT2}[n])
    if asides:
        write(root / "12_asks" / "asides.jsonl", "".join(json.dumps(a) + "\n" for a in asides))
    if player is not None:
        write(root / "fast" / "player.json", player)
    if card is not None:
        write(root / "fast" / "card.json", card)
    return root


def ask(pid, sentence, *said, kind="ask"):
    lines = []
    for i, text in enumerate(said):
        lines.append({"who": "listener", "text": text})
        lines.append({"who": "guide", "text": f"Answer {i}."})
    return {"id": f"a-{pid}-{sentence}", "kind": kind, "voice": "live", "paragraph": pid, "sentence": sentence,
            "transcript": lines}


def test_moments_are_their_questions_at_about_where_the_reading_was(tmp_path):
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "older-run", age=3600, asides=[ask("s1-p01", 0, "Old question?")])
    live_run(lis / "chapters", "not-live", voice=None, asides=[ask("s1-p01", 0, "Not live?")])
    live_run(lis / "chapters", "the-run", asides=[
        ask("s2-p01", 0, "Which engineers?"),
        ask("s1-p02", 1, "Why did trade anger her?", "And then?"),
        {"kind": "check_in", "paragraph": "s2-p01", "sentence": 0, "transcript": [{"who": "listener", "text": "ok"}]},
        ask("s1-p01", 0),  # a tap where they said nothing
    ])
    got = debrief.moments(lis)
    # s1-p01 has 11 words; the first sentence of s1-p02 has 5 more: 16 words at 2.5 a second.
    assert got[0] == {"at_s": 6.4, "clock": "0:06", "words": "Why did trade anger her?"}
    p1 = len("Picture a woman in a meeting. Her name is Donella Meadows.".split())
    p2 = len("The meeting is about trade. It angers her. So she gets up.".split())
    assert got[1] == {"at_s": round((p1 + p2) / 2.5, 1), "clock": debrief.clock((p1 + p2) / 2.5), "words": "Which engineers?"}
    assert len(got) == 2


def test_a_moment_in_a_paragraph_not_found_has_no_time(tmp_path):
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "r", locked=(1,), asides=[ask("s9-p01", 0, "Huh?")])
    assert debrief.moments(lis) == [{"at_s": None, "clock": None, "words": "Huh?"}]


def test_no_chapter_no_moments(tmp_path):
    assert debrief.moments(tmp_path) == []
    assert debrief.chapter_context(tmp_path) == {"run": None, "title": None, "summary": None, "minutes_heard": 0,
                                                 "minutes": None}


def test_chapter_context_heard_part_way(tmp_path):
    lis = tmp_path / "t-sam"
    live_run(lis / "chapters", "r", player={"state": "playing", "chapter_t": 9.0},
             card={"title": "A card title", "overview": "o", "summary": "The card's summary."})
    got = debrief.chapter_context(lis)
    words = len(" ".join([TEXT1, TEXT2]).split()) - 4  # the four [D..] markers' words come out
    assert got["run"] == "r" and got["title"] == "The List" and got["summary"] == "The card's summary."
    assert got["minutes"] == round(words / 2.5 / 60, 1) and got["minutes_heard"] == 0.1


def test_chapter_context_heard_to_the_end_and_unfinished_chapters(cfg, tmp_path):
    lis = tmp_path / "t-sam"
    root = live_run(lis / "chapters", "r", locked=(1,), player={"state": "done", "chapter_t": 3.0})
    got = debrief.chapter_context(lis, cfg)
    assert got["minutes"] == round(1500 / 2.5 / 60, 1) == got["minutes_heard"]  # the outline's target words
    assert got["summary"] == "Why her list of levers still matters."  # no card: the outline's angle
    (root / "05_outline.json").write_text(json.dumps({"title": "T", "sections": [{"n": 1}, {"n": 2}]}))
    cfg.setdefault("demo", {})["chapter_minutes"] = 10
    assert debrief.chapter_context(lis, cfg)["minutes"] == 10
