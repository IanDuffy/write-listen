"""The colour theme (theme/, docs/theming.md): Material 3 roles from a seed, /theme.css, the listener's setting, and
the rule that keeps it the foundation: no page sets a colour of its own."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
import theme
from overnight import profiles
from stages.common import ROOT, StageError

PAIRS = [("on-primary", "primary"), ("on-primary-container", "primary-container"), ("on-secondary", "secondary"),
         ("on-secondary-container", "secondary-container"), ("on-tertiary", "tertiary"),
         ("on-tertiary-container", "tertiary-container"), ("on-error", "error"), ("on-surface", "surface"),
         ("on-surface-variant", "surface"), ("on-surface", "surface-container-high"), ("inverse-on-surface", "inverse-surface")]
SEEDS = ["#d9822b", "#24585b", "#6b5b95", "#ffeb3b", "#000000"]


def test_a_seed_gives_every_role_in_light_and_dark():
    t = theme.scheme("#d9822b")
    for mode in ("light", "dark"):
        roles = t[mode]
        assert {"primary", "on-primary", "primary-container", "secondary", "tertiary", "error", "surface", "on-surface",
                "on-surface-variant", "outline", "outline-variant", "surface-container-lowest", "surface-container-low",
                "surface-container", "surface-container-high", "surface-container-highest", "inverse-surface",
                "shadow", "scrim"} <= set(roles)
        assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in roles.values())
        assert not any(k.endswith("palette-key-color") for k in roles)
    assert t["light"]["surface"] != t["dark"]["surface"]
    for mode in ("light", "dark"):  # each scheme has its own palettes (they differ under the 2025 spec)
        assert set(t["palettes"][mode]) == {"primary", "secondary", "tertiary", "neutral", "neutral-variant", "error"}
        assert t["palettes"][mode]["primary"][0] == "#000000" and t["palettes"][mode]["primary"][100] == "#ffffff"
    assert theme.scheme("#D9822B") == t  # one theme for one colour, however it is written


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", list(theme.VARIANTS))
def test_every_role_and_its_on_colour_are_readable(seed, variant):
    """Material 3's promise: each 'on' role is readable on its role (WCAG AA, 4.5:1), in both schemes."""
    t = theme.scheme(seed, variant)
    for mode in ("light", "dark"):
        for on, bg in PAIRS:
            assert theme.contrast_ratio(t[mode][on], t[mode][bg]) >= 4.5, (seed, variant, mode, on, bg)


APP_PAIRS = [("fg", "bg"), ("muted", "bg"), ("accent", "bg"), ("on-accent", "accent"), ("fg", "card"), ("muted", "card"),
             ("fg", "lt-surface"), ("lt-accent-text", "lt-surface"), ("on-soft", "soft"),
             ("lt-on-topic-1", "lt-topic-1"), ("lt-on-topic-2", "lt-topic-2"), ("lt-on-topic-3", "lt-topic-3")]


def app_colours(t, mode):
    names = {**theme.ALIASES, **(theme.ALIASES_DARK if mode == "dark" else {})}
    return {**{k: t[mode][v] for k, v in names.items()}, **t["app"][mode]}


@pytest.mark.parametrize("contrast", list(theme.CONTRASTS))
@pytest.mark.parametrize("variant", list(theme.VARIANTS))
def test_the_apps_names_stay_readable_at_every_style_and_contrast(variant, contrast):
    """What the pages actually pair (text on the page, on rows and cards, a highlight's text, each topic card's
    text), for every style and contrast a listener can pick, in both schemes. (Review: the read-along put plain text
    on --soft, unreadable at high contrast; it now uses --on-soft.)"""
    for seed in SEEDS[:3]:
        t = theme.scheme(seed, variant, contrast)
        for mode in ("light", "dark"):
            c = app_colours(t, mode)
            for fg, bg in APP_PAIRS:
                assert theme.contrast_ratio(c[fg], c[bg]) >= 4.5, (seed, variant, contrast, mode, fg, bg)
            assert theme.contrast_ratio(c["edge"], c["bg"]) >= 3, (seed, variant, contrast, mode)  # a control's edge


@pytest.mark.parametrize("variant", list(theme.VARIANTS))
def test_topic_cards_are_rich_in_light_and_deep_in_dark(variant):
    """Review: the dark containers were pale under the 2025 spec (a pale yellow card in a dark strip)."""
    for seed in SEEDS:
        t = theme.scheme(seed, variant)
        for i in (1, 2, 3):
            assert theme.contrast_ratio(t["app"]["light"][f"lt-topic-{i}"], "#ffffff") >= 4.5  # a dark tone, not pale
            assert theme.contrast_ratio(t["app"]["dark"][f"lt-topic-{i}"], "#000000") < 3  # deep in dark
            assert theme.contrast_ratio(t["app"]["dark"][f"lt-topic-{i}"], t["dark"]["surface"]) >= 1.5  # seen


def test_higher_contrast_is_higher():
    std = theme.scheme("#d9822b", contrast="standard")["light"]
    high = theme.scheme("#d9822b", contrast="high")["light"]
    assert theme.contrast_ratio(high["on-surface-variant"], high["surface"]) > \
        theme.contrast_ratio(std["on-surface-variant"], std["surface"])


def test_css_has_the_three_layers_and_both_schemes():
    css = theme.css({"seed": "#24585b"})
    assert "seed #24585b, tonal_spot, standard contrast" in css
    root = css.split("@media")[0]
    assert "--md-ref-palette-primary40:" in root and "--md-sys-color-primary:" in root
    for name, role in theme.ALIASES.items():
        assert f"--{name}: var(--md-sys-color-{role});" in root
    t0 = theme.scheme("#24585b")
    assert f"--lt-topic-1: {t0['app']['light']['lt-topic-1']};" in root
    dark = css.split("@media (prefers-color-scheme: dark) {")[1].split("\n}\n")[0]
    assert ':root:not([data-theme="light"])' in dark and "color-scheme: dark" in dark
    t = theme.scheme("#24585b")
    assert f"--md-sys-color-surface: {t['dark']['surface']};" in dark
    assert f"--lt-topic-1: {t['app']['dark']['lt-topic-1']};" in dark
    assert f"--md-ref-palette-primary80: {t['palettes']['dark']['primary'][80]};" in dark  # the dark palettes in dark
    assert "--card: var(--md-sys-color-surface-container);" in dark
    assert ':root[data-theme="dark"]' in css  # an explicit choice beats the system's
    roles = set(t["light"])
    assert all(v in roles for v in [*theme.ALIASES.values(), *theme.ALIASES_DARK.values()])


def test_the_header_names_the_spec_actually_used():
    """Review: the library uses the 2021 spec for fidelity, content and monochrome whatever is asked."""
    assert "(2025 colour spec)" in theme.css({"variant": "tonal_spot"})
    for v in ("fidelity", "content", "monochrome"):
        assert theme.scheme("#d9822b", v)["spec"] == "2021" and "(2021 colour spec)" in theme.css({"variant": v})


def test_a_bad_setting_falls_back_and_a_check_says_why():
    assert theme.clean({"seed": "nope", "variant": "loud", "contrast": 9}) == theme.DEFAULTS
    assert theme.clean({"variant": ["vibrant"], "contrast": {}, "seed": 12}) == theme.DEFAULTS  # never a TypeError
    assert theme.clean("junk") == theme.DEFAULTS
    assert theme.clean({}, defaults={"seed": "#3a6ea5"})["seed"] == "#3a6ea5"
    assert theme.clean({"seed": "24585B"})["seed"] == "#24585b"
    assert theme.check("seed", " #24585B ") == "#24585b"
    for k, v in (("seed", "red"), ("seed", "#12345"), ("variant", "loud"), ("contrast", "max"), ("size", 1),
                 ("variant", ["x"]), ("contrast", {}), ("seed", None)):
        with pytest.raises(ValueError):
            theme.check(k, v)


# --- the listener's setting and the routes ------------------------------------------------------------------

@pytest.fixture
def client(cfg, own_listeners):
    cfg.pop("listener_dir", None)
    lis = profiles.create("ian")
    profiles.set_active("ian")
    return TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False)), lis


def test_theme_css_follows_the_listeners_setting(client, cfg):
    c, lis = client
    r = c.get("/theme.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css") and "no-cache" in r.headers["cache-control"]
    assert f"seed {theme.DEFAULTS['seed']}" in r.text
    got = c.post("/api/settings", json={"theme": {"seed": "#3A6EA5", "variant": "vibrant", "contrast": "medium"}})
    assert got.status_code == 200 and got.json()["settings"]["theme"] == {"seed": "#3a6ea5", "variant": "vibrant",
                                                                        "contrast": "medium"}
    assert json.loads((lis / "settings.json").read_text())["theme"]["seed"] == "#3a6ea5"
    assert "seed #3a6ea5, vibrant, medium contrast" in c.get("/theme.css").text
    preview = c.get("/theme.css", params={"seed": "#4f7942"}).text  # a preview: not saved
    assert "seed #4f7942, vibrant, medium contrast" in preview
    assert json.loads((lis / "settings.json").read_text())["theme"]["seed"] == "#3a6ea5"
    assert c.get("/theme.css", params={"seed": "green"}).status_code == 400
    assert c.post("/api/settings", json={"theme": {"seed": "green"}}).status_code == 400
    assert c.post("/api/settings", json={"theme": {"variant": "loud"}}).status_code == 400
    assert c.post("/api/settings", json={"theme": {"contrast": "standard"}}).status_code == 200  # not a number
    assert c.post("/api/settings", json={"theme": {"variant": ["x"]}}).status_code == 400  # not a 500
    assert c.get("/theme.css", params={"seed": "3a6ea5"}).status_code == 200  # the # may be left out of a URL


def test_a_hand_edited_bad_theme_still_serves_colours(client):
    c, lis = client
    (lis / "settings.json").write_text(json.dumps({"theme": {"variant": ["vibrant"], "seed": {}}}))
    r = c.get("/theme.css")
    assert r.status_code == 200 and f"seed {theme.DEFAULTS['seed']}" in r.text


def test_the_defaults_come_from_config(cfg, own_listeners):
    cfg.pop("listener_dir", None)
    cfg["listener_settings"]["theme"]["seed"] = "#3a6ea5"
    profiles.create("ian")
    profiles.set_active("ian")
    c = TestClient(server.create_app(server.Pipeline(), cfg, remember_on_start=False))
    d = c.get("/api/theme").json()
    assert d["defaults"]["seed"] == "#3a6ea5" and d["settings"]["seed"] == "#3a6ea5"
    assert "seed #3a6ea5" in c.get("/theme.css").text


def test_api_theme_has_the_choices(client):
    c, _ = client
    d = c.get("/api/theme").json()
    assert d["settings"] == theme.DEFAULTS and d["defaults"] == theme.DEFAULTS and d["spec"] == "2025"
    assert d["variants"][0] == "tonal_spot" and d["contrasts"] == ["standard", "medium", "high"]
    assert {p["seed"] for p in d["presets"]} >= {"#d9822b", "#24585b"} and d["light"]["primary"].startswith("#")


def test_save_settings_checks_the_theme(tmp_path, cfg):
    lis = tmp_path / "l"
    lis.mkdir()
    with pytest.raises(StageError, match="hex colour"):
        profiles.save_settings(cfg, {"theme": {"seed": "blue"}}, listener=lis)
    assert profiles.save_settings(cfg, {"theme": {"seed": "#ABCDEF"}}, listener=lis)["theme"]["seed"] == "#abcdef"


# --- the foundation: no page sets a colour of its own -----------------------------------------------------------

PAGES = sorted([*ROOT.glob("page/*.html"), *ROOT.glob("page/*.js"), *ROOT.glob("episode/page/*.html")])
COLOUR = re.compile(r":\s*[^;{}\n]*?(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|\b(?:white|black)\b)")


def test_no_page_sets_a_colour_of_its_own():
    """Every colour comes from /theme.css (a role, or the app's name for one). A colour literal in a CSS value in a
    page, or in its script's styles, fails here: use --md-sys-color-<role> or an alias (docs/theming.md)."""
    found = []
    for p in PAGES:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            m = COLOUR.search(line)
            if m and "href=" not in line and "theme-ok" not in line:
                found.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert found == []


def test_every_page_links_the_theme_before_its_own_styles():
    for p in sorted([*ROOT.glob("page/*.html"), *ROOT.glob("episode/page/*.html")]):
        text = p.read_text(encoding="utf-8")
        assert '<link rel="stylesheet" href="/theme.css"' in text, p
        assert text.index("/theme.css") < text.index("<style>"), p
    assert 'link[href^="/theme.css"]' in (ROOT / "page" / "library.js").read_text()  # and the library makes sure


def test_the_episode_pages_fallback_is_the_default_theme():
    """Run on a plain file server (python -m http.server) there is no /theme.css; the page falls back to this copy,
    which `python -m theme > episode/page/theme.default.css` makes. It must stay what the engine makes."""
    assert (ROOT / "episode" / "page" / "theme.default.css").read_text(encoding="utf-8") == theme.css(None)
    page = (ROOT / "episode" / "page" / "index.html").read_text(encoding="utf-8")
    assert "this.href = 'theme.default.css'" in page
