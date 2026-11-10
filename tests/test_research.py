import pytest
from fakes import Responder, all_text, findings

from stages import research
from stages.common import StageError, dossier_entries
from stages.research import merge_claims, parse_topics, quote_check, topic_paths

PAGE = [{"url": "https://www.gutenberg.org/ebooks/1600", "title": "Symposium",
         "text": "He who has been instructed thus far in the things of love, and who has learned to see "
                 "the beautiful in due order and succession, will suddenly perceive a nature of wondrous beauty."}]


def test_quote_check_matches_exact_ellipsis_and_curly_quotes():
    d = ("## Claims\n"
         "[C1] “He who has been instructed thus far in the things of love” Source: https://gutenberg.org/ebooks/1600\n"
         "[C2] \"He who has been instructed ... will suddenly perceive a nature of wondrous beauty.\" "
         "Source: https://www.gutenberg.org/ebooks/1600/\n"
         "[C3] \"He who has been taught about love will see beauty suddenly appear\" Source: https://www.gutenberg.org/ebooks/1600\n"
         "[C4] A claim with no quote. Source: https://x.org\n")
    ok, missing, unchecked = quote_check(d, PAGE)
    assert ok == ["C1", "C2"] and missing == ["C3"] and unchecked == []
    assert quote_check(d, []) == ([], [], ["C1", "C2", "C3"])


def test_parse_topics_with_rounds():
    plan = ("# Research plan: X\n\n## Overview\nO.\n\n## T1: Plato\n- Goal: a\n\n## T2: Weil\n- Goal: b\n\n"
            "# Round 2 follow-ups\n\n## T3 - Boethius\n- Goal: c\n")
    topics = parse_topics(plan)
    assert [(t["id"], t["title"]) for t in topics] == [(1, "Plato"), (2, "Weil"), (3, "Boethius")]
    assert "Round 2" not in topics[1]["text"]
    with pytest.raises(StageError):
        parse_topics("## T1: a\n## T1: b")


def test_merge_renumbers_without_touching_text(run):
    topics = [{"id": 1, "title": "One"}, {"id": 2, "title": "Two"}]
    for t in topics:
        md, _ = topic_paths(run, t["id"])
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(findings(t["id"]).split("<findings>")[1].split("</findings>")[0])
    claims, mapping, _ = merge_claims(run, topics)
    assert mapping["T1.1"] == 1 and mapping["T2.5"] == 10
    entries = dossier_entries(claims)
    assert sorted(entries) == list(range(1, 11))
    assert entries[7].startswith('[C7] "He who has been instructed thus far')
    assert "From T2:\nC6 and C8 do not conflict." in claims


def test_failed_agent_is_retried_alone(run, cfg, monkeypatch):
    from fakes import FakeAnthropic

    from stages import llm
    run.yes = True
    run.path("angle").write_text("a")
    run.path("map").write_text("m")
    resp = Responder()
    real = resp.__call__

    def flaky(kwargs):
        msg = real(kwargs)
        if "<topic>\n## T2:" in all_text(kwargs) and resp.topic_calls.get(2) == 1:
            msg.content = [type(msg.content[-1])(type="text", text="I ran out of budget.", citations=None)]
        return msg

    monkeypatch.setattr(llm, "_client", FakeAnthropic(flaky))
    with pytest.raises(StageError, match="T2"):
        research.run(run, cfg)
    assert topic_paths(run, 1)[0].exists() and not topic_paths(run, 2)[0].exists()
    assert (run.path("topics_dir") / "_partial_T02.md").exists()
    t1_calls = resp.topic_calls[1]
    research.run(run, cfg)
    assert resp.topic_calls[1] == t1_calls  # T1 was not paid for twice
    assert run.path("dossier").exists()
