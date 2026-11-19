"""The colour picker in the library's Settings (page/library.js, theme/, docs/theming.md), in headless Chromium:
a swatch previews at once, Cancel goes back, Save keeps it across a reload, and dark mode follows the same seed."""

import json

import pytest
from test_library_timeline_page import browser, open_page, site  # noqa: F401

import theme

pytest.importorskip("playwright.sync_api")

PRIMARY = "getComputedStyle(document.documentElement).getPropertyValue('--md-sys-color-primary').trim()"
ACCENT_CARD = "getComputedStyle(document.querySelector('.lt-ch')).backgroundColor"


def rgb(hexcol):
    return "rgb({}, {}, {})".format(*(int(hexcol[i:i + 2], 16) for i in (1, 3, 5)))


def open_settings(pg):
    pg.click("#settings-link")
    pg.wait_for_function("document.querySelectorAll('.th-swatch').length > 0")


def test_a_swatch_previews_cancel_reverts_and_save_keeps_it(site, browser):  # noqa: F811
    page = open_page(site, browser, width=1000, height=900)
    pg = page.p
    first = theme.scheme(theme.DEFAULTS["seed"])["light"]["primary"]
    assert pg.evaluate(PRIMARY) == first
    open_settings(pg)
    assert pg.get_attribute(".th-swatch[aria-label=Amber]", "aria-checked") == "true" and pg.is_disabled("#th-save")
    pg.click(".th-swatch[aria-label=Blue]")
    blue = theme.scheme("#3a6ea5")["light"]
    pg.wait_for_function(f"{PRIMARY} === '{blue['primary']}'")
    blue_app = theme.scheme("#3a6ea5")["app"]["light"]
    assert pg.evaluate(ACCENT_CARD) == rgb(blue_app["lt-topic-1"])  # the cards follow: topic 1, the primary palette
    assert pg.evaluate("document.querySelectorAll('link[href^=\"/theme.css\"]').length") == 1  # the old sheet went
    assert not pg.is_disabled("#th-save") and pg.is_visible("#th-cancel")
    assert "theme" not in json.loads((site.listener / "settings.json").read_text())  # a preview saves nothing
    pg.click("#th-cancel")
    pg.wait_for_function(f"{PRIMARY} === '{first}'")
    pg.click(".th-swatch[aria-label=Teal]")
    pg.select_option("#th-variant", "vibrant")
    teal = theme.scheme("#24585b", "vibrant")["light"]
    pg.wait_for_function(f"{PRIMARY} === '{teal['primary']}'")
    pg.click("#th-save")
    pg.wait_for_function("document.getElementById('th-note').textContent === 'Saved.'")
    saved = json.loads((site.listener / "settings.json").read_text())["theme"]
    assert saved == {"seed": "#24585b", "variant": "vibrant", "contrast": "standard"}
    pg.reload()
    pg.wait_for_function("document.querySelectorAll('.lt-ch').length > 0")
    assert pg.evaluate(PRIMARY) == teal["primary"]
    pg.emulate_media(color_scheme="dark")  # the same seed's dark scheme
    pg.wait_for_function(f"{PRIMARY} === '{theme.scheme('#24585b', 'vibrant')['dark']['primary']}'")
    assert page.errors == []


def test_your_own_colour_and_high_contrast(site, browser):  # noqa: F811
    page = open_page(site, browser, width=1000, height=900)
    pg = page.p
    open_settings(pg)
    pg.evaluate("(() => { const i = document.getElementById('th-seed'); i.value = '#8a2be2'; i.dispatchEvent(new Event('input')); })()")
    pg.select_option("#th-contrast", "high")
    want = theme.scheme("#8a2be2", "tonal_spot", "high")["light"]["primary"]
    pg.wait_for_function(f"{PRIMARY} === '{want}'")
    assert all(pg.get_attribute(f".th-swatch:nth-child({i})", "aria-checked") == "false" for i in range(1, 9))
    pg.click("#th-default")
    pg.wait_for_function(f"{PRIMARY} === '{theme.scheme(theme.DEFAULTS['seed'])['light']['primary']}'")
    assert pg.is_disabled("#th-save") and page.errors == []


# --- after the review ---------------------------------------------------------------------------------------

def test_a_slow_earlier_preview_never_wins_over_the_latest(site, browser):  # noqa: F811
    page = open_page(site, browser, width=1000, height=900)
    pg = page.p
    held = []
    pg.route("**/theme.css?seed=*", lambda route: held.append(route))  # answered by hand, out of order
    open_settings(pg)
    pg.click(".th-swatch[aria-label=Violet]")
    pg.wait_for_timeout(300)
    pg.click(".th-swatch[aria-label=Green]")
    pg.wait_for_timeout(300)
    assert len(held) == 2
    held[1].continue_()  # Green arrives first...
    green = theme.scheme("#4f7942")["light"]["primary"]
    pg.wait_for_function(f"{PRIMARY} === '{green}'")
    held[0].continue_()  # ...then the earlier Violet, late
    pg.wait_for_timeout(500)
    assert pg.evaluate(PRIMARY) == green
    assert pg.evaluate("[...document.querySelectorAll('link[href^=\"/theme.css\"]')].map((l) => l.href)") == [
        site.url + "/theme.css?seed=%234f7942&variant=tonal_spot&contrast=standard"]
    assert page.errors == []


def test_the_swatches_keep_focus_and_move_with_the_arrow_keys(site, browser):  # noqa: F811
    page = open_page(site, browser, width=1000, height=900)
    pg = page.p
    open_settings(pg)
    tabbable = pg.eval_on_selector_all(".th-swatch", "els => els.filter((b) => b.tabIndex === 0).map((b) => b.getAttribute('aria-label'))")
    assert tabbable == ["Amber"]  # one tab stop: the checked one
    pg.focus(".th-swatch[aria-label=Amber]")
    pg.keyboard.press("ArrowRight")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Coral"
    assert pg.get_attribute(".th-swatch[aria-label=Coral]", "aria-checked") == "true"
    pg.keyboard.press("Enter")  # a pick by key: focus stays on the swatch
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Coral"
    pg.keyboard.press("ArrowLeft")
    pg.keyboard.press("ArrowLeft")  # round to the last
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Ochre"
    assert page.errors == []


def test_closing_settings_any_way_drops_an_unsaved_preview(site, browser):  # noqa: F811
    page = open_page(site, browser, width=1000, height=900)
    pg = page.p
    first = theme.scheme(theme.DEFAULTS["seed"])["light"]["primary"]
    open_settings(pg)
    pg.click(".th-swatch[aria-label=Teal]")
    pg.wait_for_function(f"{PRIMARY} !== '{first}'")
    pg.click("#s-save")  # the overnight settings' Save closes Settings
    pg.wait_for_function("!document.getElementById('library').classList.contains('lib-settings-open')")
    pg.wait_for_function(f"{PRIMARY} === '{first}'")
    assert page.errors == []
