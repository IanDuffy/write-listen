"""`wl study ...` (demo/cli.py)."""

import os

from demo import cli

from tester_fakes import demo_cfg


def test_create_takes_the_links_origin_from_dot_env(cfg, tmp_path, monkeypatch, capsys):
    # On the droplet `wl` runs through sudo with a clean environment: WL_BASE_URL is only in .env (the link to
    # /etc/write-listen/env), and a link printed with 127.0.0.1 would be useless to a tester (review 12).
    monkeypatch.setenv("WL_BASE_URL", "unset")  # recorded first, so the undo removes what .env sets
    monkeypatch.delenv("WL_BASE_URL")
    (tmp_path / ".env").write_text("WL_BASE_URL=https://listen.example.com\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    dcfg = demo_cfg(cfg)
    monkeypatch.setattr(cli, "load_config", lambda: dcfg)
    assert cli.main(["create", "Sam"]) == 0
    out = capsys.readouterr().out
    assert "https://listen.example.com/t/" in out and "127.0.0.1" not in out
    assert os.environ["WL_BASE_URL"] == "https://listen.example.com"


def test_a_variable_already_set_wins_over_dot_env(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WL_BASE_URL", "https://other.example.com")
    (tmp_path / ".env").write_text("WL_BASE_URL=https://listen.example.com\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: demo_cfg(cfg))
    assert cli.main(["create", "Sam"]) == 0
    assert "https://other.example.com/t/" in capsys.readouterr().out
