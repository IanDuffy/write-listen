"""The fast run's append-only dossier (faststart/dossier.py): an entry never changes its D number."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import ask_fixture
import pytest
from fakes import PAGE_TEXT, FakeAnthropic
from fast_research_fakes import (NOT_ON_PAGE, ON_PAGE, PAGE_URL, SCOUT_LINES, SEP_TEXT, SEP_URL, file_row, ledger_for,
                                 outline, scout_row)

from ask import aside
from episode import references
from episode import script as ep_script
from faststart import base
from faststart.dossier import Dossier
from stages import coverage, ledger, llm
from stages.brief import parse_segments
from stages.common import StageError, dossier_entries, entry_tags


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    """The dossier is code only: any model call from these tests is a bug (a real .env is present)."""
    def unexpected(kwargs):
        raise AssertionError("the dossier made a model call")
    monkeypatch.setattr(llm, "_client", FakeAnthropic(unexpected))


def row_lines(rid: str, n: int = 3) -> list[str]:
    return [f"[{rid}.{k}] (claim, tier 1) Fact {k} of {rid}. Source: {PAGE_URL}" for k in range(1, n + 1)]


def on_disk(run) -> dict[int, str]:
    return dossier_entries(run.path("dossier").read_text(encoding="utf-8"))


def test_numbers_stay_fixed_whatever_order_the_rows_land_in(run, cfg):
    scout_row(run)
    file_row(run, "L01", row_lines("L01", 4), pages={PAGE_URL: PAGE_TEXT})
    file_row(run, "L02", row_lines("L02", 2), pages={PAGE_URL: PAGE_TEXT})
    d = Dossier(run, cfg)
    assert d.add_row("L00") == [1, 2, 3, 4, 5]          # the scout's entries are D01.. at once
    assert d.add_row("L02") == [6, 7]                   # L02 lands before L01
    before = {n: text for n, text in d.entries().items()}
    assert d.add_row("L01") == [8, 9, 10, 11]           # and keeps its numbers when L01 lands
    assert d.numbers_of("L02") == [6, 7] and d.numbers_of("L00") == [1, 2, 3, 4, 5]
    assert {n: d.entries()[n] for n in before} == before  # nothing already filed changed: number, tags or text
    assert on_disk(run) == d.entries()
    assert entry_tags(on_disk(run)[6])["rows"] == ["L02"] and entry_tags(on_disk(run)[8])["rows"] == ["L01"]
    text = run.path("dossier").read_text(encoding="utf-8")
    assert text.index("### L00") < text.index("### L02") < text.index("### L01")  # landing order
    assert d.for_rows(["L02", "L00"]).keys() == {1, 2, 3, 4, 5, 6, 7}
    assert d.has("L01") and not d.has("L03") and d.numbers_of("L03") == []


def test_the_scout_row_is_filed_like_any_other_and_its_references_become_d_numbers(run, cfg):
    scout_row(run)  # Territory, Entries and Scenes: only the entries are filed
    d = Dossier(run, cfg)
    assert d.add_row(base.SCOUT_ROW) == [1, 2, 3, 4, 5]
    assert d.refs("L00") == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    e = on_disk(run)
    assert len(e) == 5 and all(entry_tags(x)["rows"] == ["L00"] for x in e.values())
    assert "see D02 for the scene" in e[5] and "L00.2" not in run.path("dossier").read_text(encoding="utf-8")
    assert entry_tags(e[2])["kind"] == "material" and entry_tags(e[1])["tier"] == 1
    assert "Scenes" not in run.path("dossier").read_text(encoding="utf-8")
    assert "### L00: what the scout found" in d.text()


def test_add_row_is_idempotent(run, cfg):
    file_row(run, "L01", row_lines("L01"), pages={PAGE_URL: PAGE_TEXT})
    d = Dossier(run, cfg)
    first = d.add_row("L01")
    text = run.path("dossier").read_text(encoding="utf-8")
    file_row(run, "L01", row_lines("L01", 6) + ["[L01.7] (claim, tier 1) Later. Source: " + PAGE_URL])  # a redo
    assert d.add_row("L01") == first == [1, 2, 3]
    assert run.path("dossier").read_text(encoding="utf-8") == text and len(d.entries()) == 3


def test_concurrent_rows_get_unique_consecutive_numbers_and_the_file_always_parses(run, cfg):
    sizes = {f"L{n:02d}": 2 + n % 4 for n in range(1, 13)}
    for rid, k in sizes.items():
        file_row(run, rid, row_lines(rid, k), pages={PAGE_URL: PAGE_TEXT})
    d = Dossier(run, cfg)
    stop, problems, reads = threading.Event(), [], []

    def reader():  # what ask-by-voice does while the run is writing
        seen: dict[int, str] = {}
        while not stop.is_set():
            if not run.path("dossier").exists():
                continue
            got = dossier_entries(run.path("dossier").read_text(encoding="utf-8"))
            reads.append(len(got))
            if sorted(got) != list(range(1, len(got) + 1)):
                problems.append(f"a read saw numbers {sorted(got)}")
            problems.extend(f"D{n} changed" for n in got if n in seen and seen[n] != got[n])
            seen.update(got)

    t = threading.Thread(target=reader)
    t.start()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = dict(zip(sizes, pool.map(d.add_row, sizes)))
    finally:
        stop.set()
        t.join()
    assert problems == [] and reads
    for rid, nums in numbers.items():
        assert nums == list(range(nums[0], nums[0] + sizes[rid]))       # a row's numbers are consecutive
    every = sorted(n for nums in numbers.values() for n in nums)
    assert every == list(range(1, sum(sizes.values()) + 1))             # unique, with no gaps
    assert on_disk(run) == d.entries() and len(d.entries()) == len(every)
    for rid, nums in numbers.items():
        assert all(entry_tags(on_disk(run)[n])["rows"] == [rid] for n in nums)


def test_a_new_dossier_on_the_same_run_knows_the_same_numbers(run, cfg):
    scout_row(run)
    file_row(run, "L02", row_lines("L02"), pages={PAGE_URL: PAGE_TEXT})
    file_row(run, "L01", row_lines("L01"), pages={PAGE_URL: PAGE_TEXT})
    d = Dossier(run, cfg)
    d.add_row("L00"), d.add_row("L02")
    entries, text = d.entries(), d.text()
    # The run stops. A row's findings file changing afterwards must not move anything already filed.
    file_row(run, "L02", row_lines("L02", 9))
    run.path("dossier").unlink()  # and the file itself is restored from the index
    again = Dossier(run, cfg)
    assert again.has("L00") and again.has("L02") and not again.has("L01")
    assert again.numbers_of("L02") == [6, 7, 8] and again.entries() == entries
    assert run.path("dossier").read_text(encoding="utf-8") == text
    assert again.add_row("L02") == [6, 7, 8]
    assert again.add_row("L01") == [9, 10, 11]
    # The restarted dossier still checks quotes against the pages earlier rows fetched.
    file_row(run, "L03", [f"[L03.1] (quote, tier 1, verbatim: yes) \"{ON_PAGE}\" Plato. Source: {PAGE_URL}"])
    third = Dossier(run, cfg)
    assert third.add_row("L03") == [12]
    assert entry_tags(third.entries()[12])["verbatim"] == "yes"


def test_a_quote_that_is_not_on_a_fetched_page_is_downgraded(run, cfg):
    scout_row(run)
    d = Dossier(run, cfg)
    d.add_row("L00")
    e = on_disk(run)
    assert entry_tags(e[3])["verbatim"] == "yes"      # word for word on the Gutenberg page
    assert entry_tags(e[4])["verbatim"] == "no"       # on no page: the script may not quote it
    text = d.text()
    assert "Quotes downgraded to verbatim: no, so the script may not quote them: D04 (not found on the fetched page)" in text
    assert "Verbatim quotes matched word for word on a fetched page: 1 (D03)" in text
    assert list(coverage.verbatim_entries(text)) == [3]
    script = f"Plato has Diotima say: “{ON_PAGE}.” [D03]\n\nAnd then: “{NOT_ON_PAGE}.” [D04]"
    assert [q for _, q in coverage.unbacked_quotes(script, text)] == [NOT_ON_PAGE + "."]
    # A later row quoting a page only the scout fetched is checked against that page.
    file_row(run, "L01", [f"[L01.1] (quote, tier 2, verbatim: yes) \"The dinner the Symposium describes is set "
                          f"in 416 BC\" SEP. Source: {SEP_URL}",
                          f"[L01.2] (quote, tier 2, verbatim: yes) \"The dinner was held in 399 BC\" SEP. "
                          f"Source: {SEP_URL}"])
    a, b = d.add_row("L01")
    assert entry_tags(d.entries()[a])["verbatim"] == "yes" and entry_tags(d.entries()[b])["verbatim"] == "no"
    assert entry_tags(on_disk(run)[4])["verbatim"] == "no" and entry_tags(on_disk(run)[3])["verbatim"] == "yes"


def test_a_rows_references_to_its_own_entries_become_d_numbers(run, cfg):
    scout_row(run)
    file_row(run, "L03", [
        f"[L03.1] (claim, tier 1) The ladder has rungs. Source: {PAGE_URL}",
        f"[L03.2] (tension, tier 1) This conflicts with L03.1, and L3.4 agrees; L05.1 and L03.9 are not ours. "
        f"Source: {PAGE_URL}",
        f"[L03.4] (material, tier 1) A scene. Source: {PAGE_URL}",   # the agent skipped a number
        f"[L03.4] (claim, tier 1) The same number again. Source: {PAGE_URL}",
    ], pages={PAGE_URL: PAGE_TEXT}, not_found="The date; L03.4 is the nearest thing.")
    d = Dossier(run, cfg)
    d.add_row("L00")
    assert d.add_row("L03") == [6, 7, 8] and d.refs("L03") == {1: 6, 2: 7, 4: 8}
    e = on_disk(run)
    assert "conflicts with D06, and D08 agrees; L05.1 and L03.9 are not ours" in e[7]
    assert "The same number again" not in d.text() and "L03.4" in d.text().split("## Source check")[1]
    not_found = d.text().split("## Not found")[1].split("## Sources cited")[0]
    assert "From L03:" in not_found and "D08 is the nearest thing" in not_found


def test_the_file_reads_through_the_readers_ask_and_episode_use(run, cfg, tmp_path):
    root = ask_fixture.build(run.root)         # a finished chapter whose script cites D01..D09
    (root / "04_dossier.md").unlink()          # the overnight dossier it came with
    o = outline()
    ledger.save(run, ledger_for(o))
    (root / "03_angle.md").write_text("# Angle: The ladder and the self\n\nBeauty is a ladder.\n", encoding="utf-8")
    scout_row(run)
    file_row(run, "L01", [
        f"[L01.1] (claim, tier 1) A claim whose text wraps\nonto a second line, and a third\n"
        f"[D01] that begins like an entry would. Source: Symposium, {PAGE_URL}, 210a.",
        f"[L01.2] (tension, tier 2) An objection. Source: SEP, {SEP_URL}",
        f"[L01.3] (connection, L02) Both climb.",
        f"[L01.4] (quote, tier 1, verbatim: yes) \"{ON_PAGE}\" Plato. Source: {PAGE_URL}",
    ], pages={PAGE_URL: PAGE_TEXT})
    d = Dossier(run, cfg)
    d.add_row("L00"), d.add_row("L01")
    text = (root / "04_dossier.md").read_text(encoding="utf-8")
    assert text.startswith("# Dossier: The ladder and the self\n")
    assert "### L01: Rung 2 turns the lover from one body to all." in text
    assert "Section 2, deep-dive; needs tier 2 or better." in text

    # stages.common: the parser contract, with the tags of an overnight entry.
    entries = dossier_entries(text)
    assert sorted(entries) == list(range(1, 10)) and entries == d.entries()
    assert entries[6].startswith("[D06] (L01, claim, tier 1) A claim whose text wraps onto a second line, and a "
                                 "third [D01] that begins like an entry would.")
    assert entries[1].startswith("[D01] (L00, claim, tier 1) The Symposium is Plato's dialogue")  # not overwritten
    assert entry_tags(entries[9]) == {"rows": ["L01"], "kind": "quote", "tier": 1, "verbatim": "yes",
                                      "verdict": None, "links": []}
    assert entry_tags(entries[8])["kind"] == "connection" and entry_tags(entries[8])["links"] == ["L02"]

    # episode/: links and tiers for the reference cards come from the entries.
    assert references.entry_link(entries[6], entries) == PAGE_URL
    assert references.entry_link(entries[7], entries) == SEP_URL and references.entry_link(entries[8], entries) is None
    segments = ep_script.parse((root / "09_script_final.md").read_text(encoding="utf-8"), {})
    offered = references.segment_entries(root, segments, entries)
    assert offered["seg01"] == ["D01", "D02", "D03"] and offered["seg03"] == ["D07", "D08", "D09"]
    sent = segments[2].sentences[0].id
    answer = {"sentences": [{"id": sent, "sources": [
        {"dossier": "D09", "title": "Symposium", "creator": "Plato", "note": ""},
        {"dossier": "D07", "title": "An objection", "creator": "", "note": ""}]}]}  # a tension: no card
    cards = references.candidates(segments[2], answer, offered["seg03"], entries)[sent]
    assert [(c["dossier_ref"], c["url"], c["tier"]) for c in cards] == [("D09", PAGE_URL, 1)]

    # ask/: the guide's dossier text, in full for the segment being heard and the next, one line each after.
    assert [s["entries"] for s in parse_segments((root / "05_brief.md").read_text(encoding="utf-8"))][0] == [1, 2, 3]
    cfg["listener_dir"] = str(tmp_path / "listener")
    lines = aside.Chapter(root, cfg).dossier_text(0).splitlines()
    assert len(lines) == 9 and [x[:5] for x in lines] == [f"[D{n:02d}]" for n in range(1, 10)]
    assert lines[0] == " ".join(entries[1].split()) and "Source:" in lines[5]
    assert lines[6] == "[D07] An objection." and "Source" not in lines[8]


def test_thin(run, cfg):
    cfg["research"]["tier_domains"] = {"gutenberg.org": 1, "wikipedia.org": 4}
    wiki = "https://en.wikipedia.org/wiki/Symposium_(Plato)"
    pages = {PAGE_URL: PAGE_TEXT}
    file_row(run, "L01", [f"[L01.1] (claim, tier 1) A fact. Source: {PAGE_URL}",
                          f"[L01.2] (material, tier 3) A scene. Source: {PAGE_URL}"], pages=pages)
    file_row(run, "L02", [f"[L02.1] (claim, tier 1) Wikipedia, recorded as tier 1. Source: {wiki}",
                          f"[L02.2] (material, tier 1) From a page nobody retrieved. Source: https://unseen.example.com/x"],
             urls={wiki: "Symposium"})
    file_row(run, "L03", [f"[L03.1] (claim, tier 1) A fact with nothing to tell. Source: {PAGE_URL}",
                          f"[L03.2] (connection, L01) A link."], pages=pages)
    file_row(run, "L04", [f"[L04.1] (claim, tier 1) A fact. Source: {PAGE_URL}",
                          f"[L04.2] (quote, tier 1, verbatim: yes) \"{ON_PAGE}\" Plato. Source: {PAGE_URL}"], pages=pages)
    file_row(run, "L05", [f"[L05.1] (claim, tier 1) Unchecked. Source: {PAGE_URL}"], pages=pages)
    file_row(run, "L06", [f"[L06.1] (claim, tier 1, corrected) Not 399 but 416 BC. Source: {PAGE_URL}"], pages=pages)
    file_row(run, "L07", [])
    file_row(run, "L08", [f"[L08.1] (claim, tier 1) A fact. Source: {PAGE_URL}",
                          f"[L08.2] (quote, tier 1, verbatim: yes) \"{NOT_ON_PAGE}\" Nobody. Source: {PAGE_URL}"],
             pages=pages)
    d = Dossier(run, cfg)
    row = lambda rid, need=2, type="deep-dive": {"id": rid, "tier_needed": need, "type": type}
    assert d.thin(row("L01")) == "no findings"                      # not landed
    for rid in ("L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08"):
        d.add_row(rid)
    assert d.thin(row("L01")) is None and d.best_tier("L01") == 1   # a tier 1 claim and a scene
    assert d.thin(row("L02")) == "best source tier is 4, and the row needs 2"   # the domain's tier; unseen URL uncounted
    assert d.thin(row("L02", need=4)) is None
    assert d.thin(row("L03")) == "no tellable material and no verbatim quote"
    assert d.thin(row("L04")) is None                               # a quote the script may use is something to tell
    assert d.thin(row("L08")) == "no tellable material and no verbatim quote"   # its quote was downgraded
    assert d.thin(row("L05", type="covered")) == "none of its claims is confirmed or corrected"
    assert d.thin(row("L06", type="covered")) is None
    assert d.thin(row("L07")) == "no entries" and d.numbers_of("L07") == [] and d.has("L07")
    assert "### L07" in d.text() and "(no entries)" in d.text()
    assert d.thin(row("L01", need=1)) is None
    file_row(run, "L09", [f"[L09.1] (claim, tier 3) Weak. Source: {PAGE_URL}"], pages=pages)
    d.add_row("L09")
    assert d.thin(row("L09")) == ("best source tier is 3, and the row needs 2; "
                                  "no tellable material and no verbatim quote")


def test_missing_findings_and_a_dossier_from_another_pipeline_are_refused(run, cfg):
    d = Dossier(run, cfg)
    assert d.text().count("## Entries") == 1 and d.entries() == {}
    with pytest.raises(StageError, match="no findings for L01"):
        d.add_row("L01")
    assert not d.has("L01") and not run.path("dossier").exists()
    run.path("dossier").write_text("# Dossier: overnight\n\n## Entries\n\n[D01] (L01, claim, tier 1) Paid for.\n")
    file_row(run, "L01", row_lines("L01"))
    with pytest.raises(StageError, match="not built by a fast run"):
        d.add_row("L01")
    assert "Paid for." in run.path("dossier").read_text() and not d.has("L01")


def test_the_index_holds_what_was_filed(run, cfg):
    scout_row(run)
    d = Dossier(run, cfg)
    d.add_row("L00")
    saved = json.loads((base.path(run, "work") / "dossier_index.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in saved["rows"]] == ["L00"]
    assert [e["d"] for e in saved["rows"][0]["entries"]] == [1, 2, 3, 4, 5]
    assert saved["rows"][0]["entries"][3]["tags"]["verbatim"] == "no"   # the checked verdict is what is kept
    assert "L00 4 of 6" in d.text().split("## Source check")[1]
    assert SCOUT_LINES[0].split(") ", 1)[1] in d.text() and SEP_TEXT not in d.text()
