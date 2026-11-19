# Theming: Material 3 colour from one seed

Ian, 2026-11-18: "a foundation of colour theming with Google's Material 3 colour system... the user can select a
colour and the theming tool can cascade those variables through the front end." This is that foundation: what it
is, how to use it in new UI, and how to extend it.

## How it works

One seed colour (the listener's, from Settings) goes through Google's colour algorithm (material-color-utilities,
here its Python port `materialyoucolor` 3.0.4, Material's **2025 colour spec**; the library falls back to the 2021
spec for the fidelity, content and monochrome styles, and the stylesheet's first line says which spec it used). The algorithm works in HCT
(hue, chroma, tone: a colour space where tone matches how light a colour looks), builds five **tonal palettes** from
the seed, and picks every **colour role** from them for a light and a dark scheme, with contrast between each role
and its "on" pair guaranteed. The server turns that into one stylesheet:

```
GET /theme.css            the active listener's theme (settings.json "theme")
GET /theme.css?seed=%233a6ea5&variant=vibrant&contrast=high  a preview, saved nowhere (the seed's # written
                          %23, or left out: seed=3a6ea5; a bare # would start the URL's fragment)
GET /api/theme            the same as data, with the choices (variants, contrasts, preset seeds)
POST /api/settings {"theme": {"seed", "variant", "contrast"}}    save it
```

Code: `theme/__init__.py` (the scheme and the CSS), `theme/routes.py`; the defaults in `config.yaml`
`listener_settings.theme`; the picker in `page/library.js` (Settings, "Colours").

## The three layers

| Layer | Names | What it is |
| --- | --- | --- |
| Reference | `--md-ref-palette-<palette><tone>` | The tonal palettes: `primary`, `secondary`, `tertiary`, `neutral`, `neutral-variant`, `error`, tones 0 (black) to 100 (white). Each scheme has its own (under the 2025 spec the dark palettes differ), so the dark values replace the light ones in dark. For rare cases; prefer roles. |
| System | `--md-sys-color-<role>` | Material 3's roles. Light by default; dark under `prefers-color-scheme: dark`, or forced with `<html data-theme="dark">` / `"light"`. |
| App | `--bg`, `--fg`, `--muted`, `--line`, `--edge`, `--accent`, `--on-accent`, `--card`, `--soft`, `--on-soft`, `--lt-*` | The names the pages use, each pointing at a role (`theme.ALIASES`; a few point at another role in dark, `theme.ALIASES_DARK`), and a few app colours made from the palettes (`theme.TOPICS`: the library's topic cards). |

The roles, briefly (Material's guide: https://m3.material.io/styles/color/roles):

- **primary**, **secondary**, **tertiary**, **error**: accents in falling emphasis; each has **on-X** (text and icons on
  it), **X-container** (a softer fill) and **on-X-container**. Fixed variants (`primary-fixed`...) are meant to
  change little between light and dark, but under the 2025 spec they are not identical: don't rely on them for a
  colour that must not change.
- **surface** (the page), **surface-container-lowest/low/(plain)/high/highest** (cards and panels, from least to most
  emphasis), **surface-dim/bright**; **on-surface** (text), **on-surface-variant** (secondary text).
- **outline** (borders that must be seen: the app's `--edge`), **outline-variant** (dividers: `--line`); **inverse-surface/on-surface/primary**
  (snackbars, tooltips); **shadow**, **scrim**.

## Rules for new UI

1. **Never write a colour.** Use a role (`var(--md-sys-color-primary)`) or an app name (`var(--accent)`).
   `tests/test_theme.py` fails on a hex, `rgb()`, `hsl()`, `white` or `black` in a CSS value anywhere in `page/` or
   `episode/page/`. (For a genuine exception, a data colour like a swatch, set it from data in script, as the picker
   does with `--sw`.)
2. **Pair X with on-X.** Text on `primary` is `on-primary`; on `tertiary-container` it is `on-tertiary-container`;
   on a `--soft` highlight it is `--on-soft`, never plain `--fg` (at high contrast that fails). The pairs are what
   the contrast guarantee covers, tested for every style and contrast level at 4.5:1 or more.
   Borders a control needs to be seen by use `--edge` (outline, 3:1 or more); dividers use `--line`.
3. **Surfaces by emphasis, not by colour.** A row or card on the page: a surface container. The library uses
   `surface-container-lowest` in light (white on cream) and `surface-container-high` in dark (raised above the page):
   that is an app name with a dark override (`--lt-surface`). Do the same rather than branching in page CSS.
4. **Translucency from roles**: `color-mix(in srgb, var(--md-sys-color-shadow) 15%, transparent)`, never `rgba()`.
5. **Link the theme first.** Every page's `<head>` has `<link rel="stylesheet" href="/theme.css" id="theme-css">`
   before its `<style>` (tested); `page/library.js` adds it if a host page forgot. A page sets no `:root` colours.
6. **A new app name** goes in `theme.ALIASES` (and `ALIASES_DARK` if dark needs another role), with a line in the
   readability test's pairs (`APP_PAIRS` in tests/test_theme.py) for each pair it is used in.
7. **A colour of the app's own** (like the topic cards, which must stay rich in light and deep in dark for every
   style) comes from a palette at fixed tones (`theme.TOPICS`, `TOPIC_TONES`), Material's way to add a custom colour:
   a tone gap of 60 between a fill and its text guarantees AA contrast. Don't assume a role's tone: under the 2025
   spec the dark containers are pale for some styles (tonal spot's tertiary container is tone ~90).
8. **Focus rings on a coloured fill** use that fill's on-colour (the library's cards do), not `--fg`.

## Choices for the listener

- **Seed**: any colour (`<input type=color>`), or a preset swatch (`theme.PRESETS`).
- **Style** (`variant`): `tonal_spot` (Material's default: calm, the seed softened), `vibrant`, `expressive`,
  `neutral`, `fidelity` and `content` (both keep close to the exact seed), `monochrome`.
- **Contrast**: `standard`, `medium`, `high` (Material's levels 0, 0.5, 1).

Choosing previews at once (the stylesheet link is swapped for a preview URL, the old one dropped once the new one
has loaded, so nothing flashes); Save writes `settings.json`; Cancel, or closing Settings, goes back.

## Extending it

- **Light or dark by choice**: set `document.documentElement.dataset.theme = "dark" | "light"` (the CSS already
  handles it); store it as another `theme` setting if the listener should pick it.
- **A colour from an image** (a chapter's artwork): `materialyoucolor` has quantize and score
  (`materialyoucolor.quantize`, `materialyoucolor.score`), which give the best seed in an image; pass it to
  `theme.css({"seed": ...})`.
- **Other surfaces** (an app, an email): `theme.scheme(seed, variant, contrast)` returns every role as hex for both
  schemes; use the same role names there.
- **Typography, shape, motion** are other Material 3 token sets; this foundation is colour only.
- **The episode page on its own** (`python -m http.server`, no /theme.css) falls back to
  `episode/page/theme.default.css`, the default theme; after changing the engine, remake it with
  `.venv/bin/python -m theme > episode/page/theme.default.css` (a test checks it matches).
