"""overnight/planner.py: the planning pass (docs/overnight-chapters.md, "Planning the next chapter"; checks 3, 4)."""

import json
from datetime import datetime

import pytest
from overnight_plan_fakes import CHOOSE, install, listener_folder, offline, chapter  # noqa: F401
from fakes import all_text

from overnight import library, planner, profiles
from stages.common import StageError, load_prompt

TUESDAY_EVENING = datetime(2026, 11, 17, 18, 0)


@pytest.fixture
def world(tmp_path, cfg, monkeypatch):
    fake = install(monkeypatch)
    return listener_folder(tmp_path, cfg), fake


def card_of(d):
    return profiles.read_json(d / "up_next.json")


def test_surprise_reads_everything_and_writes_the_card(world, cfg):
    d, fake = world
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert up == card_of(d)
    assert up["for_morning"] == "2026-11-18" and up["status"] == "planned" and up["locked_at"] is None
    assert up["mode"] == "surprise" and up["decided"] == "continue"
    assert up["source"] == {"kind": "chapter", "ref": "2026-11-17-the-strike", "words": None}
    assert up["title"] == "The Ledger of Small Hours" and up["research_questions"] == ["What is known?", "Who studied it?"]
    assert up["request"] is None and up["chapter"] is None and up["plan_dir"] == f"plans/{up['id']}"
    pdir = d / up["plan_dir"]
    for f in ("log.jsonl", "inputs.md", "choice.json", "scout.md", "card.json"):
        assert (pdir / f).exists(), f
    assert fake.kinds() == ["open", "choose", "scout", "card"]  # the latest chapter's asides judged first
    # What the planner read: the last chapter, seeds, library, profile, every past chapter.
    seen = all_text(fake.of("choose")[0])
    for bit in ("Fish that see the shadow", "Patience is a hunting tool.", "Wait, why do they stand on one leg?",
                "Herons and fishermen", "more about the fish, please", "How do kingfishers see under water?",
                "The Peregrine", "Fishes on the river most mornings", "Why Herons Wait",
                "Why do herons wait so long? This follows one heron.", "Continuations in a row: 1",
                'It came from the listener saying: "why do they wait so long"'):
        assert bit in seen, bit
    assert seen.count("Why Herons Wait") >= 1 and "okay go on" not in seen  # not a question
    assert fake.of("choose")[0]["output_config"]["format"]["schema"]["properties"]["decided"]["enum"] == \
        ["continue", "branch"]
    scout = fake.of("scout")[0]
    assert scout["tools"] == [{"type": cfg["tools"]["web_search"], "name": "web_search", "max_uses": 4}]
    assert "https://example.org/herons" in (pdir / "scout.md").read_text()
    assert "Enough material" in all_text(fake.of("card")[0]) and "The Heron's Strike" in all_text(fake.of("card")[0])
    # Cost: every call logged and priced, the pass well under $0.50.
    log = [json.loads(x) for x in (pdir / "log.jsonl").read_text().splitlines()]
    assert [x["stage"] for x in log] == ["overnight_plan_choose", "overnight_plan_scout", "overnight_plan_card"]
    assert all(x["provider"] == "anthropic" and x["cost_usd"] > 0 for x in log)
    total = sum(x["cost_usd"] for x in log)
    assert profiles.read_json(pdir / "card.json")["cost_usd"] == pytest.approx(total, abs=1e-4) and total < 0.5


def test_continue_mode(world, cfg):
    d, fake = world
    profiles.save_settings(cfg, {"chapter": {"mode": "continue"}}, listener=d)
    fake.prefer = "branch"  # not offered
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert up["mode"] == "continue" and up["decided"] == "continue"
    assert fake.of("choose")[0]["output_config"]["format"]["schema"]["properties"]["decided"]["enum"] == ["continue"]


def test_your_topic_by_request(world, cfg):
    d, fake = world
    library.add_queue(d, "Old mills")  # a request for tonight wins; the queued topic waits
    req = {"mode": "topic", "topic": "why rivers meander", "notes": "no maths please", "session": "20261117-190000"}
    up = planner.plan(cfg, d, request=req, now=TUESDAY_EVENING)
    assert up["mode"] == "topic" and up["decided"] == "topic" and up["request"] == req
    assert up["source"] == {"kind": "request", "ref": "20261117-190000", "words": "why rivers meander"}
    assert up["notes"] == "no maths please"
    assert "why rivers meander" in all_text(fake.of("choose")[0]) and "no maths please" in all_text(fake.of("card")[0])
    assert [q["topic"] for q in library.queue(d)] == ["Old mills"]
    seed = planner.seed_text(up)
    assert "## What the listener said, in their own words\n\nwhy rivers meander" in seed
    assert up["summary"] in seed and "- What is known?" in seed and "no maths please" in seed
    assert seed.startswith("# Seed\n\n## Topic\n\nThe Ledger of Small Hours: ")


def test_surprise_me_by_request_without_a_topic(world, cfg):
    d, fake = world
    up = planner.plan(cfg, d, request={"mode": "topic", "topic": " ", "session": "x"}, now=TUESDAY_EVENING)
    assert up["mode"] == "surprise" and up["decided"] in ("continue", "branch")


def test_a_queued_topic_goes_ahead_of_the_mode_and_is_marked_used(world, cfg):
    d, fake = world
    q = library.add_queue(d, "How locks on a canal work", notes="the old ones", session="20261117-030000")
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert up["decided"] == "queue" and up["mode"] == "topic"
    assert up["source"] == {"kind": "queue", "ref": q["id"], "words": "How locks on a canal work"}
    assert up["notes"] == "the old ones" and up["request"] is None
    assert library.queue(d) == [] and library.queue(d, unused=False)[0]["id"] == q["id"]
    used = [r for r in profiles.read_jsonl(d / "queue.jsonl") if "used" in r]
    assert used == [{"used": q["id"], "by": up["id"], "t": used[0]["t"]}]
    up2 = planner.plan(cfg, d, now=datetime(2026, 11, 18, 18, 0))
    assert up2["decided"] != "queue" and up2["mode"] == "surprise" and up2["id"] != up["id"]


def test_after_three_continuations_surprise_branches(tmp_path, cfg, monkeypatch):
    fake = install(monkeypatch)
    d = listener_folder(tmp_path, cfg, chapters=False)
    for i, decided in enumerate(["branch", "continue", "continue", "continue"], start=10):
        chapter(d, f"2026-11-{i}-ch{i}", title=f"Chapter number {i}", summary=f"Summary {i}.", decided=decided)
    seed_id = library.seeds(d)[0]["id"]
    fake.source = {"kind": "seed", "ref": seed_id, "words": "words nobody said"}
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert fake.of("choose")[0]["output_config"]["format"]["schema"]["properties"]["decided"]["enum"] == ["branch"]
    assert up["decided"] == "branch" and "Continuations in a row: 3" in all_text(fake.of("choose")[0])
    # From a real seed: its own words replace words the listener never said, and it is marked used.
    assert up["source"] == {"kind": "seed", "ref": seed_id, "words": "how do kingfishers even see the fish"}
    assert seed_id not in [s["id"] for s in library.seeds(d)]


def test_two_continuations_or_a_migrated_chapter_leave_the_choice_open(tmp_path, cfg, monkeypatch):
    fake = install(monkeypatch)
    d = listener_folder(tmp_path, cfg, chapters=False)
    chapter(d, "2026-11-09-first", title="Can Beauty Make Us Good?", summary=None, decided=None)  # migrated: no card
    for i in (10, 11, 12):
        chapter(d, f"2026-11-{i}-ch{i}", title=f"Chapter number {i}", summary=f"Summary {i}.", decided="continue")
    chapter(d, "2026-11-13-ch13", title="Chapter thirteen", summary="S.", decided="branch")
    chapter(d, "2026-11-14-ch14", title="Chapter fourteen", summary="S.", decided="continue")
    planner.plan(cfg, d, now=TUESDAY_EVENING)
    seen = all_text(fake.of("choose")[0])
    assert "Continuations in a row: 1" in seen
    assert "Can Beauty Make Us Good? (2026-11-09-first): The first paragraph of the angle." in seen
    assert planner.continuations([{"decided": "continue"}, {"decided": None}, {"decided": "continue"}]) == 1


def test_a_repeated_title_gets_one_retry(world, cfg):
    d, fake = world
    fake.titles = ["why herons wait!", "Night Fishing on the River"]
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert up["title"] == "Night Fishing on the River" and fake.kinds() == ["open", "choose", "scout", "card", "card"]
    retry = all_text(fake.of("card")[1])
    assert 'titled "why herons wait!"' in retry and '"Why Herons Wait"' in retry
    assert profiles.read_json(d / up["plan_dir"] / "card.json")["titles_tried"] == ["why herons wait!",
                                                                                   "Night Fishing on the River"]


def test_a_title_that_repeats_twice_stops_the_pass_and_keeps_the_card(world, cfg):
    d, fake = world
    old = planner.plan(cfg, d, now=TUESDAY_EVENING)
    fake.titles = ["Why Herons Wait", "How Herons Wait"]
    fake.calls.clear()
    with pytest.raises(StageError, match="repeats the past chapter"):
        planner.plan(cfg, d, now=datetime(2026, 11, 17, 19, 0))
    assert card_of(d) == old and len(fake.of("card")) == 2


def test_first_run_with_nothing(tmp_path, cfg, monkeypatch):
    fake = install(monkeypatch)
    profiles.create("new-listener")
    d = profiles.folder("new-listener")
    fake.prefer = "continue"  # not offered: nothing to continue
    up = planner.plan(cfg, d, now=datetime(2026, 11, 20, 18, 30))  # a Friday evening
    assert up["for_morning"] == "2026-11-23" and up["decided"] == "branch" and up["mode"] == "surprise"
    assert up["source"] == {"kind": "profile", "ref": None, "words": None}  # the seed it named does not exist
    seen = all_text(fake.of("choose")[0])
    assert "(no chapter yet: this is the first)" in seen and "(none yet)" in seen
    assert fake.of("choose")[0]["output_config"]["format"]["schema"]["properties"]["decided"]["enum"] == ["branch"]


def test_a_request_after_the_cutoff_goes_to_the_queue(world, cfg):
    d, fake = world
    old = planner.plan(cfg, d, now=datetime(2026, 11, 17, 1, 0))  # the card for this morning
    fake.calls.clear()
    out = planner.plan(cfg, d, request={"mode": "topic", "topic": "x", "session": "s"},
                       now=datetime(2026, 11, 17, 2, 30))
    assert out["queued"] is True and "next build day" in out["note"] and out["id"] == old["id"]
    assert card_of(d) == old and fake.calls == []  # nothing paid, the card untouched
    assert [q["topic"] for q in library.queue(d)] == ["x"]


def test_a_change_whose_cutoff_comes_during_the_pass_is_queued_not_written(world, cfg, monkeypatch):
    """C2/C11/C28: Change at 01:59:30, the pass takes a minute; at 02:00:30 the old card is tonight's
    build. The card must not be replaced (a second chapter that night); the request is queued."""
    d, fake = world
    old = planner.plan(cfg, d, now=datetime(2026, 11, 16, 18, 0))  # Tuesday's card
    clock = iter([0.0, 60.0])
    monkeypatch.setattr(planner, "_monotonic", lambda: next(clock))
    req = {"mode": "topic", "topic": "Octopus eyes", "notes": "", "session": "s1"}
    out = planner.plan(cfg, d, request=req, now=datetime(2026, 11, 17, 1, 59, 30))
    assert out["queued"] is True and out["id"] == old["id"]
    assert card_of(d) == old
    assert [q["topic"] for q in library.queue(d)] == ["Octopus eyes"]
    assert profiles.read_json(sorted((d / "plans").iterdir())[-1] / "outcome.json")["queued"] is True


def test_a_change_queues_when_the_build_takes_the_card_during_the_pass(world, cfg, monkeypatch):
    """The runner (another process) moved the card to building while the pass ran."""
    d, fake = world
    old = planner.plan(cfg, d, now=datetime(2026, 11, 16, 18, 0))
    real = fake.__call__

    def build_starts(kwargs):
        if "Write the Up next card" in all_text(kwargs):
            profiles.write_json(d / "up_next.json", {**card_of(d), "status": "building", "chapter": "c",
                                                     "locked_at": "2026-11-17T02:00:01"})
            (d / "chapters" / "c").mkdir()
            profiles.write_json(d / "chapters" / "c" / "status.json", {"state": "making", "pid": __import__("os").getpid()})
        return real(kwargs)
    from fakes import FakeAnthropic
    from stages import llm
    monkeypatch.setattr(llm, "_client", FakeAnthropic(build_starts))
    out = planner.plan(cfg, d, request={"mode": "topic", "topic": "Tides", "session": "s2"},
                       now=datetime(2026, 11, 17, 1, 59))
    assert out["queued"] is True and card_of(d)["status"] == "building" and card_of(d)["id"] == old["id"]
    assert [q["topic"] for q in library.queue(d)] == ["Tides"]


def test_a_plain_pass_is_dropped_if_the_card_changed_meanwhile(world, cfg, monkeypatch):
    """C2 (c): the 18:00 pass and a Change at 18:00 both write; the listener's card must survive."""
    d, fake = world
    real = fake.__call__
    changed = {"id": "plan-by-change", "title": "Octopus Eyes", "status": "planned", "for_morning": "2026-11-18"}

    def change_lands(kwargs):
        if "Write the Up next card" in all_text(kwargs) and card_of(d) is None:
            profiles.write_json(d / "up_next.json", changed)
        return real(kwargs)
    from fakes import FakeAnthropic
    from stages import llm
    monkeypatch.setattr(llm, "_client", FakeAnthropic(change_lands))
    out = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert out == changed and card_of(d) == changed


def test_failed_passes_are_recorded_and_a_queued_topic_that_keeps_failing_is_set_aside(world, cfg):
    """C3/C23: a queued topic whose card title repeats a past chapter must not block every night."""
    d, fake = world
    q = library.add_queue(d, "Why herons wait")
    fake.titles = ["Why Herons Wait"]  # every card repeats the first chapter
    for hour in (18, 19):
        with pytest.raises(StageError, match="repeats the past chapter"):
            planner.plan(cfg, d, now=datetime(2026, 11, 17, hour, 0))
    fails = planner.failures(d)
    assert [f["queued"] for f in fails] == [q["id"], q["id"]] and fails[0]["t"] == "2026-11-17T18:00:00"
    assert fails[0]["reason"].startswith("the title 'Why Herons Wait' repeats the past chapter")
    assert library.queue(d) == []  # set aside after QUEUE_TRIES failures
    fake.titles = ["Something New Entirely"]
    fake.calls.clear()
    up = planner.plan(cfg, d, now=datetime(2026, 11, 17, 20, 0))
    assert up["decided"] != "queue" and up["mode"] == "surprise"
    with pytest.raises(StageError):  # a request's failure is the Change's to handle, not the tick's
        fake.titles = ["Why Herons Wait"]
        planner.plan(cfg, d, request={"mode": "topic", "topic": "herons", "session": "s"},
                     now=datetime(2026, 11, 17, 21, 0))
    assert len(planner.failures(d)) == 2


def test_each_pass_brings_the_latest_chapters_asides_in(world, cfg):
    """C25: the morning's asides come after the build's library step; the next pass adds their questions."""
    d, fake = world
    strike = d / "chapters" / "2026-11-17-the-strike"
    library.after_chapter(cfg, d, strike)  # what the build did at 05:00
    fake.calls.clear()
    profiles.append_jsonl(strike / "12_asks" / "asides.jsonl", {"id": "ask-2", "segment": 2, "transcript": [
        {"who": "listener", "text": "How fast is the strike compared to a cat?"}]})
    fake.open = [{"n": 1, "question": "How does a heron's strike compare with a cat's pounce?"}]
    planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert fake.kinds()[0] == "open"
    listed = all_text(fake.of("open")[0])
    assert "How fast is the strike compared to a cat?" in listed and "stand on one leg" not in listed
    assert "How does a heron's strike compare with a cat's pounce?" in [s["text"] for s in library.seeds(d)]
    assert "How does a heron's strike compare with a cat's pounce?" in all_text(fake.of("choose")[0])


def test_overnight_off_still_dates_the_card(world, cfg):
    d, fake = world
    profiles.save_settings(cfg, {"overnight": {"enabled": False}}, listener=d)
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert up["for_morning"] == "2026-11-18"


@pytest.mark.parametrize("title, past, clash", [
    ("Why Herons Wait", ["why herons wait"], "why herons wait"),
    ("How Herons Wait", ["Why Herons Wait"], "Why Herons Wait"),
    ("The Heron's Strike", ["The Herons Strike"], "The Herons Strike"),
    ("What the Heron Is Doing While It Waits", ["Why Herons Wait"], None),
    ("Kingfishers Under Water", ["Why Herons Wait", "The Heron's Strike"], None),
])
def test_near_duplicate(title, past, clash):
    assert planner.near_duplicate(title, past) == clash


def test_every_plan_prompt_loads_with_its_inputs():
    load_prompt("overnight_plan_card_retry", title="a", clash="b")
    assert CHOOSE in load_prompt("overnight_plan_choose", inputs="x", mode="surprise", allowed="branch", streak=3,
                                 minutes=20)


def test_a_queued_surprise_me_sets_the_mode_and_is_used(world, cfg):
    d, fake = world
    profiles.save_settings(cfg, {"chapter": {"mode": "topic"}}, listener=d)
    q = library.queue_add(d, {"mode": "continue", "topic": "", "notes": "more on the fish", "session": "s9"})
    assert q["mode"] == "continue" and q["session"] == "s9"
    up = planner.plan(cfg, d, now=TUESDAY_EVENING)
    assert up["mode"] == "continue" and up["decided"] == "continue" and up["notes"] == "more on the fish"
    assert "Mode: continue" in all_text(fake.of("choose")[0]) and library.queue(d) == []


def test_a_summary_past_four_sentences_gets_one_retry(tmp_path, cfg, monkeypatch):
    """Live, the card's summary ran to five sentences once in three; the spec says three or four."""
    import overnight_plan_fakes as P
    from overnight import planner
    fake = P.install(monkeypatch)
    listener = P.listener_folder(tmp_path, cfg)
    real = fake.__call__
    long = "One thing. Two things. Three things. Four things. Five things."
    seen = []

    def call(kwargs):
        out = real(kwargs)
        if fake.calls and fake.calls[-1][0] == "card":
            n = len(fake.of("card"))
            seen.append(n)
            if n == 1:
                body = json.loads(out.content[0].text)
                body["summary"] = long
                out.content[0].text = json.dumps(body)
        return out
    from fakes import FakeAnthropic
    from stages import llm
    monkeypatch.setattr(llm, "_client", FakeAnthropic(call))
    card = planner.plan(cfg, listener, now=datetime(2026, 11, 16, 18, 5))
    assert seen == [1, 2] and planner.sentences(card["summary"]) <= 4
    assert "had 5 sentences" in P.all_text(fake.of("card")[1])
