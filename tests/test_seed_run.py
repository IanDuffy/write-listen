"""Phase 1, milestone 1: a seed -> a finished chapter with no checkpoints (`new --seed ... --unattended`)."""

import builtins
import json
import re

import pytest
from fakes import FakeAnthropic, Responder, all_text

import cli
from stages import common, ledger, llm, new, research, seed
from stages.common import PROMPTS_DIR, Run, StageError, load_prompt

TOPIC = "I can't stop thinking about why Roman concrete lasted"


@pytest.fixture
def env(tmp_path, cfg, monkeypatch, fake_tts):
    """The CLI with fake APIs, runs under tmp_path, and a listener profile."""
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(common, "RUNS_DIR", runs)
    monkeypatch.setattr(seed, "RUNS_DIR", runs)
    listener = tmp_path / "listener"
    listener.mkdir()
    (listener / "profile.md").write_text("- Pours concrete for a living.\n")
    cfg["listener_dir"] = str(listener)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail(f"an unattended run asked: {prompt}"))
    resp = Responder()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    return runs, resp, fake_tts


def calls_with(resp, phrase):
    return [c for c in resp.calls if phrase in all_text(c)]


def test_seed_to_chapter_unattended(env):
    runs, resp, tts = env
    assert cli.main(["new", "--seed", TOPIC, "--why", "I pour concrete for a living", "--unattended"]) == 0
    (root,) = runs.iterdir()
    assert re.fullmatch(r"roman-concrete-lasted-\d{8}", root.name)
    r = Run.open(root.name, runs_dir=runs)
    assert "I pour concrete for a living" in r.read("seed")

    # The scout maps the territory with its own budget, and its map is what later stages read.
    (scout,) = calls_with(resp, "Before anyone chooses an angle, scout the territory")
    assert [t["max_uses"] for t in scout["tools"]] == [10, 5]
    assert "Pours concrete for a living." in all_text(scout)  # the listener's profile
    assert "Self-healing lime clasts" in r.read("map")
    assert json.loads(r.path("scout_sources").read_text())["searches"] == 4

    # Every stage used its seed variant, sized as a 20-minute lesson.
    angles = calls_with(resp, "candidate angles")[-1]
    assert "about 3000 words" in all_text(angles) and "What the listener said" in all_text(angles)
    (led_call,) = calls_with(resp, "Before any research, we decide what the")
    assert "A 20-minute lesson lands 3 or 4 ideas well" in all_text(led_call)
    assert "<transcript>" not in all_text(led_call)
    row_call = calls_with(resp, "Your idea is row L01")[0]
    assert "A scout's map of the territory" in all_text(row_call)
    brief_call = calls_with(resp, "Write the brief")[0]
    assert "tying back to what the listener said" in all_text(brief_call)
    assert "in 4 to 4 segments" in all_text(brief_call)
    led = ledger.load(r, None)
    assert led["source"] == "seed" and "Scout has:" in r.path("ledger_md").read_text()
    for c in resp.calls:  # no seed-run prompt talks about a podcast or its transcript
        assert "podcast" not in all_text(c).lower(), all_text(c)[:200]

    # Finished, with no questions asked, and every decision written down for Ian.
    assert r.path("chapter_mp3").stat().st_size > 1000 and r.path("report").exists()
    decisions = r.read("decisions")
    for heading in ("## start", "## angle", "## ledger", "## ledger update", "## brief", "## done"):
        assert heading in decisions, heading
    assert 'Chose "The ladder and the self"' in decisions and "Approved the ledger without review" in decisions
    log = r.read_log()
    assert {"scout", "angles", "choose", "ledger", "research_row", "ledger_update", "brief", "draft",
            "coverage", "edit", "revise", "narrate"} <= {x["stage"] for x in log}

    # status.json, for the page: ready, every stage done, with each stage's real duration.
    st = json.loads(r.path("status").read_text())
    assert st["state"] == "ready" and st["stages_done"] == st["stages_total"] == len(new.STEPS)
    assert st["audio"] == "10_audio/chapter.mp3" and st["title"] == "Beauty" and st["minutes_left"] == 0
    assert [h["stage"] for h in st["history"]] == [s[0] for s in new.STEPS]

    # The timed transcript, from the timings narration kept: the chapter can be read along and asked about.
    timing = json.loads(r.path("timing").read_text())
    assert timing["aligned_by"] == "elevenlabs" and timing["stats"]["estimated"] == 0
    assert json.loads(r.path("episode").read_text())["references"] == []
    assert "can be read along and asked about" in decisions
    chunk = json.loads(next((r.path("audio_dir") / "chunks").glob("*.json")).read_text())
    assert chunk["alignment"]["characters"] == list(chunk["text"])
    from ask.routes import with_extras
    assert with_extras(st, runs)["asks"] is True

    # listener/history.md: what the guide has made for this listener.
    assert "## Beauty (" in (runs.parent / "listener" / "history.md").read_text()

    # Running it again does nothing more.
    n = len(resp.calls)
    assert cli.main(["new", root.name, "--unattended"]) == 0
    assert len(resp.calls) == n


def test_forecast_over_the_cap_stops_before_spending(env):
    runs, resp, _ = env
    assert cli.main(["new", "lesson", "--seed", TOPIC, "--unattended", "--max-usd", "1"]) == 1
    r = Run.open("lesson", runs_dir=runs)
    assert not resp.calls and r.read_log() == []
    assert json.loads(r.path("status").read_text())["state"] == "problem"
    assert "## stopped" in r.read("decisions") and "Not started" in r.read("decisions")


def test_a_run_stopped_by_its_cap_continues_where_it_stopped(env, monkeypatch):
    runs, resp, _ = env
    monkeypatch.setattr(new, "remaining_forecast", lambda r, cfg: 0.0)  # let it start, then hit the cap
    # A $1.50 cap fits the scout's guard (about $0.85 on Sonnet) but not research's (about $1.90): it stops there.
    assert cli.main(["new", "lesson", "--seed", TOPIC, "--unattended", "--max-usd", "1.5"]) == 1
    r = Run.open("lesson", runs_dir=runs)
    assert r.path("map").exists() and r.path("ledger").exists() and not r.path("dossier").exists()
    assert "Stopped at research" in r.read("decisions")
    st = json.loads(r.path("status").read_text())
    assert st["state"] == "problem" and st["stage"] == "research"
    assert st["message"] == "The chapter stopped while researching: continuing would pass its spending limit."
    assert st["retryable"] is False and st["pid"]
    scouts = resp.scout_calls
    assert cli.main(["new", "lesson", "--unattended", "--max-usd", "50"]) == 0
    assert resp.scout_calls == scouts and r.path("chapter_mp3").exists()  # nothing paid for twice


def test_unattended_follow_ups_that_do_not_fit_are_left_out(env, cfg):
    runs, resp, _ = env
    cfg["research"]["budgets"]["followup"]["searches"] = 40  # no room under the 40-search cap
    assert cli.main(["new", "lesson", "--seed", TOPIC, "--unattended", "--max-usd", "100"]) == 0
    r = Run.open("lesson", runs_dir=runs)
    assert "L02 follow-up" not in resp.row_calls
    d = r.read("decisions")
    assert "## follow-ups" in d and "left out L02" in d and "L02 partial" in d
    assert r.path("chapter_mp3").exists()


def test_scout_paused_at_its_budget_is_written_up(env):
    runs, resp, _ = env
    resp.scout_pause = True
    assert cli.main(["new", "lesson", "--seed", TOPIC]) == 0  # seed only: run the scout by hand
    assert cli.main(["scout", "lesson"]) == 0
    r = Run.open("lesson", runs_dir=runs)
    assert resp.scout_calls == 1 and len(calls_with(resp, "You were scouting the territory")) == 1
    assert "Self-healing lime clasts" in r.read("map")
    assert json.loads(r.path("scout_sources").read_text())["written_up"] is True


def test_new_refuses_a_different_seed_for_an_existing_run(env):
    runs, _, _ = env
    assert cli.main(["new", "lesson", "--seed", TOPIC]) == 0
    assert cli.main(["new", "lesson", "--seed", "Something else entirely"]) == 1
    assert cli.main(["new", "lesson", "--seed", TOPIC]) == 0  # the same seed continues
    with pytest.raises(StageError, match="needs a topic"):
        seed.write(Run.open("empty", create=True, runs_dir=runs))


def test_run_names_from_seeds_are_unique(tmp_path):
    a = seed.run_name(TOPIC, tmp_path)
    (tmp_path / a).mkdir()
    b = seed.run_name(TOPIC, tmp_path)
    assert a.startswith("roman-concrete-lasted-") and b == a + "-2"
    assert seed.run_name("what is it?", tmp_path).startswith("seed-")


def test_seed_variants_drop_the_episode_and_keep_the_parser_contract():
    for p in PROMPTS_DIR.glob("*_seed.md"):
        body = p.read_text().split("\n---\n", 1)[1]
        assert "episode" not in body.lower().replace("episode_has", ""), p.name
        assert "podcast" not in body.lower(), p.name
        assert (PROMPTS_DIR / p.name.replace("_seed", "")).exists(), p.name
    # The research entry format is a parser contract: the two versions show the same entry lines.
    a = load_prompt("research_format", row_id="L01", start=1)
    b = load_prompt("research_format_seed", row_id="L01", start=1)
    entries = lambda t: [ln for ln in t.splitlines() if ln.startswith("[L01.")]  # noqa: E731
    assert entries(a) == entries(b) and len(entries(a)) == 5


def test_episode_runs_are_unchanged_by_seed_settings(run, cfg):
    run.path("source_dir").mkdir()
    run.path("transcript").write_text("t")
    assert common.run_config(run, cfg) is cfg and common.prompt_name(run, "ledger") == "ledger"
    s = Run.open("s", create=True, runs_dir=run.root.parent)
    s.path("source_dir").mkdir()
    s.path("seed").write_text("# Seed\n")
    c = common.run_config(s, cfg)
    assert c["chapter"]["target_words"] == 3000 and c["research"]["max_rows"] == 4
    assert c["research"]["budgets"] == cfg["research"]["budgets"]  # merged, not replaced
    assert common.prompt_name(s, "ledger") == "ledger_seed" and common.prompt_name(s, "coverage") == "coverage"


def test_same_nameless_command_continues_a_stopped_run(env, monkeypatch):
    runs, resp, _ = env
    monkeypatch.setattr(new, "remaining_forecast", lambda r, cfg: 0.0)
    assert cli.main(["new", "--seed", TOPIC, "--unattended", "--max-usd", "1.5"]) == 1
    assert cli.main(["new", "--seed", TOPIC, "--unattended", "--max-usd", "50"]) == 0
    assert len(list(runs.iterdir())) == 1 and resp.scout_calls == 1


def test_seed_run_records_the_script_and_uses_seed_corrections(env, monkeypatch):
    runs, resp, _ = env
    assert cli.main(["new", "lesson", "--seed", TOPIC, "--unattended"]) == 0
    r = Run.open("lesson", runs_dir=runs)
    d = r.read("decisions")
    assert "## script" in d and "Approved the revised script without review" in d and "L03 missing" in d
    brief_text = r.read("brief")
    assert "## Corrections" in brief_text  # the fake verify agent corrects one claim
    assert "episode" not in brief_text.split("## Corrections")[1].split("\n- ")[0].lower()


def test_seed_ledger_parks_rows_past_the_lesson_size(env, cfg):
    runs, resp, _ = env
    cfg["seed_runs"]["research"]["max_rows"] = 2
    assert cli.main(["new", "lesson", "--seed", TOPIC]) == 0
    for st in ("scout", "angles", "choose", "ledger"):
        assert cli.main([st, "lesson"]) == 0
    led = ledger.load(Run.open("lesson", runs_dir=runs), None)
    assert len(led["rows"]) == 2 and "Beauty prompts a desire to replicate." in led["parked"]


def test_failed_scout_write_up_does_not_buy_the_search_again(env, monkeypatch):
    runs, resp, _ = env
    resp.scout_pause = True
    real = resp.__call__

    def bad_writeup(kwargs):
        if "You were scouting the territory" in all_text(kwargs):
            return resp.msg(kwargs, "no map here")
        return real(kwargs)

    assert cli.main(["new", "lesson", "--seed", TOPIC]) == 0
    monkeypatch.setattr(llm, "_client", FakeAnthropic(bad_writeup))
    assert cli.main(["scout", "lesson"]) == 1
    r = Run.open("lesson", runs_dir=runs)
    assert (r.root / "01_scout.pending.json").exists() and not r.path("map").exists()
    monkeypatch.setattr(llm, "_client", FakeAnthropic(resp))
    assert cli.main(["scout", "lesson"]) == 0
    assert resp.scout_calls == 1 and r.path("map").exists() and not (r.root / "01_scout.pending.json").exists()


def test_ctrl_c_mid_stage_still_writes_a_problem_status(env, monkeypatch):
    runs, resp, _ = env
    from stages import scout

    def interrupted(r, cfg):
        raise KeyboardInterrupt

    monkeypatch.setattr(new, "STEPS", [("scout", "map", interrupted, "Looking into it")] + new.STEPS[1:])
    assert cli.main(["new", "lesson", "--seed", TOPIC, "--unattended"]) == 130
    st = json.loads(Run.open("lesson", runs_dir=runs).path("status").read_text())
    assert st["state"] == "problem" and st["retryable"] is True
    assert st["message"] == "The chapter was interrupted while looking into it."
