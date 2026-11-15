"""overnight/library.py (seeds, library, queue; what a finished chapter adds), the memory pass's library and
seeds, and overnight/intro.py (the intro brief)."""

import json
import re

import pytest
from overnight_plan_fakes import HERON_SCRIPT, chapter, install, listener_folder, offline  # noqa: F401
from fakes import all_text

from overnight import intro, library, profiles
from stages import memory
from stages.common import Run, StageError


@pytest.fixture
def world(tmp_path, cfg, monkeypatch):
    fake = install(monkeypatch)
    return listener_folder(tmp_path, cfg), fake


# --- seeds, library, queue ----------------------------------------------------------

def test_add_dedupes_by_id_and_mark_used_appends(tmp_path):
    d = tmp_path / "l"
    a = library.add_seeds(d, [{"kind": "question", "text": "Why is the sky blue?", "words": "why's it blue"},
                              {"kind": "question", "text": "why is the sky blue"},  # the same, by its hash
                              {"kind": "nonsense", "text": "x"}, {"kind": "idea", "text": " "}],
                          source={"session": "s1"})
    assert len(a) == 1 and a[0]["id"].startswith("s-") and a[0]["source"] == {"session": "s1"}
    assert library.add_seeds(d, [{"kind": "question", "text": "Why is the sky BLUE?!"}]) == []
    b = library.add_seeds(d, [{"kind": "idea", "text": "Clouds"}])[0]
    library.mark_used(d, [a[0]["id"]], "plan-1")
    assert [s["id"] for s in library.seeds(d)] == [b["id"]]
    assert [s["id"] for s in library.seeds(d, unused=False)] == [a[0]["id"], b["id"]]
    lines = (d / "seeds.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[-1])["used"] == a[0]["id"]  # append-only
    w = library.add_works(d, [{"kind": "work", "title": "The Peregrine", "creator": "J. A. Baker"},
                              {"kind": "work", "title": "the peregrine", "creator": "J A Baker"},
                              {"kind": "person", "title": "J. A. Baker"}])
    assert len(w) == 2 and w[0]["id"].startswith("w-") and len(library.works(d)) == 2
    q1, q2 = library.add_queue(d, "Canals"), library.add_queue(d, "Mills")
    assert q1["id"].startswith("q-") and q1["id"] != q2["id"]
    library.mark_used(d, [q1["id"]], "plan-2", library.QUEUE)
    assert [q["topic"] for q in library.queue(d)] == ["Mills"]


def test_after_chapter_adds_parked_saved_questions_references_and_the_topic(world, cfg):
    d, fake = world
    root = d / "chapters" / "2026-11-17-the-strike"
    before = {s["id"] for s in library.seeds(d)}
    got = library.after_chapter(cfg, d, root)
    new = [s for s in library.seeds(d) if s["id"] not in before]
    by_kind = {k: [s for s in new if s["kind"] == k] for k in ("parked", "idea", "question")}
    assert [s["text"] for s in by_kind["parked"]] == ["Fish that see the shadow"]
    assert by_kind["parked"][0]["source"] == {"chapter": "2026-11-17-the-strike"}
    idea = by_kind["idea"][0]
    assert idea["text"] == "Herons and fishermen (he fishes)"
    assert idea["words"] == "I fish there too, I'd love to know what herons and anglers share"  # before the save
    q = by_kind["question"][0]
    assert q["text"] == "Why do herons stand on one leg?" and q["words"] == "Wait, why do they stand on one leg?"
    assert q["source"] == {"chapter": "2026-11-17-the-strike", "aside": "ask-1"}
    # The one call: only real questions, with the script, logged to the chapter.
    call = all_text(fake.of("open")[0])
    assert "1. (asked during section 1) Wait, why do they stand on one leg?" in call and "okay go on" not in call
    assert "faster than the fish can turn" in call and "[D02]" not in call
    log = Run(name=root.name, root=root).read_log()
    assert log[0]["stage"] == "overnight_library" and log[0]["cost_usd"] > 0
    lib = {(w["kind"], w["title"]) for w in library.works(d)}
    assert {("topic", "The Heron's Strike"), ("work", "The Heron"), ("person", "Konrad Lorenz")} <= lib
    assert ("source", "A field study") not in lib and not any(t == "A field study" for _, t in lib)
    assert got == {"seeds": 3, "library": 3}
    # Again: nothing new, and the questions are not judged twice.
    assert library.after_chapter(cfg, d, root) == {"seeds": 0, "library": 0}
    assert len(fake.of("open")) == 1 and (root / library.ADDED).exists()


def test_after_chapter_without_asides_makes_no_call_and_keeps_questions_if_the_call_fails(world, cfg, monkeypatch):
    d, fake = world
    first = d / "chapters" / "2026-11-16-why-herons-wait"
    assert library.after_chapter(cfg, d, first)["seeds"] == 2  # two parked ideas, a str and a dict
    assert fake.of("open") == []
    root = d / "chapters" / "2026-11-17-the-strike"
    monkeypatch.setattr(library, "open_questions", lambda *a: (_ for _ in ()).throw(StageError("down")))
    library.after_chapter(cfg, d, root)
    assert "Wait, why do they stand on one leg?" in [s["text"] for s in library.seeds(d) if s["kind"] == "question"]


def test_after_chapter_judges_questions_asked_since_its_last_run(world, cfg):
    """C25: the build's run (05:00) sees no asides; the morning's are judged on the next run, and only they."""
    d, fake = world
    root = d / "chapters" / "2026-11-17-the-strike"
    library.after_chapter(cfg, d, root)
    assert len(fake.of("open")) == 1
    profiles.append_jsonl(root / "12_asks" / "asides.jsonl", {"id": "ask-2", "segment": 2, "transcript": [
        {"who": "listener", "text": "Do herons ever miss the fish?"}]})
    fake.open = [{"n": 1, "question": "How often does a heron's strike miss?"}]
    got = library.after_chapter(cfg, d, root)
    assert len(fake.of("open")) == 2 and got["seeds"] == 1
    call = all_text(fake.of("open")[1])
    assert "1. (asked during section 2) Do herons ever miss the fish?" in call and "one leg" not in call
    added = json.loads((root / library.ADDED).read_text())
    assert [q["question"] for q in added["open_questions"]] == ["Why do herons stand on one leg?",
                                                               "How often does a heron's strike miss?"]
    assert library.after_chapter(cfg, d, root) == {"seeds": 0, "library": 0} and len(fake.of("open")) == 2


def test_the_chapter_that_answered_every_question_adds_none(world, cfg):
    d, fake = world
    fake.open = []
    library.after_chapter(cfg, d, d / "chapters" / "2026-11-17-the-strike")
    assert not [s for s in library.seeds(d) if s["kind"] == "question" and "leg" in s["text"]]


def test_is_question():
    assert library.is_question("Wait, why do they stand on one leg?")
    assert library.is_question("how long can a heron stand still")
    assert not library.is_question("okay go on") and not library.is_question("why?")


# --- the memory pass ----------------------------------------------------------------

def test_the_memory_pass_adds_library_and_seeds(world, cfg):
    d, fake = world
    cfg["listener_dir"] = str(d)
    (d / "sessions").mkdir(exist_ok=True)
    t = d / "sessions" / "20261117-200000.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in [
        {"who": "guide", "text": "Hi."},
        {"who": "listener", "text": "I just finished H is for Hawk, and honestly how do you even train a hawk?"}]) + "\n")
    assert memory.after_session(cfg, t) is True
    call = all_text(fake.of("memory")[0])
    assert "- (work) The Peregrine" in call and "- (seed, question) How do kingfishers see under water?" in call
    schema = fake.of("memory")[0]["output_config"]["format"]["schema"]
    assert {"library", "seeds", "profile", "guide", "forget_chapters", "forgot"} == set(schema["required"])
    work = [w for w in library.works(d) if w["title"] == "H is for Hawk"][0]
    assert work["creator"] == "Helen Macdonald" and work["source"] == {"session": "20261117-200000"}
    seeds = {s["text"]: s for s in library.seeds(d)}
    assert seeds["How do falconers train a hawk?"]["words"] == "how do you even train a hawk"
    assert seeds["Tides"]["words"] is None  # words the listener never said are dropped
    assert (d / "profile.md").read_text().startswith("# Profile")
    memory.after_session(cfg, t)  # once per session
    assert len(fake.of("memory")) == 1


def test_a_memory_answer_without_library_or_seeds_still_works(world, cfg):
    d, fake = world
    cfg["listener_dir"] = str(d)
    fake.memory = {}
    (d / "sessions").mkdir(exist_ok=True)
    t = d / "sessions" / "20261117-210000.jsonl"
    t.write_text(json.dumps({"who": "listener", "text": "hello"}) + "\n")
    n = len(library.works(d))
    assert memory.after_session(cfg, t) is True and len(library.works(d)) == n


# --- the intro brief ----------------------------------------------------------------

DOSSIER = ("# Dossier\n\n## Entries\n\n"
           "[D01] (L01, claim, tier 2) A grey heron stands in the shallows at dawn and does not move for a very long "
           "time, and the fish beneath it never notice. Source: Example, https://example.org/a\n"
           "[D02] (L01, claim, tier 2) Grey herons can wait motionless for over an hour. Source: https://example.org/b\n"
           "[D03] (L02, claim, tier 2) A heron's neck vertebra works like a spring. Source: https://example.org/c\n")


@pytest.fixture
def built(world):
    d, fake = world
    root = d / "chapters" / "2026-11-18-night"
    chapter(d, "2026-11-18-night", title="The Night Shift", decided="continue", script=HERON_SCRIPT,
            summary="Why do herons fish at night? This chapter follows one night. You asked why they wait so long.",
            outline={"through_line": "Patience is a tool.", "closing_idea": "Night is a second dawn.",
                     "sections": [{"n": 1, "title": "Dusk", "lands": "herons hunt by moonlight"},
                                  {"n": 2, "title": "Strike", "lands": "the strike is faster at night"}]},
            words="why do they wait so long")
    (root / "04_dossier.md").write_text(DOSSIER)
    r = Run(name=root.name, root=root, unattended=True, max_usd=50)
    return r, fake


def test_intro_brief_headings_findings_and_their_words(built, cfg):
    r, fake = built
    p = intro.run(r, cfg)
    text = p.read_text()
    heads = re.findall(r"^## .+$", text, re.M)
    assert heads == ["## Title", "## Summary", "## Findings you may mention", "## Why this one, for them"]
    assert "The Night Shift" in text and "Why do herons fish at night? This chapter follows one night." in text
    assert "- Grey herons can wait motionless for over an hour." in text
    assert 'Their words: "why do they wait so long"' in text
    call = all_text(fake.of("intro")[0])
    assert "lands herons hunt by moonlight" in call and "[D03]" in call and "Night is a second dawn." in call
    assert "faster than the fish can turn" not in call  # the model never sees the script
    assert intro.run(r, cfg) == p and len(fake.of("intro")) == 1  # skipped once it exists


def no_script_runs(text: str, script: str, allowed: str) -> list:
    """Every 12-word run of the brief that is in the script, beyond what the card says."""
    return sorted((intro.runs(text) & intro.runs(script)) - intro.runs(allowed))


def test_intro_brief_never_carries_the_script(built, cfg):
    r, fake = built
    fake.findings = [
        {"text": "A grey heron stands in the shallows at dawn and does not move for a very long time, "
                 "and the fish beneath it never notice.", "entry": "D01"},  # the script's own line
        {"text": "Grey herons can wait motionless for over an hour.", "entry": "D02"},
        {"text": "Something with no entry behind it.", "entry": "D99"}]
    fake.why = ("You asked why they wait so long. The strike itself takes less than a tenth of a second, faster "
                "than the fish can turn away.")
    text = intro.run(r, cfg).read_text()
    script = (r.root / "09_script_final.md").read_text()
    card = profiles.read_json(r.root / "00_source" / "up_next.json")
    assert no_script_runs(text, script, card["title"] + " " + card["summary"] + " " + card["source"]["words"]) == []
    assert "does not move for a very long time" not in text and "Something with no entry" not in text
    assert "You asked why they wait so long." in text and "tenth of a second" not in text
    left = (r.root / "decisions.md").read_text()
    assert "Left out of the intro brief" in left and "shares a line with the script" in left and "'D99'" in left


def test_intro_brief_stops_if_no_finding_survives(built, cfg):
    r, fake = built
    fake.findings = [{"text": "Unbacked.", "entry": "none"}]
    with pytest.raises(StageError, match="no finding passed"):
        intro.run(r, cfg)
    assert not (r.root / intro.OUT).exists()


def test_next_notes_are_append_only_and_marked_used(tmp_path):
    d = tmp_path / "l"
    a = library.add_next_note(d, " Something on tides ", "typed")
    b = library.add_next_note(d, "short please", "voice")
    assert a["id"] != b["id"] and a["id"].startswith("n-") and a["text"] == "Something on tides"
    assert [n["text"] for n in library.next_notes(d)] == ["Something on tides", "short please"]
    library.mark_used(d, [a["id"]], "plan-1", library.NEXT_NOTES)
    assert [n["id"] for n in library.next_notes(d)] == [b["id"]]
    assert len(library.next_notes(d, unused=False)) == 2 and len((d / "next_notes.jsonl").read_text().splitlines()) == 3
