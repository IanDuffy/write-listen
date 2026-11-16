"""wl demo seed (demo/seed.py, demo/library_demo.json; docs/library-timeline.md "Demo content", check 1)."""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from demo import seed as demo
from overnight import commands, library, profiles, timeline
from stages.common import StageError

SEED = json.loads(demo.SEED.read_text(encoding="utf-8"))


def fake_source(tmp_path) -> Path:
    """A listener holding the two real chapters the demo copies (tiny stand-ins for their files)."""
    src = tmp_path / "src-listener"
    for ch in SEED["chapters"]:
        if ch.get("copy_from"):
            root = src / "chapters" / ch["copy_from"]
            for f in demo.COPY:
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_text(f"{ch['copy_from']}/{f}")
            dur = ch["playback"]["duration_s"]
            (root / "11_episode" / "episode.json").write_text(json.dumps({"title": "Real title", "duration": dur}))
    return src


def tree_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(d)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def test_seed_twice_is_identical_and_touches_no_other_listener(tmp_path, own_listeners):
    src = fake_source(tmp_path)
    ian = profiles.create("ian")
    (ian / "profile.md").write_text("Ian's own.")
    before = tree_hash(ian)
    d = demo.seed_listener(src)
    first = tree_hash(d)
    (d / "chapters" / "stray.txt").write_text("left over")  # rebuilt from scratch, not merged
    demo.seed_listener(src)
    assert tree_hash(d) == first and tree_hash(ian) == before
    assert d == own_listeners / "demo-library" and sorted(p.name for p in own_listeners.iterdir()) == ["demo-library", "ian"]
    herons = d / "chapters" / "2026-11-14-herons-hunt-standing-still"
    assert (herons / "10_audio" / "chapter.mp3").read_text().endswith("10_audio/chapter.mp3")
    assert not (d / "chapters" / "2026-11-03-same-heron-every-morning" / "10_audio").exists()  # filler: no audio


def test_a_missing_real_chapter_stops_before_anything_changes(tmp_path, own_listeners):
    src = fake_source(tmp_path)
    d = demo.seed_listener(src)
    first = tree_hash(d)
    (src / "chapters" / "2026-11-14-herons-hunt-standing-still" / "10_audio" / "chapter.mp3").unlink()
    with pytest.raises(StageError, match="give the listener that has it"):
        demo.seed_listener(src)
    assert tree_hash(d) == first and not (own_listeners / ".demo-library.tmp").exists()


def test_the_seed_only_ever_replaces_the_demo_folder(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**SEED, "listener": "ian"}))
    with pytest.raises(StageError, match="only demo-library"):
        demo.seed_listener(fake_source(tmp_path), bad)


def test_the_demo_reads_as_the_sketches_say(tmp_path, own_listeners):
    d = demo.seed_listener(fake_source(tmp_path))
    card = profiles.read_json(d / "up_next.json")
    latest = profiles.latest_chapter(listener=d)
    st = timeline.state(d, card, "planned", latest.name, "Wren", today=date(2026, 11, 16))
    chs = st["chapters"]
    assert 8 <= len(chs) <= 10 and latest.name == "2026-11-14-herons-hunt-standing-still"
    assert [c["playable"] for c in chs].count(True) == 1 and chs[-1]["playable"]
    by = {c["title"]: c for c in chs}
    assert by["What Beauty Asks of Us"]["status_line"] == "Finished"
    herons = by["Herons: Hunting by Standing Still"]
    assert herons["status_line"] == "Finished" and herons["counts"] == "2 questions · 1 mention"
    words = [m["text"] for m in herons["moments"]]
    assert words == ["Why do they stand on one leg?", "Do gardens make us notice like this?", "Upstream, Mary Oliver"]
    assert [m["outcome_label"] for m in herons["moments"]] == ["Answered", "Became tonight's chapter", "In your library"]
    assert card["title"] == "Borrowed Scenery: Why Japanese Gardens Hide the View"
    assert st["first"] == {"date": "2026-10-24", "title": "the heron by the pond"}
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days
            for a, b in zip([st["first"]["date"]] + [c["date"] for c in chs], [c["date"] for c in chs])]
    assert max(gaps) > 7  # check 5 needs a gap the scrubber squeezes
    assert {c["status"] for c in chs} >= {"finished", "partial"}
    assert all(1 <= len(c["moments"]) <= 3 for c in chs)


def test_the_seed_file_hangs_together():
    """Every moment inside its chapter and heard; every saved or became moment's seed in seeds.jsonl; every
    became_chapter pointing at a card; every mention in the library."""
    cards = {c["card"]["id"] for c in SEED["chapters"]} | {SEED["up_next"]["id"]}
    seed_ids = {s["id"] for s in SEED["seeds"] if "id" in s}
    works = {w["id"]: w for w in SEED["library"]}
    for ch in SEED["chapters"]:
        pb = ch["playback"]
        assert ch["card"]["chapter"] == ch["folder"] and ch["card"]["for_morning"] == ch["folder"][:10]
        for m in ch["moments"]:
            assert 0 < m["position_s"] <= pb["furthest_s"] <= pb["duration_s"] + 0.1, (ch["folder"], m)
            if m["outcome"] in ("saved", "became_chapter"):
                assert m["seed"] in seed_ids
            if m["outcome"] == "became_chapter":
                assert m["outcome_ref"] in cards and m["outcome_ref"] != ch["card"]["id"]
            if m["kind"] == "mention":
                w = works[m["outcome_ref"]]
                assert (w["title"], w["creator"]) == (m["title"], m["creator"])
                assert m["outcome_ref"] == library.work_id("work", m["title"], m["creator"])
        if ch.get("copy_from"):
            assert all(m["sentence_id"] and m["paragraph_id"] for m in ch["moments"])
    assert SEED["up_next"]["source"]["ref"] in seed_ids
    assert {"Upstream", "On Beauty and Being Just"} <= {w["title"] for w in SEED["library"] if w["kind"] == "work"}


def test_the_scheduler_leaves_the_demo_alone(cfg, tmp_path, own_listeners):
    demo.seed_listener(fake_source(tmp_path))
    out = commands.tick(cfg, "demo-library")
    assert out == {"did": None, "reason": "a demo listener"}
