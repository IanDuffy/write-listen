"""The end-to-end harness (python -m faststart): the report on a run, how the notes were used, the
blind comparison, the build's cost log and cap, and the e2e command itself with everything faked."""

import copy
import json
from types import SimpleNamespace as NS

import yaml

import pytest

import fast_outline_fakes as A
import fast_sections_fakes as C
from fakes import all_text
from fast_run_fakes import (Balance, Patient, compare_verdict, happened, install, no_leaks, offline,  # noqa: F401
                            settings, slow_section_3, wait_for, world)

import cli
from faststart import __main__ as main_mod
from faststart import base, compare, e2e, narrate, notes
from faststart import run as fast_run
from faststart import timings as T
from faststart.e2e import ScriptedListener
from stages import common, new, seed
from stages.common import Run, StageError, load_config

REMARKS = [{"kind": "landed", "text": "The barge going down into the water, I could see that."},
           {"kind": "unclear", "text": "I lost track of where the ash came from."},
           {"kind": "connection", "text": "My grandfather was a mason in Naples."}]
CALLBACK = "Your grandfather the mason would have known this wall on sight."

OTHER_SCRIPT = ("<!-- seg01 -->\n\nThe other telling starts with a summary of everything it will say. [D01]\n\n"
                "<!-- seg02 -->\n\nThen it says it, in the order it promised. [D02]\n")
OTHER_DOSSIER = ("# Dossier: The other telling\n\nBuilt in rank order once every row had findings.\n\n## Entries\n\n"
                 "[D01] (L01, claim, tier 1) The harbor was built in open water. Source: https://a.org/1\n\n"
                 "[D02] (L02, material, tier 2) A core drilled from the wall. Source: https://a.org/2\n")


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    """One fast run, made once for this file: section 2 locked solo after a hold, three remarks
    at the check-in, and a last section that calls back to one of them."""
    tmp = tmp_path_factory.mktemp("finished")
    cfg = load_config()
    cfg["narration"]["voice_id"] = "voice_fake"
    with pytest.MonkeyPatch.context() as mp:
        w = world(tmp, cfg, mp)
        w.cfg["fast"]["short_wait_seconds"] = 2
        clock = slow_section_3(w)
        w.chain.on("draft", 4, lambda kw: C.section_text(4, C.offered(C.last_block(kw))) + "\n\n" + CALLBACK)
        fast_run.run(w.r, w.cfg, player=Patient(), listener=ScriptedListener(REMARKS, gap=0), clock=clock)
    return w


@pytest.fixture
def costs(tmp_path, monkeypatch):
    p = tmp_path / "build_costs.jsonl"
    monkeypatch.setattr(e2e, "COSTS", p)
    return p


def lines(p, started: bool = False) -> list[dict]:
    """The build log's lines for paid steps (with started=True, the lines that only name a run as well)."""
    got = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [x for x in got if started or x.get("state") != e2e.STARTED]


# --- the report ---------------------------------------------------------------------------------

def test_the_report_holds_what_checks_9_and_12_ask_for(finished):
    r = finished.r
    rep = e2e.report(r, finished.cfg)
    assert rep["run"] == r.name and rep["title"] == "The harbor that heals itself" and rep["state"] == "ready"
    s = rep["start"]
    assert 0 <= s["scout"] <= s["outline"] <= s["first_audio"] <= s["playback"] and s["continued"] == 0
    assert s["scout_seconds"] is not None and s["outline_seconds"] is not None and s["opening_seconds"] is not None

    one, two, three, four = rep["sections"]
    assert [x["mode"] for x in rep["sections"]][:2] == ["opening", "solo"] and four["mode"] == "final"
    assert all(x["words"] > 30 and x["locked"] <= x["ready"] <= x["narrated"] for x in rep["sections"])
    assert two["needed"] < two["ready"] and two["margin"] < 0 and two["waited"] >= 3 and not two["projected"]
    assert one["waited"] == 0 and three["needed"] is not None and four["needed"] is not None

    hold = rep["holds"][0]
    assert (hold["section"], hold["reason"], hold["solo"], hold["covered_by"]) == (2, "end_of_audio", True, "check-in")
    assert two["waited"] == pytest.approx(hold["seconds"], abs=0.06)
    assert hold["decision"] == "check_in" and rep["solo"][0]["section"] == 2 and rep["solo"][0]["waited"] >= 3

    assert [(x["id"], x["section"], x["research"]) for x in rep["ledger"]] == [
        ("L01", 2, "filled"), ("L02", 2, "filled"), ("L03", 3, "filled"), ("L04", 4, "filled")]
    assert all(x["entries"] == 6 and x["coverage"] in ("covered", "thin", "missing") for x in rep["ledger"])
    assert rep["cost_usd"] == pytest.approx(r.spent(), abs=0.001)
    assert sum(c["usd"] for c in rep["cost"]) == pytest.approx(rep["cost_usd"], abs=0.001)
    assert {c["stage"] for c in rep["cost"]} >= {"fast_scout", "fast_outline", "research_row", "fast_draft", "fast_lock",
                                                 "fast_narrate", "fast_note"}
    assert rep["words"] > 200 and rep["audio_seconds"] == pytest.approx(sum(
        p.seconds for n in range(1, 5) for p in narrate.pieces(r, n)), abs=0.3)
    assert [n["text"] for n in rep["notes"]] == [x["text"] for x in REMARKS] and rep["problems"] == []


def test_the_report_renders_as_tables_ready_to_paste(finished, capsys):
    text = e2e.render(e2e.report(finished.r, finished.cfg))
    for head in ("| Seed to | At | The step took |", "| Section | Title | Words | Edit | Locked | Ready (first piece) |",
                 "| Hold | Waiting for section | Began | Length |", "| Row | Section | Research | Entries |",
                 "| Stage | Calls | Cost |", "| Listener note | At |"):
        assert head in text, head
    assert "| 2 | Ash and lime |" in text and "the listener waited" in text and "playback starts here" in text
    assert "Solo-edit fallbacks: 1 (section 2)." in text and "| yes | check-in | yes |" in text
    assert "**Total**" in text and "minutes." in text
    table = [ln for ln in text.splitlines() if ln.startswith("|")]
    assert all(ln.endswith("|") for ln in table)

    assert main_mod.main(["report", str(finished.r.root)]) == 0  # a path works as well as a run name
    assert "| Section | Title |" in capsys.readouterr().out
    assert main_mod.main(["report", str(finished.r.root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sections"][1]["mode"] == "solo"


def test_the_report_counts_a_solo_fallback_once_and_only_where_the_solo_lock_stood(finished, tmp_path):
    """Regression: the report listed one fallback per solo_fallback event. The event is written when
    the solo lock is issued; a lock issued again repeats it, and one that fails may end as a paired lock."""
    import shutil

    root = tmp_path / finished.r.name
    shutil.copytree(finished.r.root, root)
    r = Run(name=finished.r.name, root=root)
    tl = base.Timeline(r)
    tl.event(T.SOLO_FALLBACK, section=2, waited=9.0)  # section 2's solo lock, issued a second time
    tl.event(T.SOLO_FALLBACK, section=3, waited=4.0)  # a solo lock of 3 that failed; its paired lock is the one on record
    rep = e2e.report(r, finished.cfg)
    assert [x["section"] for x in rep["solo"]] == [2] and rep["solo"][0]["waited"] == 9.0
    assert "Solo-edit fallbacks: 1 (section 2)." in e2e.render(rep)


def test_the_report_refuses_a_run_that_is_not_fast(run, capsys):
    assert main_mod.main(["report", str(run.root)]) == 1
    assert "not a fast run" in capsys.readouterr().err


def test_a_hold_shorter_than_the_short_wait_would_have_been_a_pause(finished):
    cfg = {**finished.cfg, "fast": {**finished.cfg["fast"], "short_wait_seconds": 100000}}
    rep = e2e.report(finished.r, cfg)
    assert rep["holds"][0]["covered_by"] == "pause" and "no need: a pause" in e2e.render(rep)


# --- how the notes were used (check 10) -----------------------------------------------------------

def test_notes_use_quotes_the_later_lines_that_share_a_notes_words(finished, capsys):
    found = e2e.notes_use(finished.r)
    assert [x["text"] for x in found] == [x["text"] for x in REMARKS]
    assert all(x["sections_after"] == [2, 3, 4] and x["section"] == 1 for x in found)  # said before 2 was locked
    landed, unclear, connection = found
    assert connection["lines"] == [{"section": 4, "line": CALLBACK, "shared": ["grandfather", "mason"]}]
    assert unclear["lines"] == []  # the fake writer cleared nothing up, and the tool does not pretend it did
    assert main_mod.main(["notes-use", str(finished.r.root)]) == 0
    out = capsys.readouterr().out
    assert f'- Section 4: "{CALLBACK}" (shares: grandfather, mason)' in out
    assert "matching words" in out and "No line in them shares its distinctive words." in out


def test_notes_use_says_when_a_note_came_too_late_or_never(finished, tmp_path, cfg, monkeypatch):
    late = world(tmp_path, cfg, monkeypatch, name="late")
    late.chain.on("lock", 4, lambda kw: notes.add(late.r.root, "The mason again, please.", "wants more", section=3) and None)
    fast_run.run(late.r, late.cfg, episode=False)
    (found,) = e2e.notes_use(late.r)
    assert found["sections_after"] == []  # every lock had begun: nothing could use it
    assert "nothing could use it" in e2e.render_notes_use(late.r, [found])
    quiet = Run.open("quiet", create=True, runs_dir=tmp_path)
    assert e2e.render_notes_use(quiet, e2e.notes_use(quiet)) == "quiet: no listener notes.\n"


# --- the blind comparison (check 11) --------------------------------------------------------------

@pytest.fixture
def other(tmp_path):
    root = tmp_path / "elsewhere" / "overnight-run"  # a run folder outside this checkout's runs/
    root.mkdir(parents=True)
    (root / "09_script_final.md").write_text(OTHER_SCRIPT, encoding="utf-8")
    (root / "04_dossier.md").write_text(OTHER_DOSSIER, encoding="utf-8")
    return root


def verdict_for(who: dict) -> dict:
    """A verdict whose quotations come from the right scripts, whichever label each run got."""
    fast = next(k for k, v in who.items() if v == "fast")
    oth = next(k for k, v in who.items() if v == "other")
    return compare_verdict(
        criteria=[{"name": "arc", "verdict": fast, "reason": f"{fast} builds; {oth} lists.",
                   "quotes": [{"script": fast, "text": "So the opening rests here, on a wall going into the water",
                               "why": "it hands off"}]},
                  {"name": "repetition", "verdict": "tie", "reason": "Neither repeats itself.", "quotes": []},
                  {"name": "accuracy", "verdict": oth, "reason": f"{fast} outruns an entry.",
                   "quotes": [{"script": fast, "text": "a sentence the judge made up", "why": "not in the script"}]}],
        overall={"verdict": fast, "reason": "The arc decides it."},
        differences=[{"script": oth, "quote": "The other telling starts with a summary of everything it will say.",
                      "why_it_matters": "It tells the listener the ending first."}])


def test_compare_is_blind_and_unblinds_only_in_its_output(finished, other, monkeypatch):
    r = finished.r
    chain, _ = install(monkeypatch)
    who = compare.labels(r.name, other.name)
    chain.compare = verdict_for(who)
    before = r.spent()

    out = compare.run(r, other, finished.cfg)

    (kw,) = chain.of("compare")
    sent = all_text(kw)
    script_a = C.tag(sent, "script_a")
    assert script_a == (r.read("script_final").strip() if who["A"] == "fast" else OTHER_SCRIPT.strip())
    assert C.tag(sent, "script_b") == (OTHER_SCRIPT.strip() if who["A"] == "fast" else r.read("script_final").strip())
    # the judge is told nothing of how either was made, or which is which
    for word in (r.name, other.name, "overnight", "fast run", "Fast Start", "as each row's research landed",
                 "Built in rank order"):
        assert word not in sent, word
    fast_entries = C.tag(sent, "dossier_a" if who["A"] == "fast" else "dossier_b")
    assert fast_entries.startswith("[D01] (L00,") and "## " not in fast_entries  # the entries alone
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": compare.SCHEMA}

    assert out["labels"] == who and out["fast"] == r.name and out["other"] == other.name
    u = out["unblinded"]
    assert [(c["name"], c["winner"]) for c in u["criteria"]] == [("arc", "fast"), ("repetition", "tie"), ("accuracy", "other")]
    assert u["overall"]["winner"] == "fast"
    assert u["criteria"][0]["quotes"][0] == {"from": "fast", "why": "it hands off", "found": True,
                                             "text": "So the opening rests here, on a wall going into the water"}
    assert u["criteria"][2]["quotes"][0]["found"] is False  # a quotation that is in neither script is flagged
    assert u["differences"][0]["from"] == "other" and u["differences"][0]["found"]
    assert out["cost_usd"] == pytest.approx(r.spent() - before) and out["cost_usd"] > 0
    assert r.read_log()[-1]["stage"] == "fast_compare"

    assert json.loads((r.root / "fast_compare.json").read_text(encoding="utf-8"))["unblinded"] == u
    md = (r.root / "fast_compare.md").read_text(encoding="utf-8")
    assert f"A was {'fast (' + r.name + ')' if who['A'] == 'fast' else 'the other run (overnight-run)'}" in md
    assert f"**Overall: fast ({r.name}).**" in md and "| accuracy | the other run (overnight-run) |" in md
    assert "(not found word for word in that script)" in md and "The other telling starts with a summary" in md


def test_the_blind_order_is_fixed_by_the_run_names_and_the_schema_is_strict():
    assert compare.labels("a-run", "b-run") == compare.labels("a-run", "b-run")
    seen = {compare.labels(f"fast-{i}", "overnight")["A"] for i in range(40)}
    assert seen == {"fast", "other"}  # sometimes the fast script is A, sometimes B: the name decides, not a habit

    def strict(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
            for sub in schema["properties"].values():
                strict(sub)
        elif schema.get("type") == "array":
            strict(schema["items"])
        assert not {"minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"} & set(schema)
    strict(compare.SCHEMA)


def test_compare_needs_two_finished_runs_and_every_criterion(finished, other, tmp_path, monkeypatch):
    chain, _ = install(monkeypatch)
    with pytest.raises(StageError, match="two different runs"):
        compare.run(finished.r, finished.r.root, finished.cfg)
    (other / "09_script_final.md").unlink()
    with pytest.raises(StageError, match="No final script"):
        compare.run(finished.r, other, finished.cfg)
    assert chain.calls == []
    (other / "09_script_final.md").write_text(OTHER_SCRIPT, encoding="utf-8")
    chain.compare = compare_verdict(criteria=compare_verdict()["criteria"][:2])
    with pytest.raises(StageError, match="no verdict on accuracy"):
        compare.run(finished.r, other, finished.cfg)


def test_the_compare_command_counts_against_the_builds_cap(finished, other, costs, monkeypatch, capsys):
    chain, _ = install(monkeypatch)
    monkeypatch.setattr(main_mod, "load_config", lambda: finished.cfg)
    chain.compare = verdict_for(compare.labels(finished.r.name, other.name))
    e2e.record_costs("e2e", finished.r, 0)  # as the e2e command that made the run left it
    assert main_mod.main(["compare", str(finished.r.root), str(other)]) == 0
    assert "a blind comparison" in capsys.readouterr().out
    made, line = lines(costs)
    assert line["what"] == "compare" and line["run"] == finished.r.name and line["other"] == "overnight-run"
    assert line["cost_usd"] == line["claude_usd"] > 0 and line["elevenlabs_chars"] == 0  # the comparison alone
    assert made["cost_usd"] + line["cost_usd"] == pytest.approx(finished.r.spent(), abs=0.001)

    costs.write_text(json.dumps({"what": "e2e", "cost_usd": 24.99}) + "\n", encoding="utf-8")
    assert main_mod.main(["compare", str(finished.r.root), str(other)]) == 1
    assert "of its $25.00 cap" in capsys.readouterr().err and len(chain.of("compare")) == 1 and len(lines(costs)) == 1


def test_a_comparison_catches_up_what_the_build_log_never_heard_of(finished, other, costs, monkeypatch, capsys):
    """Regression: a step's line was what the command itself added to the run's log, so spend no
    line had recorded (a killed try, a continuation by cli.py) stayed out of the build's total."""
    chain, _ = install(monkeypatch)
    monkeypatch.setattr(main_mod, "load_config", lambda: finished.cfg)
    chain.compare = verdict_for(compare.labels(finished.r.name, other.name))
    assert main_mod.main(["compare", str(finished.r.root), str(other)]) == 0
    (line,) = lines(costs)
    assert line["cost_usd"] == pytest.approx(finished.r.spent(), abs=0.001) and line["elevenlabs_chars"] > 0
    assert e2e.build_spend()["cost_usd"] == pytest.approx(finished.r.spent(), abs=0.001)


def test_a_run_made_another_way_is_recorded_in_the_builds_costs_once(finished, costs, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "load_config", lambda: finished.cfg)
    assert main_mod.main(["record", str(finished.r.root)]) == 0
    (line,) = lines(costs)
    assert (line["what"], line["run"]) == ("overnight", finished.r.name)
    assert line["cost_usd"] == pytest.approx(finished.r.spent(), abs=0.001) and line["elevenlabs_chars"] > 0
    assert e2e.build_spend()["cost_usd"] == line["cost_usd"]
    assert main_mod.main(["record", str(finished.r.root)]) == 1 and "already in" in capsys.readouterr().err
    assert len(lines(costs)) == 1


# --- the e2e command, with everything faked -------------------------------------------------------

@pytest.fixture
def harness(tmp_path, cfg, monkeypatch, costs):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(common, "RUNS_DIR", runs)
    monkeypatch.setattr(seed, "RUNS_DIR", runs)
    settings(cfg, tmp_path)
    cfg["fast"]["narration"].update(synth_chars_per_second=1e6, speech_chars_per_second=100)  # short silence, at once
    # each command is its own process: it loads the config afresh, and nothing one leaves in it reaches the next
    monkeypatch.setattr(main_mod, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(cli, "load_config", lambda: copy.deepcopy(cfg))
    chain, voice = install(monkeypatch)
    return NS(runs=runs, cfg=cfg, chain=chain, voice=voice, costs=costs, tmp=tmp_path)


def test_e2e_with_a_skip_and_three_remarks(harness, capsys):
    r = Run(name="skipper", root=harness.runs / "skipper")
    # section 2 is not locked until the listener has skipped to it and is waiting: the check-in is certain
    harness.chain.on("lock", 2, lambda kw: wait_for(lambda: happened(r, T.HOLD_START, section=2)) and None)
    remarks = harness.tmp / "remarks.json"
    remarks.write_text(json.dumps(REMARKS), encoding="utf-8")

    code = main_mod.main(["e2e", "--seed", A.TOPIC, "--why", "It is my trade.", "--name", "skipper", "--narrate", "sim",
                          "--skip-at", "0.05", "--remarks", str(remarks), "--remark-gap", "0", "--max-usd", "3"])
    out = capsys.readouterr().out
    assert code == 0, out

    assert harness.voice.calls == []  # simulated: no ElevenLabs character was asked for
    assert all(p.simulated for n in range(1, 5) for p in narrate.pieces(r, n)) and r.path("chapter_mp3").exists()
    skip = [e for e in base.read_timeline(r) if e["event"] == T.SKIP]
    assert [(e["from"], e["to"]) for e in skip] == [(1, 2)]
    hold = next(e for e in base.read_timeline(r) if e["event"] == T.HOLD_START)
    assert (hold["section"], hold["reason"]) == (2, "skip")
    said = notes.load(r.root)
    assert [(n["kind"], n["text"], n["section"]) for n in said] == [(x["kind"], x["text"], 1) for x in REMARKS]
    assert all(x["text"] in all_text(harness.chain.of("lock", 4)[0]) for x in REMARKS)  # the later sections had them

    # the listener's memory went inside the run folder, not to listener/
    assert (r.root / "listener" / "history.md").exists() and not (harness.tmp / "listener").exists()
    # the report was printed, and the run is in the build's cost log
    assert "| Hold | Waiting for section |" in out and "| skip |" in out and "| Listener note |" in out
    (line,) = lines(harness.costs)
    assert (line["what"], line["run"], line["narrate"], line["state"]) == ("e2e", "skipper", "sim", "ready")
    assert line["seed"] == A.TOPIC and line["elevenlabs_chars"] == 0 and line["elevenlabs_usd"] == 0
    assert line["claude_usd"] == line["cost_usd"] == pytest.approx(r.spent(), abs=0.001) and line["seconds"] >= 0
    assert (r.root / "11_episode" / "episode.json").exists()

    assert main_mod.main(["spend"]) == 0
    assert f"${line['cost_usd']:.2f} of $25.00" in capsys.readouterr().out


def test_e2e_narrate_first_pays_for_the_first_piece_only(harness, capsys):
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "first", "--narrate", "first", "--no-episode"]) == 0
    r = Run(name="first", root=harness.runs / "first")
    pieces = [p for n in range(1, 5) for p in narrate.pieces(r, n)]
    real = [p for p in pieces if not p.simulated]
    assert real and real[0] is not None and [p.simulated for p in pieces] == sorted(p.simulated for p in pieces)
    assert (real[0].section, real[0].index) == (1, 0) and sum(p.chars for p in real) <= 450
    assert harness.voice.texts == [p.text for p in real] and len(real) < len(pieces)
    (line,) = lines(harness.costs)
    assert line["elevenlabs_chars"] == sum(p.chars for p in real) and line["elevenlabs_usd"] > 0
    assert line["claude_usd"] == pytest.approx(line["cost_usd"] - line["elevenlabs_usd"])
    assert not (r.root / "11_episode" / "episode.json").exists()


def test_e2e_refuses_a_run_that_could_pass_the_builds_cap(harness, capsys):
    harness.costs.write_text(json.dumps({"what": "e2e", "cost_usd": 18.0, "claude_usd": 18.0}) + "\n", encoding="utf-8")
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "refused"]) == 1  # 18 + the run's cap of 8 > 25
    err = capsys.readouterr().err
    assert "build has spent $18.00 of its $25.00 cap" in err and "Not started" in err
    assert not (harness.runs / "refused").exists() and harness.chain.calls == []
    assert len(lines(harness.costs, started=True)) == 1
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "fits", "--max-usd", "6", "--no-episode"]) == 0
    assert [x.get("run") for x in lines(harness.costs)] == [None, "fits"]


def test_e2e_records_what_a_stopped_run_cost_and_continues_it_by_name(harness, capsys):
    harness.chain.on("lock", 3, lambda kw: (_ for _ in ()).throw(RuntimeError("the editor is out")))
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "stopped", "--no-episode"]) == 1
    assert "lock 3 (paired) failed" in capsys.readouterr().err
    (first,) = lines(harness.costs)
    assert first["state"] == "problem" and first["cost_usd"] > 0
    del harness.chain.hooks[("lock", 3)]
    assert main_mod.main(["e2e", "--name", "stopped", "--no-episode"]) == 0
    first, second = lines(harness.costs)
    r = Run(name="stopped", root=harness.runs / "stopped")
    assert second["state"] == "ready" and first["cost_usd"] + second["cost_usd"] == pytest.approx(r.spent(), abs=0.001)
    assert main_mod.main(["e2e"]) == 1 and "needs --seed" in capsys.readouterr().err


def boom(kw):
    raise RuntimeError("the editor is out")


def all_pieces(r) -> list:
    return [p for n in range(1, 5) for p in narrate.pieces(r, n)]


def test_a_try_killed_before_it_could_write_its_line_is_still_counted(harness, capsys):
    """Regression: the only write to the build log was in e2e's finally, as the difference from the
    run's log when the command started. A killed process wrote nothing, and the continuation then
    left out everything the killed try had spent: the build's cap undercounted by a whole try."""
    harness.chain.on("lock", 3, boom)
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "killed", "--no-episode"]) == 1
    del harness.chain.hooks[("lock", 3)]
    r = Run(name="killed", root=harness.runs / "killed")
    # what a kill leaves: the line that named the run when it started, and none for what it spent
    began = [x for x in lines(harness.costs, started=True) if x.get("state") == e2e.STARTED]
    assert [(x["run"], x["dir"], x["cost_usd"]) for x in began] == [("killed", str(r.root), 0)]
    harness.costs.write_text("".join(json.dumps(x) + "\n" for x in began), encoding="utf-8")
    spent = r.spent()
    assert spent > 0 and e2e.build_spend()["cost_usd"] == pytest.approx(spent, abs=0.001)  # the cap sees it at once
    assert main_mod.main(["spend"]) == 0 and f"${spent:.2f} of $25.00" in capsys.readouterr().out

    assert main_mod.main(["e2e", "--name", "killed", "--no-episode"]) == 0
    (line,) = lines(harness.costs)
    assert line["state"] == "ready" and line["cost_usd"] == pytest.approx(r.spent(), abs=0.001) and r.spent() > spent
    assert e2e.build_spend()["cost_usd"] == pytest.approx(r.spent(), abs=0.001)

    # a run the log has never heard of (made by cli.py) is named before the cap is checked, so all of it counts
    harness.costs.unlink()
    assert e2e.build_spend()["cost_usd"] == 0
    harness.costs.write_text(json.dumps({"what": "e2e", "cost_usd": 24.0}) + "\n", encoding="utf-8")
    assert main_mod.main(["e2e", "--name", "killed", "--max-usd", str(round(r.spent() + 2, 2))]) == 1
    assert "Not started" in capsys.readouterr().err  # 24 + the run's own spend + the 2 it may still spend > 25


def test_a_stopped_build_test_stays_one_however_it_is_continued(harness, capsys):
    """Regression: the simulated narration and the listener folder lived only in the e2e process, and
    a stopped run's message said to continue with cli.py: that narrated the whole chapter for real,
    wrote Ian's listener/history.md, and added nothing to the build's costs."""
    harness.chain.on("opening", 1, boom)
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "simrun", "--narrate", "sim", "--no-episode"]) == 1
    err = capsys.readouterr().err
    assert "To continue: python -m faststart e2e --name simrun" in err and "cli.py new" not in err
    del harness.chain.hooks[("opening", 1)]
    root = harness.runs / "simrun"
    own = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert own == {"fast": {"narration": {"build_test": "sim", "simulate_after_chars": 0}},
                   "listener_dir": str(root / "listener")}
    assert harness.cfg["fast"]["narration"]["simulate_after_chars"] is None  # nothing was left in the shared config
    stopped = Run(name="simrun", root=root)
    assert e2e.narrate_mode(stopped, None) == "sim" and e2e.narrate_mode(stopped, "real") == "real"  # no piece yet

    # the command the message used to print, which is also what the server's Try again runs
    assert cli.main(["new", "simrun", "--unattended"]) == 0
    r = Run(name="simrun", root=root)
    assert harness.voice.calls == [] and all_pieces(r) and all(p.simulated for p in all_pieces(r))
    assert (root / "listener" / "history.md").exists() and not (harness.tmp / "listener").exists()
    assert e2e.build_spend()["cost_usd"] == pytest.approx(r.spent(), abs=0.001)  # though cli.py wrote no line


def test_a_real_run_continued_by_name_keeps_its_voice(harness, capsys):
    """Regression: --narrate defaulted to sim on every call, so `e2e --name RUN` finished a real
    run in silence, and the silent sections could never be narrated afterwards."""
    harness.chain.on("lock", 3, boom)
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "real", "--narrate", "real", "--no-episode"]) == 1
    del harness.chain.hooks[("lock", 3)]
    r = Run(name="real", root=harness.runs / "real")
    before = all_pieces(r)
    assert before and not any(p.simulated for p in before)
    capsys.readouterr()
    assert main_mod.main(["e2e", "--name", "real", "--narrate", "sim", "--no-episode"]) == 1
    assert "started with --narrate real" in capsys.readouterr().err and all_pieces(r) == before
    assert main_mod.main(["e2e", "--name", "real", "--no-episode"]) == 0
    after = all_pieces(r)
    assert len(after) > len(before) and not any(p.simulated for p in after)
    assert harness.voice.texts == [p.text for p in after]  # every piece from the voice, each once
    assert [x["narrate"] for x in lines(harness.costs)] == ["real", "real"]
    # a run begun before the mode was kept with it: its pieces say what it is
    (r.root / "config.yaml").unlink()
    assert e2e.narrate_mode(r, None) == "real"
    with pytest.raises(StageError, match="part voice and part silence"):
        e2e.narrate_mode(r, "first")


def test_e2e_prints_the_balance_and_refuses_real_narration_that_does_not_fit(harness, capsys):
    harness.voice.balance.used, harness.voice.balance.limit = 30_000, 40_000  # a chapter may take 24 min x 150 x 6
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "short", "--narrate", "real"]) == 1
    out, err = capsys.readouterr()
    assert "ElevenLabs: 10,000 of 40,000 characters left this month; this run (narration real) will use up to 21,600." in out
    assert "Not enough narration characters this month" in err and "Not started" in err
    assert not (harness.runs / "short").exists() and harness.chain.calls == []
    assert lines(harness.costs, started=True) == []

    # silence uses none, so the same account is no obstacle; and the first piece alone fits
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "quiet", "--no-episode"]) == 0
    assert "(narration sim) will use none." in capsys.readouterr().out
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "one", "--narrate", "first", "--no-episode"]) == 0
    assert "(narration first) will use up to 450." in capsys.readouterr().out

    # a balance that cannot be read stops nothing: it is said, and the run goes on
    harness.voice.balance.error = RuntimeError("no network")
    assert main_mod.main(["e2e", "--seed", A.TOPIC, "--name", "unread", "--narrate", "first", "--no-episode"]) == 0
    assert "ElevenLabs: the balance could not be read; this run (narration first) will use up to 450." in capsys.readouterr().out
    r = Run(name="unread", root=harness.runs / "unread")
    assert any("could not be read" in str(x.get("error")) for x in r.read_log() if x.get("label") == "ElevenLabs balance")


def test_the_overnight_command_makes_the_other_script_under_the_builds_cap(harness, monkeypatch, capsys):
    """Check 11's overnight run: in a folder of its own, as far as the script, counted against the build."""
    ran = []

    def fake(label, key):
        def stage(r, cfg):
            ran.append((label, r.name, r.unattended, r.max_usd, cfg["listener_dir"]))
            r.log({"stage": label, "provider": "anthropic", "cost_usd": 0.5})
            r.write(key, f"{label}\n")
        return stage

    monkeypatch.setattr(new, "STEPS", [(label, key, fake(label, key), plain) for label, key, _, plain in new.STEPS])
    monkeypatch.setattr(fast_run, "run", lambda *a, **k: pytest.fail("the overnight command started a fast run"))
    # the fast run of the same seed is still going (or stopped) in runs/
    assert cli.main(["new", "--seed", A.TOPIC, "--why", "It is my trade.", "--mode", "fast"]) == 0
    (fast_root,) = [p for p in harness.runs.iterdir() if p.is_dir()]
    base.mark_fast(Run(name=fast_root.name, root=fast_root))

    assert main_mod.main(["overnight", "--seed", A.TOPIC, "--why", "It is my trade.", "--max-usd", "7"]) == 0
    (root,) = [p for p in harness.runs.iterdir() if p.is_dir() and p != fast_root]
    assert [x[0] for x in ran] == ["scout", "angles", "choose", "ledger", "research", "ledger-update", "brief",
                                   "draft", "coverage", "edit", "factcheck", "revise"]  # no narration, no report
    assert {x[1:] for x in ran} == {(root.name, True, 7.0, str(root / "listener"))}
    assert (root / "09_script_final.md").exists() and not (root / "10_audio").exists()
    assert not (root / "fast.json").exists() and sorted(p.name for p in fast_root.iterdir()) == ["00_source", "fast.json"]
    (line,) = lines(harness.costs)
    assert (line["what"], line["run"], line["state"], line["cost_usd"]) == ("overnight", root.name, "script", 6.0)
    assert f"compare <fast_run> {root.name}" in capsys.readouterr().out

    # the build's cap holds for it: 6 spent, and a run that may spend 20 more is refused before it exists
    assert main_mod.main(["overnight", "--seed", "Another topic entirely"]) == 1
    assert "of its $25.00 cap" in capsys.readouterr().err and len([p for p in harness.runs.iterdir() if p.is_dir()]) == 2
    # continued by name: nothing is left to do, and nothing is paid or counted twice
    assert main_mod.main(["overnight", "--name", root.name]) == 0 and len(ran) == 12
    assert e2e.build_spend()["cost_usd"] == 6.0
    assert main_mod.main(["overnight", "--name", fast_root.name]) == 1 and "is a fast run" in capsys.readouterr().err
    # and the other way: e2e does not continue the overnight folder as a fast run, or write anything into it
    kept, logged = sorted(p.name for p in root.iterdir()), len(lines(harness.costs, started=True))
    assert main_mod.main(["e2e", "--name", root.name]) == 1 and "is not a fast run" in capsys.readouterr().err
    assert sorted(p.name for p in root.iterdir()) == kept and len(lines(harness.costs, started=True)) == logged
    assert main_mod.main(["overnight"]) == 1 and "needs --seed" in capsys.readouterr().err


def test_a_stopped_overnight_run_is_recorded_and_says_how_to_continue(harness, monkeypatch, capsys):
    def scout(r, cfg):
        r.log({"stage": "scout", "provider": "anthropic", "cost_usd": 0.25})
        raise StageError("the scout found nothing")

    monkeypatch.setattr(new, "STEPS", [("scout", "map", scout, "Looking into it")] + new.STEPS[1:])
    assert main_mod.main(["overnight", "--seed", A.TOPIC, "--name", "night"]) == 1
    err = capsys.readouterr().err
    assert "stopped at scout: the scout found nothing" in err and "python -m faststart overnight --name night" in err
    (line,) = lines(harness.costs)
    assert (line["run"], line["state"], line["cost_usd"]) == ("night", "problem", 0.25)


def test_the_scripted_listener_speaks_once_and_spaces_its_remarks(tmp_path):
    slept, saved = [], []
    listener = ScriptedListener(REMARKS, gap=4.0, sleep=slept.append)
    add = lambda text, kind=None: saved.append((kind, text)) or {"text": text}
    listener.on_check_in(100.0, 2, add)
    listener.join(5)
    assert saved == [(x["kind"], x["text"]) for x in REMARKS] and slept == [4.0, 4.0]
    assert listener.at == {"now": 100.0, "waiting_for": 2} and len(listener.said) == 3
    listener.on_check_in(200.0, 3, add)  # a later check-in: nothing more to say
    assert len(saved) == 3
    (tmp_path / "remarks.json").write_text('{"kind": "landed"}', encoding="utf-8")
    with pytest.raises(StageError, match="JSON list"):
        e2e.load_remarks(str(tmp_path / "remarks.json"))
    with pytest.raises(StageError, match="--remarks"):
        e2e.load_remarks(str(tmp_path / "missing.json"))
