"""The app's colour theme: Material 3's colour system from one seed colour (docs/theming.md).

Google's algorithm (material-color-utilities, here its Python port `materialyoucolor`) turns a seed colour into five
tonal palettes and, from them, every Material 3 colour role for a light and a dark scheme, contrast guaranteed between
each role and its "on" pair. This module makes that one CSS file, /theme.css, in three layers:

    reference   --md-ref-palette-<palette><tone>    the tonal palettes (primary, secondary, tertiary, neutral,
                                                    neutral-variant, error), tones 0-100
    system      --md-sys-color-<role>               the roles (primary, on-primary, primary-container, surface,
                                                    surface-container-low, on-surface-variant, outline...), light
                                                    by default, dark under prefers-color-scheme or data-theme="dark"
    app         --bg, --fg, --muted, --line, --accent, --card, --soft, the library's --lt-* ...
                                                    names the pages already use, each pointing at a role, so the
                                                    pages follow the theme without knowing it is Material

A listener's choice lives in their settings.json group `theme` ({"seed", "variant", "contrast"}); config.yaml
listener_settings.theme has the defaults. A page links /theme.css before its own styles and never sets a colour of
its own: tests/test_theme.py fails on a colour literal in page/ outside the allowed few.
"""

from __future__ import annotations

import re
from functools import lru_cache

from materialyoucolor.dynamiccolor.material_dynamic_colors import MaterialDynamicColors
from materialyoucolor.hct.hct import Hct
from materialyoucolor.scheme.scheme_content import SchemeContent
from materialyoucolor.scheme.scheme_expressive import SchemeExpressive
from materialyoucolor.scheme.scheme_fidelity import SchemeFidelity
from materialyoucolor.scheme.scheme_monochrome import SchemeMonochrome
from materialyoucolor.scheme.scheme_neutral import SchemeNeutral
from materialyoucolor.scheme.scheme_tonal_spot import SchemeTonalSpot
from materialyoucolor.scheme.scheme_vibrant import SchemeVibrant
from materialyoucolor.utils.color_utils import hex_from_argb

SPEC = "2025"  # Material 3's current colour spec (the library's default; "2021" is the first M3)
VARIANTS = {  # what the listener picks between; tonal_spot is Android's default
    "tonal_spot": SchemeTonalSpot, "vibrant": SchemeVibrant, "expressive": SchemeExpressive,
    "neutral": SchemeNeutral, "fidelity": SchemeFidelity, "content": SchemeContent, "monochrome": SchemeMonochrome,
}
CONTRASTS = {"standard": 0.0, "medium": 0.5, "high": 1.0}  # Material 3's contrast levels
PALETTES = ("primary", "secondary", "tertiary", "neutral", "neutral_variant", "error")
TONES = (0, 4, 6, 10, 12, 17, 20, 22, 24, 30, 40, 50, 60, 70, 80, 87, 90, 92, 94, 95, 96, 98, 99, 100)
SKIP = {"primaryPaletteKeyColor", "secondaryPaletteKeyColor", "tertiaryPaletteKeyColor", "neutralPaletteKeyColor",
        "neutralVariantPaletteKeyColor", "errorPaletteKeyColor"}  # inputs to the algorithm, not roles to style with
HEX = re.compile(r"#?([0-9a-fA-F]{6})")
DEFAULTS = {"seed": "#d9822b", "variant": "tonal_spot", "contrast": "standard"}  # also config.yaml
PRESETS = [  # the picker's swatches: the app's first orange, the library sketches' three, and a few more
    {"name": "Amber", "seed": "#d9822b"}, {"name": "Coral", "seed": "#c8553d"}, {"name": "Rose", "seed": "#b5485d"},
    {"name": "Violet", "seed": "#6b5b95"}, {"name": "Blue", "seed": "#3a6ea5"}, {"name": "Teal", "seed": "#24585b"},
    {"name": "Green", "seed": "#4f7942"}, {"name": "Ochre", "seed": "#b7791f"},
]

# The app's own names for the roles: what page/*.html and page/library.js use. A new page should use the roles
# (or these) and never a colour of its own.
ALIASES = {
    "bg": "surface", "fg": "on-surface", "muted": "on-surface-variant",
    "line": "outline-variant",  # dividers and decoration (Material: outline-variant)
    "edge": "outline",          # the edge of a control that must be seen: a text box, a select (Material: outline)
    "accent": "primary", "on-accent": "on-primary", "card": "surface-container-low",
    "soft": "secondary-container", "on-soft": "on-secondary-container",  # a highlight: its text is --on-soft
    "lt-surface": "surface-container-lowest", "lt-accent-text": "primary", "lt-became": "primary-container",
    "lt-mention": "tertiary",
}
# In the dark scheme a few names point at other roles: raised surfaces are lighter than the page there (the
# "lowest" container is black in the 2025 spec), so cards and rows take a higher container.
ALIASES_DARK = {"card": "surface-container", "lt-surface": "surface-container-high"}
# The library's topic cards are app colours made from the palettes at fixed tones (Material's way to add a colour of
# your own): rich in light, deep in dark, for every style and seed. The containers would not do: under the 2025 spec
# some are pale in dark (tonal spot's tertiary container is tone ~90). A tone gap of 60 guarantees AA contrast.
TOPICS = {"lt-topic-1": "primary", "lt-topic-2": "tertiary", "lt-topic-3": "secondary"}
TOPIC_TONES = {"light": (40, 100), "dark": (30, 90)}  # (the card, its text)


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _hex(argb: int) -> str:
    return "#" + hex_from_argb(argb).lstrip("#")[:6].lower()  # the library gives #RRGGBBAA


def _seed(value) -> str | None:
    m = HEX.fullmatch(value.strip()) if isinstance(value, str) else None
    return f"#{m.group(1).lower()}" if m else None


def clean(settings: dict | None, defaults: dict | None = None) -> dict:
    """A theme setting made safe: a #rrggbb seed, a known variant and contrast; anything else falls back to
    `defaults` (config.yaml's, when the caller has them) or DEFAULTS. Never raises, whatever settings.json holds."""
    base = DEFAULTS if defaults is None else clean(defaults)
    got = settings if isinstance(settings, dict) else {}
    return {"seed": _seed(got.get("seed")) or base["seed"],
            "variant": got.get("variant") if isinstance(got.get("variant"), str) and got["variant"] in VARIANTS
            else base["variant"],
            "contrast": got.get("contrast") if isinstance(got.get("contrast"), str) and got["contrast"] in CONTRASTS
            else base["contrast"]}


def check(key: str, value) -> str:
    """One theme setting from the listener, checked (profiles.save_settings): raises ValueError with a plain reason."""
    if key == "seed":
        seed = _seed(value)
        if not seed:
            raise ValueError("The colour must be a hex colour like #d9822b.")
        return seed
    if key == "variant":
        if not isinstance(value, str) or value not in VARIANTS:
            raise ValueError(f"The style must be one of {', '.join(VARIANTS)}.")
        return value
    if key == "contrast":
        if not isinstance(value, str) or value not in CONTRASTS:
            raise ValueError(f"The contrast must be one of {', '.join(CONTRASTS)}.")
        return value
    raise ValueError(f"Unknown theme setting {key}.")


@lru_cache(maxsize=64)
def scheme(seed: str, variant: str = "tonal_spot", contrast: str = "standard") -> dict:
    """{"settings", "spec": the colour spec used, "light"/"dark": {role: "#rrggbb"}, "palettes": {"light"/"dark":
    {palette: {tone: "#rrggbb"}}}, "app": {"light"/"dark": {name: "#rrggbb"}} (the topic colours)}.
    The library falls back to the 2021 spec for fidelity, content and monochrome; "spec" says which was used."""
    s = clean({"seed": seed, "variant": variant, "contrast": contrast})
    source = Hct.from_int(int("ff" + s["seed"][1:], 16))
    cls, level = VARIANTS[s["variant"]], CONTRASTS[s["contrast"]]
    out: dict = {"settings": s, "palettes": {}, "app": {}}
    for mode, dark in (("light", False), ("dark", True)):
        sch = cls(source, dark, level, spec_version=SPEC)
        spec = str(getattr(sch, "spec_version", SPEC))
        out["spec"] = spec
        roles = [c for c in MaterialDynamicColors(spec).all_colors if c.name not in SKIP]
        out[mode] = {_kebab(c.name): _hex(c.get_argb(sch)) for c in roles}
        pal = {p: getattr(sch, f"{p}_palette") for p in PALETTES}
        out["palettes"][mode] = {p.replace("_", "-"): {t: _hex(pal[p].tone(t)) for t in TONES} for p in PALETTES}
        card, text = TOPIC_TONES[mode]
        out["app"][mode] = {}
        for name, p in TOPICS.items():
            out["app"][mode][name] = _hex(pal[p].tone(card))
            out["app"][mode][name.replace("lt-topic", "lt-on-topic")] = _hex(pal[p].tone(text))
    return out


def _block(selector: str, decls: dict[str, str], indent: str = "") -> str:
    body = "\n".join(f"{indent}  {k}: {v};" for k, v in decls.items())
    return f"{indent}{selector} {{\n{body}\n{indent}}}"


def css(settings: dict | None = None, defaults: dict | None = None) -> str:
    """/theme.css for a listener's theme setting."""
    s = clean(settings, defaults)
    t = scheme(s["seed"], s["variant"], s["contrast"])

    def layer(mode: str) -> dict[str, str]:
        ref = {f"--md-ref-palette-{p}{tone}": v for p, tones in t["palettes"][mode].items() for tone, v in tones.items()}
        sys = {f"--md-sys-color-{k}": v for k, v in t[mode].items()}
        app = {f"--{k}": v for k, v in t["app"][mode].items()}
        names = ALIASES if mode == "light" else ALIASES_DARK
        return {**ref, **sys, **{f"--{k}": f"var(--md-sys-color-{v})" for k, v in names.items()}, **app}

    dark = {"color-scheme": "dark", **layer("dark")}
    head = (f"/* write-listen theme: Material 3 ({t['spec']} colour spec), seed {s['seed']}, {s['variant']}, "
            f"{s['contrast']} contrast. Generated by theme/__init__.py; see docs/theming.md. */")
    return "\n\n".join([
        head,
        _block(":root", {"color-scheme": "light dark", **layer("light")}),
        "@media (prefers-color-scheme: dark) {\n" + _block(':root:not([data-theme="light"])', dark, "  ") + "\n}",
        _block(':root[data-theme="dark"]', dark),
        _block(':root[data-theme="light"]', {"color-scheme": "light"}),
    ]) + "\n"


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast between two #rrggbb colours."""
    def lum(h: str) -> float:
        out = []
        for i in (1, 3, 5):
            c = int(h[i:i + 2], 16) / 255
            out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
