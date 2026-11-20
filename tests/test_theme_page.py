"""The theme in headless Chromium (page/theme.js, page/library.js, theme/, docs/theming.md).

The theme bar at the top left: a palette button opens the looks and modes; a look changes the whole page with one
click and is saved; Light, Dark and Auto
override the system or follow it; a failed save goes back; the last of fast clicks wins. The library's Settings
picker: a swatch previews at once, Cancel goes back, Save keeps it across a reload, and it follows the bar."""

import json

import pytest
from test_library_timeline_page import LATEST, STUB, browser, open_page, site  # noqa: F401

import theme

pytest.importorskip("playwright.sync_api")

PRIMARY = "getComputedStyle(document.documentElement).getPropertyValue('--md-sys-color-primary').trim()"
ACCENT_CARD = "getComputedStyle(document.querySelector('.lt-ch')).backgroundColor"
LOOK = {x["name"]: x for x in theme.LOOKS}


def look(name, contrast="standard"):
    return theme.scheme(LOOK[name]["seed"], LOOK[name]["variant"], contrast)


DEFAULT = theme.scheme(theme.DEFAULTS["seed"], theme.DEFAULTS["variant"])


def rgb(hexcol):
    return "rgb({}, {}, {})".format(*(int(hexcol[i:i + 2], 16) for i in (1, 3, 5)))


def saved_theme(site):
    f = site.listener / "settings.json"
    return json.loads(f.read_text()).get("theme") if f.exists() else None


def open_bar(site, browser, **kw):  # noqa: F811
    page = open_page(site, browser, width=1000, height=900, **kw)
    page.p.wait_for_function("document.querySelectorAll('#theme-bar .tb-look').length === 5")
    return page


def tap(pg, sel):
    """Click a control in the theme panel, opening the panel first if it is closed (the palette button)."""
    if pg.get_attribute("#theme-bar .tb-toggle", "aria-expanded") != "true":
        pg.click("#theme-bar .tb-toggle")
    pg.click(sel)


def open_panel(pg):
    if pg.get_attribute("#theme-bar .tb-toggle", "aria-expanded") != "true":
        pg.click("#theme-bar .tb-toggle")
    pg.wait_for_selector("#theme-panel", state="visible")


def open_settings(pg):
    pg.click("#settings-link")
    pg.wait_for_function("document.querySelectorAll('.th-swatch').length > 0")


def wait_saved(site, pg, **want):
    for _ in range(50):
        got = saved_theme(site) or {}
        if all(got.get(k) == v for k, v in want.items()):
            return got
        pg.wait_for_timeout(100)
    raise AssertionError(f"settings.json theme is {saved_theme(site)}, not {want}")


# --- the theme bar ----------------------------------------------------------------------------------------------

def test_a_look_changes_the_page_with_one_click_and_is_kept(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    assert pg.evaluate(PRIMARY) == DEFAULT["light"]["primary"]
    names = pg.eval_on_selector_all("#theme-bar .tb-look", "els => els.map((b) => b.getAttribute('aria-label'))")
    assert names == ["Forest", "Amber", "Ocean", "Rose", "Slate"]
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Forest]", "aria-checked") == "true"
    swatch = pg.eval_on_selector("#theme-bar .tb-look[aria-label=Ocean] span", "s => getComputedStyle(s).backgroundColor")
    assert swatch == rgb(look("Ocean")["light"]["primary"])  # a swatch shows its look's primary
    tap(pg, "#theme-bar .tb-look[aria-label=Ocean]")
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean')['light']['primary']}'")
    assert pg.evaluate(ACCENT_CARD) == rgb(look("Ocean")["app"]["light"]["lt-topic-1"])  # the cards follow
    assert wait_saved(site, pg, seed=LOOK["Ocean"]["seed"], variant=LOOK["Ocean"]["variant"])["mode"] == "auto"
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Ocean]", "aria-checked") == "true"
    assert pg.evaluate("document.querySelectorAll('link[href^=\"/theme.css\"]').length") == 1  # the old sheet went
    pg.reload()
    pg.wait_for_function("document.querySelectorAll('#theme-bar .tb-look').length === 5")
    assert pg.evaluate(PRIMARY) == look("Ocean")["light"]["primary"]  # from the first paint: the saved theme
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Ocean]", "aria-checked") == "true"
    assert page.errors == []


def test_light_dark_and_auto(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.emulate_media(color_scheme="light")
    assert pg.get_attribute("#theme-bar .tb-mode[aria-label=Auto]", "aria-checked") == "true"
    tap(pg, "#theme-bar .tb-mode[aria-label=Dark]")  # dark, though the system is light
    pg.wait_for_function(f"{PRIMARY} === '{DEFAULT['dark']['primary']}'")
    assert wait_saved(site, pg, mode="dark")
    swatch = pg.eval_on_selector("#theme-bar .tb-look[aria-label=Rose] span", "s => getComputedStyle(s).backgroundColor")
    assert swatch == rgb(look("Rose")["dark"]["primary"])  # in dark the swatches show the dark primaries
    tap(pg, "#theme-bar .tb-look[aria-label=Rose]")  # a look keeps the mode
    pg.wait_for_function(f"{PRIMARY} === '{look('Rose')['dark']['primary']}'")
    wait_saved(site, pg, seed=LOOK["Rose"]["seed"], mode="dark")
    pg.reload()
    pg.wait_for_function("document.querySelectorAll('#theme-bar .tb-look').length === 5")
    assert pg.evaluate(PRIMARY) == look("Rose")["dark"]["primary"]
    assert pg.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark"
    pg.emulate_media(color_scheme="dark")
    tap(pg, "#theme-bar .tb-mode[aria-label=Light]")  # light, though the system is dark
    pg.wait_for_function(f"{PRIMARY} === '{look('Rose')['light']['primary']}'")
    assert pg.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
    tap(pg, "#theme-bar .tb-mode[aria-label=Auto]")  # auto: the system's (dark here), and it follows a change
    pg.wait_for_function(f"{PRIMARY} === '{look('Rose')['dark']['primary']}'")
    pg.emulate_media(color_scheme="light")
    pg.wait_for_function(f"{PRIMARY} === '{look('Rose')['light']['primary']}'")
    wait_saved(site, pg, mode="auto")
    assert page.errors == []


def test_a_failed_save_goes_back_and_says_so(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.route("**/api/settings", lambda r: r.fulfill(status=400, content_type="application/json",
                                                    body=json.dumps({"detail": "No listener folder."})))
    tap(pg, "#theme-bar .tb-look[aria-label=Slate]")
    pg.wait_for_function("document.querySelector('#theme-bar .tb-note').textContent === 'No listener folder.'")
    pg.wait_for_function(f"{PRIMARY} === '{DEFAULT['light']['primary']}'")
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Forest]", "aria-checked") == "true"
    assert saved_theme(site) is None and page.errors == []


def test_the_last_of_fast_clicks_wins(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    for name in ("Ocean", "Rose", "Amber", "Slate"):
        tap(pg, f"#theme-bar .tb-look[aria-label={name}]")
    tap(pg, "#theme-bar .tb-mode[aria-label=Dark]")
    pg.wait_for_function(f"{PRIMARY} === '{look('Slate')['dark']['primary']}'")
    got = wait_saved(site, pg, seed=LOOK["Slate"]["seed"], mode="dark")
    assert got["variant"] == LOOK["Slate"]["variant"]
    pg.wait_for_timeout(400)
    assert pg.evaluate(PRIMARY) == look("Slate")["dark"]["primary"] and saved_theme(site) == got
    assert page.errors == []


def test_the_bar_by_keyboard(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    tab = pg.eval_on_selector_all("#theme-bar [role=radio]", "els => els.filter((b) => b.tabIndex === 0).map((b) => b.getAttribute('aria-label'))")
    assert tab == ["Forest", "Auto"]  # one tab stop per group: its checked button
    open_panel(pg)
    pg.focus("#theme-bar .tb-look[aria-label=Forest]")
    pg.keyboard.press("ArrowRight")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Amber"
    pg.wait_for_function(f"{PRIMARY} === '{look('Amber')['light']['primary']}'")
    pg.keyboard.press("End")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Slate"
    wait_saved(site, pg, seed=LOOK["Slate"]["seed"])
    pg.focus("#theme-bar .tb-mode[aria-label=Auto]")
    pg.keyboard.press("ArrowLeft")  # Light, Dark, Auto: left of Auto is Dark
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Dark"
    wait_saved(site, pg, mode="dark")
    assert page.errors == []


def test_the_bar_sits_above_the_librarys_heading(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    bar = pg.eval_on_selector("#theme-bar", "b => b.getBoundingClientRect().bottom")
    lib = pg.eval_on_selector("#library", "l => l.getBoundingClientRect().top")
    assert bar <= lib  # /library has a heading at the top left: the bar has a row of its own
    assert page.errors == []


# --- the Settings picker ----------------------------------------------------------------------------------------

def test_a_swatch_previews_cancel_reverts_and_save_keeps_it(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    first = DEFAULT["light"]["primary"]
    assert pg.evaluate(PRIMARY) == first
    open_settings(pg)
    assert pg.get_attribute(".th-swatch[aria-label=Forest]", "aria-checked") == "true" and pg.is_disabled("#th-save")
    pg.click(".th-swatch[aria-label=Ocean]")
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean')['light']['primary']}'")
    assert pg.evaluate(ACCENT_CARD) == rgb(look("Ocean")["app"]["light"]["lt-topic-1"])
    assert not pg.is_disabled("#th-save") and pg.is_visible("#th-cancel")
    assert pg.input_value("#th-variant") == LOOK["Ocean"]["variant"]  # a look is a seed and a style
    assert saved_theme(site) is None  # a preview saves nothing
    pg.click("#th-cancel")
    pg.wait_for_function(f"{PRIMARY} === '{first}'")
    pg.click(".th-swatch[aria-label=Forest]")
    pg.select_option("#th-variant", "vibrant")  # Forest, made vibrant: no longer one of the five
    want = theme.scheme(LOOK["Forest"]["seed"], "vibrant")
    pg.wait_for_function(f"{PRIMARY} === '{want['light']['primary']}'")
    assert pg.get_attribute(".th-swatch[aria-label=Forest]", "aria-checked") == "false"
    pg.click("#th-save")
    pg.wait_for_function("document.getElementById('th-note').textContent === 'Saved.'")
    assert saved_theme(site) == {"seed": LOOK["Forest"]["seed"], "variant": "vibrant", "contrast": "standard"}
    assert pg.eval_on_selector_all("#theme-bar .tb-look", "els => els.filter((b) => b.getAttribute('aria-checked') === 'true').length") == 0
    pg.reload()
    pg.wait_for_function("document.querySelectorAll('.lt-ch').length > 0")
    assert pg.evaluate(PRIMARY) == want["light"]["primary"]
    pg.emulate_media(color_scheme="dark")  # the same seed's dark scheme
    pg.wait_for_function(f"{PRIMARY} === '{want['dark']['primary']}'")
    assert page.errors == []


def test_your_own_colour_and_high_contrast(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    open_settings(pg)
    pg.evaluate("(() => { const i = document.getElementById('th-seed'); i.value = '#8a2be2'; i.dispatchEvent(new Event('input')); })()")
    pg.select_option("#th-contrast", "high")
    want = theme.scheme("#8a2be2", theme.DEFAULTS["variant"], "high")["light"]["primary"]
    pg.wait_for_function(f"{PRIMARY} === '{want}'")
    assert all(pg.get_attribute(f".th-swatch:nth-child({i})", "aria-checked") == "false" for i in range(1, 6))
    pg.click("#th-default")
    pg.wait_for_function(f"{PRIMARY} === '{DEFAULT['light']['primary']}'")
    assert pg.is_disabled("#th-save") and page.errors == []


def test_settings_follows_the_bar(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    open_settings(pg)
    tap(pg, "#theme-bar .tb-look[aria-label=Ocean]")  # the bar saves: Settings shows what is saved now
    pg.wait_for_function("document.querySelector('.th-swatch[aria-label=Ocean]').getAttribute('aria-checked') === 'true'")
    assert pg.input_value("#th-variant") == LOOK["Ocean"]["variant"] and pg.is_disabled("#th-save")
    pg.select_option("#th-contrast", "high")  # an unsaved preview in Settings...
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean', 'high')['light']['primary']}'")
    tap(pg, "#theme-bar .tb-mode[aria-label=Dark]")  # ...stays through light or dark, now in dark
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean', 'high')['dark']['primary']}'")
    assert pg.input_value("#th-contrast") == "high" and not pg.is_disabled("#th-save")
    assert wait_saved(site, pg, mode="dark").get("contrast", "standard") == "standard"  # the bar saved no preview
    tap(pg, "#theme-bar .tb-look[aria-label=Rose]")  # a look from the bar replaces the preview
    pg.wait_for_function(f"{PRIMARY} === '{look('Rose')['dark']['primary']}'")
    pg.wait_for_function("document.getElementById('th-save').disabled")
    assert pg.input_value("#th-contrast") == "standard"
    assert page.errors == []


def test_a_slow_earlier_preview_never_wins_over_the_latest(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    held = []
    pg.route("**/theme.css?seed=*", lambda route: held.append(route))  # answered by hand, out of order
    open_settings(pg)
    pg.click(".th-swatch[aria-label=Rose]")
    pg.wait_for_timeout(300)
    pg.click(".th-swatch[aria-label=Ocean]")
    pg.wait_for_timeout(300)
    assert len(held) == 2
    held[1].continue_()  # Ocean arrives first...
    ocean = look("Ocean")["light"]["primary"]
    pg.wait_for_function(f"{PRIMARY} === '{ocean}'")
    held[0].continue_()  # ...then the earlier Rose, late
    pg.wait_for_timeout(500)
    assert pg.evaluate(PRIMARY) == ocean
    assert pg.evaluate("[...document.querySelectorAll('link[href^=\"/theme.css\"]')].map((l) => l.href)") == [
        site.url + "/theme.css?seed=%232f6db5&variant=vibrant&contrast=standard&mode=auto"]
    assert page.errors == []


def test_the_swatches_keep_focus_and_move_with_the_arrow_keys(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    open_settings(pg)
    tabbable = pg.eval_on_selector_all(".th-swatch", "els => els.filter((b) => b.tabIndex === 0).map((b) => b.getAttribute('aria-label'))")
    assert tabbable == ["Forest"]  # one tab stop: the checked one
    pg.focus(".th-swatch[aria-label=Forest]")
    pg.keyboard.press("ArrowRight")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Amber"
    assert pg.get_attribute(".th-swatch[aria-label=Amber]", "aria-checked") == "true"
    pg.keyboard.press("Enter")  # a pick by key: focus stays on the swatch
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Amber"
    pg.keyboard.press("ArrowLeft")
    pg.keyboard.press("ArrowLeft")  # round to the last
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Slate"
    assert page.errors == []


def test_closing_settings_any_way_drops_an_unsaved_preview(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    first = DEFAULT["light"]["primary"]
    open_settings(pg)
    pg.click(".th-swatch[aria-label=Ocean]")
    pg.wait_for_function(f"{PRIMARY} !== '{first}'")
    pg.click("#s-save")  # the overnight settings' Save closes Settings
    pg.wait_for_function("!document.getElementById('library').classList.contains('lib-settings-open')")
    pg.wait_for_function(f"{PRIMARY} === '{first}'")
    assert page.errors == []


# --- after the review -------------------------------------------------------------------------------------------

def test_a_save_that_went_through_is_kept_when_a_later_one_fails(site, browser):  # noqa: F811
    """Ocean saved, then Rose failed: the page, the bar and the next click all keep Ocean (not the Forest before)."""
    page = open_bar(site, browser)
    pg = page.p
    seen = []

    def handle(route):
        seen.append(route)
        if len(seen) == 1:
            pg.wait_for_timeout(300)
            route.continue_()
        else:
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "Disk full."}))
    pg.route("**/api/settings", handle)
    tap(pg, "#theme-bar .tb-look[aria-label=Ocean]")
    tap(pg, "#theme-bar .tb-look[aria-label=Rose]")
    pg.wait_for_function("document.querySelector('#theme-bar .tb-note').textContent === 'Disk full.'")
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean')['light']['primary']}'")
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Ocean]", "aria-checked") == "true"
    pg.unroute("**/api/settings")
    tap(pg, "#theme-bar .tb-mode[aria-label=Dark]")
    got = wait_saved(site, pg, mode="dark")
    assert got["seed"] == LOOK["Ocean"]["seed"]
    assert page.errors == []


def test_the_error_note_is_seen_on_an_inline_bar(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.route("**/api/settings", lambda r: r.fulfill(status=400, content_type="application/json",
                                                    body=json.dumps({"detail": "Nope."})))
    tap(pg, "#theme-bar .tb-look[aria-label=Slate]")
    pg.wait_for_function("document.querySelector('#theme-bar .tb-note').textContent === 'Nope.'")
    box = pg.eval_on_selector("#theme-bar .tb-note", "n => n.getBoundingClientRect().toJSON()")
    panel = pg.eval_on_selector("#theme-panel", "p => p.getBoundingClientRect().toJSON()")
    toggle = pg.eval_on_selector("#theme-bar .tb-toggle", "b => b.getBoundingClientRect().toJSON()")
    assert toggle["bottom"] <= panel["top"] and box["height"] > 0  # in the panel, under the button, on screen
    assert panel["top"] <= box["top"] and box["bottom"] <= panel["bottom"] <= 900
    assert page.errors == []


def test_a_sheet_that_never_loads_leaves_the_page_and_bar_telling_the_truth(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.route("**/theme.css?seed=*", lambda r: r.abort())
    # Theme.apply alone (the picker's preview): the page stays as it was, and so does the bar
    assert pg.evaluate("Theme.apply({seed: '#2f6db5', variant: 'vibrant', contrast: 'standard', mode: 'dark'})") == "failed"
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Forest]", "aria-checked") == "true"
    assert pg.get_attribute("#theme-bar .tb-mode[aria-label=Auto]", "aria-checked") == "true"
    assert pg.evaluate("Theme.dark()") is False
    # a click whose preview never loads but whose save goes through: the saved sheet is loaded instead
    tap(pg, "#theme-bar .tb-look[aria-label=Ocean]")
    wait_saved(site, pg, seed=LOOK["Ocean"]["seed"])
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean')['light']['primary']}'")
    assert page.errors == []


def test_modified_arrow_keys_are_left_to_the_browser(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    open_panel(pg)
    pg.focus("#theme-bar .tb-look[aria-label=Forest]")
    for key in ("Alt+ArrowRight", "Meta+ArrowRight", "Control+End"):
        pg.keyboard.press(key)
    pg.wait_for_timeout(300)
    assert pg.get_attribute("#theme-bar .tb-look[aria-label=Forest]", "aria-checked") == "true"
    assert saved_theme(site) is None and page.errors == []


def test_on_the_orb_page_the_bar_clears_the_chapter_pages_back_link(site, browser):  # noqa: F811
    page = open_page(site, browser, path="/", width=1280, height=800)
    pg = page.p
    pg.wait_for_function("document.querySelectorAll('#theme-bar .tb-look').length === 5")
    assert pg.evaluate("getComputedStyle(document.getElementById('theme-bar')).position") == "absolute"  # home: floats
    pg.evaluate(f"location.hash = '#/chapter/{site.root.name}'")
    pg.wait_for_function("document.documentElement.classList.contains('lib-in-chapter')")
    pg.wait_for_function("document.getElementById('pg-back') && document.getElementById('pg-back').offsetParent")
    bar = pg.eval_on_selector("#theme-bar", "b => b.getBoundingClientRect().bottom")
    back = pg.eval_on_selector("#pg-back", "b => b.getBoundingClientRect().toJSON()")
    assert bar <= back["top"]
    hit = pg.evaluate(f"document.elementFromPoint({back['x'] + back['width'] / 2}, {back['y'] + back['height'] / 2}).closest('#pg-back') !== null")
    assert hit
    assert page.errors == []


def test_on_a_phone_the_orb_clears_the_bar(site, browser):  # noqa: F811
    page = open_page(site, browser, path="/", width=375, height=667)
    pg = page.p
    pg.wait_for_function("document.querySelectorAll('#theme-bar .tb-look').length === 5")
    pg.evaluate("window.scrollTo(0, 0)")
    bar = pg.eval_on_selector("#theme-bar", "b => b.getBoundingClientRect().bottom")
    orb = pg.eval_on_selector("#orb", "o => o.getBoundingClientRect().top")
    assert bar <= orb
    assert page.errors == []


def test_back_to_the_default_keeps_the_bars_mode(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.emulate_media(color_scheme="light")
    tap(pg, "#theme-bar .tb-mode[aria-label=Dark]")
    wait_saved(site, pg, mode="dark")
    open_settings(pg)
    pg.click(".th-swatch[aria-label=Ocean]")
    pg.wait_for_function(f"{PRIMARY} === '{look('Ocean')['dark']['primary']}'")
    pg.click("#th-default")
    pg.wait_for_function(f"{PRIMARY} === '{DEFAULT['dark']['primary']}'")  # still dark
    assert pg.is_disabled("#th-save") and page.errors == []


def test_swatches_follow_the_saved_contrast_and_the_systems_scheme(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.emulate_media(color_scheme="light")
    open_settings(pg)
    pg.select_option("#th-contrast", "high")
    pg.click("#th-save")
    pg.wait_for_function("document.getElementById('th-note').textContent === 'Saved.'")
    want = rgb(look("Ocean", "high")["light"]["primary"])
    pg.wait_for_function(f"getComputedStyle(document.querySelector('#theme-bar .tb-look[aria-label=Ocean] span')).backgroundColor === '{want}'")
    pg.emulate_media(color_scheme="dark")  # auto: the Settings swatches turn to the dark primaries too
    dark = rgb(look("Ocean", "high")["dark"]["primary"])
    pg.wait_for_function(f"getComputedStyle(document.querySelector('.th-swatch[aria-label=Ocean]')).backgroundColor === '{dark}'")
    assert page.errors == []


# --- behind the palette button (Ian, 2026-11-20) ----------------------------------------------------------------

def test_the_looks_hide_behind_a_palette_button(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    toggle = "#theme-bar .tb-toggle"
    assert pg.is_visible(toggle) and not pg.is_visible("#theme-panel")  # closed: only the button shows
    assert pg.get_attribute(toggle, "aria-expanded") == "false" and pg.get_attribute(toggle, "aria-label") == "Theme"
    assert not pg.is_visible("#theme-bar .tb-look[aria-label=Ocean]")
    pg.click(toggle)
    assert pg.is_visible("#theme-panel") and pg.get_attribute(toggle, "aria-expanded") == "true"
    pg.click("#theme-bar .tb-look[aria-label=Ocean]")
    pg.click("#theme-bar .tb-look[aria-label=Rose]")  # it stays open while you try them
    pg.wait_for_function(f"{PRIMARY} === '{look('Rose')['light']['primary']}'")
    assert pg.is_visible("#theme-panel")
    pg.keyboard.press("Escape")  # Escape closes it, and focus goes back to the button
    assert not pg.is_visible("#theme-panel") and pg.get_attribute(toggle, "aria-expanded") == "false"
    assert pg.evaluate("document.activeElement.classList.contains('tb-toggle')")
    pg.click(toggle)
    pg.mouse.click(700, 600)  # a click elsewhere closes it
    assert not pg.is_visible("#theme-panel")
    pg.click(toggle)
    pg.click(toggle)  # and so does the button
    assert not pg.is_visible("#theme-panel")
    wait_saved(site, pg, seed=LOOK["Rose"]["seed"])
    assert page.errors == []


def test_the_palette_button_by_keyboard(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.focus("#theme-bar .tb-toggle")
    pg.keyboard.press("Enter")
    assert pg.is_visible("#theme-panel")
    pg.keyboard.press("Tab")  # into the panel: the checked look
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Forest"
    pg.keyboard.press("Tab")
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Auto"
    pg.keyboard.press("Tab")  # focus leaves the bar: the panel closes
    assert not pg.evaluate("document.getElementById('theme-bar').contains(document.activeElement)")
    assert not pg.is_visible("#theme-panel")
    assert saved_theme(site) is None and page.errors == []


def test_a_failed_save_opens_the_panel_to_say_so(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p

    def slow_fail(route):
        pg.wait_for_timeout(400)
        route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "Disk full."}))
    pg.route("**/api/settings", slow_fail)
    tap(pg, "#theme-bar .tb-look[aria-label=Slate]")
    pg.keyboard.press("Escape")  # closed before the answer comes
    assert not pg.is_visible("#theme-panel")
    pg.wait_for_function("document.querySelector('#theme-bar .tb-note').textContent === 'Disk full.'")
    assert pg.is_visible("#theme-bar .tb-note")
    assert pg.get_attribute("#theme-bar .tb-status", "role") == "status"  # what a screen reader hears
    assert pg.text_content("#theme-bar .tb-status") == "Disk full."
    pg.wait_for_function(f"{PRIMARY} === '{DEFAULT['light']['primary']}'")
    assert page.errors == []


# --- after the review of the palette button --------------------------------------------------------------------

def test_the_live_region_is_rendered_before_a_failure(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    shown = pg.eval_on_selector("#theme-bar .tb-status", "s => getComputedStyle(s).display !== 'none' && "
                                "!s.closest('[hidden]')")
    assert shown and pg.text_content("#theme-bar .tb-status") == ""  # in the accessibility tree, empty, panel closed
    assert pg.get_attribute("#theme-bar .tb-note", "aria-live") is None  # the visible note is not a second voice
    assert page.errors == []


def test_closing_by_a_press_elsewhere_never_scrolls_the_page(site, browser):  # noqa: F811
    page = open_page(site, browser, width=1000, height=500)
    pg = page.p
    tap(pg, "#theme-bar .tb-look[aria-label=Ocean]")  # focus is now on a swatch in the panel
    pg.mouse.move(500, 300)
    pg.mouse.wheel(0, 600)
    pg.wait_for_function("window.scrollY > 100")
    y = pg.evaluate("window.scrollY")
    pg.evaluate("window.__hits = []; document.addEventListener('click', (e) => __hits.push(e.target.id || e.target.className), true)")
    pg.mouse.click(500, 300)
    assert pg.evaluate("window.scrollY") == y  # no jump back to the button
    assert not pg.is_visible("#theme-panel")
    assert pg.evaluate("__hits.length") == 1
    assert not pg.evaluate("document.activeElement.classList.contains('tb-toggle')")
    assert page.errors == []


def test_a_save_made_elsewhere_clears_an_old_failure(site, browser):  # noqa: F811
    page = open_bar(site, browser)
    pg = page.p
    pg.route("**/api/settings", lambda r: r.fulfill(status=500, content_type="application/json",
                                                    body=json.dumps({"detail": "Disk full."})))
    tap(pg, "#theme-bar .tb-look[aria-label=Slate]")
    pg.wait_for_function("document.querySelector('#theme-bar .tb-note').textContent === 'Disk full.'")
    pg.unroute("**/api/settings")
    pg.keyboard.press("Escape")
    open_settings(pg)
    pg.click(".th-swatch[aria-label=Ocean]")
    pg.click("#th-save")
    pg.wait_for_function("document.getElementById('th-note').textContent === 'Saved.'")
    assert pg.text_content("#theme-bar .tb-note") == "" and pg.text_content("#theme-bar .tb-status") == ""
    assert page.errors == []


def test_a_long_error_wraps_inside_a_phone_screen(site, browser):  # noqa: F811
    page = open_page(site, browser, width=320, height=640)
    pg = page.p
    long = "No listener folder at /Users/someone/Desktop/write-listen/listeners/a-rather-long-listener-name."
    pg.route("**/api/settings", lambda r: r.fulfill(status=400, content_type="application/json",
                                                    body=json.dumps({"detail": long})))
    tap(pg, "#theme-bar .tb-look[aria-label=Slate]")
    pg.wait_for_function(f"document.querySelector('#theme-bar .tb-note').textContent === {json.dumps(long)}")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    right = pg.eval_on_selector("#theme-bar .tb-note", "n => n.getBoundingClientRect().right")
    assert right <= 320
    assert page.errors == []


def test_on_the_episode_page_the_bars_keys_are_its_own(site, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1000, "height": 800})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script(STUB)
    pg.goto(f"{site.url}/episode/?run=/runs/{LATEST}")
    pg.wait_for_function("typeof ep !== 'undefined' && ep && document.querySelectorAll('#theme-bar .tb-look').length === 5")
    pg.evaluate("""window.__plays = 0; window.__steps = 0;
      audio.play = () => { __plays++; return Promise.resolve(); };
      const step = T.sentenceStep; T.sentenceStep = (...a) => { __steps++; return step(...a); };
      0;""")  # not the function: evaluate would call it
    pg.focus("#theme-bar .tb-toggle")
    pg.keyboard.press("Space")  # the button opens the palette; the chapter does not play
    assert pg.get_attribute("#theme-bar .tb-toggle", "aria-expanded") == "true"
    pg.focus("#theme-bar .tb-look[aria-label=Forest]")
    pg.keyboard.press("ArrowRight")  # a look is picked; the place in the chapter does not move
    assert pg.evaluate("document.activeElement.getAttribute('aria-label')") == "Amber"
    assert pg.evaluate("[__plays, __steps]") == [0, 0]
    pg.focus("body")
    pg.evaluate("document.activeElement.blur()")
    pg.keyboard.press("ArrowRight")  # outside the bar the shortcuts still work
    assert pg.evaluate("__steps") == 1
    assert errors == []
    ctx.close()
