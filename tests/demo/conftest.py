"""tests/demo: shared guards."""

import pytest


@pytest.fixture(autouse=True)
def no_real_dot_env(monkeypatch, tmp_path_factory):
    """`wl study` loads ROOT/.env (review 12); in tests ROOT is the repo, whose .env holds the real keys, and
    load_dotenv would leave them in os.environ for every later test. A test that wants a .env sets cli.ROOT."""
    import demo.cli
    monkeypatch.setattr(demo.cli, "ROOT", tmp_path_factory.mktemp("no-dot-env"))
