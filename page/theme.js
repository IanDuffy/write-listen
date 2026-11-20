// The theme bar and window.Theme (theme/, docs/theming.md; Ian, 2026-11-19: "a series of 5 different colors...
// a few color buttons at the top left that I can see and test").
//
// Every page loads this after /theme.css. It puts a palette button at the top left (over the page, or in a row of its
// own where the page says <body data-theme-bar="inline">, for a heading that sits there; Ian, 2026-11-20: "hide those
// color toggle buttons behind an icon button"). The button opens a panel with the five looks (theme.LOOKS, each a seed
// and a style) and Light / Dark / Auto; it stays open while you try them, and closes on the button, Escape, a click
// elsewhere or focus moving away. A click changes the whole page at once and saves it to the listener's
// settings.json, so every page and every reload keeps it. window.Theme is the one place that swaps the stylesheet:
// the library's Settings picker previews through it too.
//
//   Theme.apply(t)      show theme t ({seed, variant, contrast, mode}) now, saving nothing; null: the saved one.
//                       Resolves "loaded", "superseded" (a later apply won) or "failed" (the colours stay as they were)
//   Theme.dark()        whether the page is showing the dark scheme
//   Theme.saved         the saved theme (after /api/theme has answered), or null
//   Theme.subscribe(fn) fn(saved, change) after the bar saves a change; the library's picker follows it
//   Theme.saved_by(t)   tell the bar that someone else (the picker) saved t
//   Theme.refresh()     re-read the saved theme (another listener became active) and show it
//
// The stylesheet swap loads the new sheet before dropping the old one, so nothing flashes; only the latest request
// may win, whichever answers first. A page with no /api/theme (the episode page on a plain file server) gets no bar.
(function () {
  "use strict";
  if (window.Theme) return;

  const NAMES = { auto: "Auto", light: "Light", dark: "Dark" };
  const TIPS = { auto: "Auto: follows your system", light: "Light", dark: "Dark" };
  const ICONS = {  // 16px line icons, coloured by currentColor
    light: '<circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/>',
    dark: '<path d="M13.2 10.1A5.6 5.6 0 0 1 5.9 2.8a5.6 5.6 0 1 0 7.3 7.3Z"/>',
    auto: '<circle cx="8" cy="8" r="5.6"/><path d="M8 2.4v11.2A5.6 5.6 0 0 0 8 2.4Z" class="fill"/>',
  };
  const PALETTE = '<path d="M10 2.5a7.5 7.5 0 0 0 0 15c1 0 1.6-.7 1.6-1.5 0-.4-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 ' +
    '0-.8.7-1.5 1.5-1.5h1.9a3.9 3.9 0 0 0 3.9-3.9C18.1 5.5 14.5 2.5 10 2.5Z"/><circle cx="6.1" cy="9.3" r="1.1" class="fill"/>' +
    '<circle cx="8.4" cy="6" r="1.1" class="fill"/><circle cx="12.2" cy="6.2" r="1.1" class="fill"/>';
  const STYLE = `
  /* Colours: only the theme's roles (docs/theming.md). The swatches show their look's primary, set from data. */
  #theme-bar { position: absolute; top: 12px; left: 12px; z-index: 40; width: max-content;
               font: 13px/1 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--md-sys-color-on-surface); }
  #theme-bar[hidden] { display: none; }
  /* A page with its own heading at the top left (<body data-theme-bar="inline">) gives the button a row of its own,
     and so does the orb page while the library's chapter page or read-along shows (its back link sits there).
     Relative, not static: the panel opens against it, over what is below. */
  body[data-theme-bar="inline"] #theme-bar, html.lib-in-chapter #theme-bar {
    position: relative; top: auto; left: auto; align-self: flex-start; margin: 12px 0 0 12px; }
  #theme-bar button { appearance: none; -webkit-appearance: none; margin: 0; border: 0; padding: 0; cursor: pointer;
                      font: inherit; color: inherit; background: none; display: grid; place-items: center; }
  #theme-bar .tb-toggle { width: 36px; height: 36px; border-radius: 50%; color: var(--md-sys-color-primary);
                          background: var(--md-sys-color-surface-container);
                          border: 1px solid var(--md-sys-color-outline-variant); }
  #theme-bar .tb-toggle svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.5;
                              stroke-linejoin: round; }
  #theme-bar .tb-toggle svg .fill { fill: currentColor; stroke: none; }
  #theme-bar .tb-toggle:hover { background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%,
                                                       var(--md-sys-color-surface-container)); }
  #theme-bar .tb-toggle[aria-expanded="true"] { background: var(--md-sys-color-secondary-container);
                                                color: var(--md-sys-color-on-secondary-container); }
  #theme-bar .tb-panel { position: absolute; top: calc(100% + 6px); left: 0; display: flex; flex-direction: column;
                         align-items: flex-start; gap: 4px; padding: 4px; border-radius: 22px;
                         max-width: calc(100vw - 24px); box-sizing: border-box;
                         background: var(--md-sys-color-surface-container);
                         border: 1px solid var(--md-sys-color-outline-variant);
                         box-shadow: 0 4px 16px color-mix(in srgb, var(--md-sys-color-shadow) 18%, transparent); }
  #theme-bar .tb-panel[hidden] { display: none; }
  #theme-bar .tb-row { display: flex; align-items: center; gap: 4px; }
  #theme-bar .tb-group { display: flex; align-items: center; gap: 2px; }
  #theme-bar .tb-sep { width: 1px; height: 18px; margin: 0 4px; background: var(--md-sys-color-outline-variant); }
  #theme-bar .tb-look { width: 28px; height: 28px; border-radius: 50%; }
  #theme-bar .tb-look span { width: 20px; height: 20px; border-radius: 50%; background: var(--sw);
                             box-shadow: 0 0 0 1px color-mix(in srgb, var(--md-sys-color-on-surface) 18%, transparent) inset; }
  #theme-bar .tb-look[aria-checked="true"] { box-shadow: 0 0 0 2px var(--md-sys-color-on-surface) inset; }
  #theme-bar .tb-mode { width: 28px; height: 28px; border-radius: 50%; color: var(--md-sys-color-on-surface-variant); }
  #theme-bar .tb-mode[aria-checked="true"] { background: var(--md-sys-color-secondary-container);
                                             color: var(--md-sys-color-on-secondary-container);
                                             box-shadow: 0 0 0 2px var(--md-sys-color-on-surface) inset; }
  #theme-bar .tb-mode svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.4;
                            stroke-linecap: round; stroke-linejoin: round; }
  #theme-bar .tb-mode svg .fill { fill: currentColor; stroke: none; }
  #theme-bar .tb-panel button:hover { background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent); }
  #theme-bar .tb-mode[aria-checked="true"]:hover { background: var(--md-sys-color-secondary-container); }
  #theme-bar button:focus-visible { outline: 2px solid var(--md-sys-color-primary); outline-offset: 1px; }
  /* The note takes the row's width and wraps, so a long error never widens the panel past the screen. */
  #theme-bar .tb-note { box-sizing: border-box; width: 0; min-width: 100%; padding: 2px 8px 4px; font-size: 12px;
                        line-height: 1.35; overflow-wrap: anywhere; color: var(--md-sys-color-error); }
  #theme-bar .tb-note:empty { display: none; }
  #theme-bar .tb-status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%);
                          white-space: nowrap; }
  @media print { #theme-bar { display: none; } }`;

  const T = { saved: null, looks: [], modes: ["light", "dark", "auto"], shown: null, latest: null, seq: 0,
              subs: [], bar: null, panel: null, toggle: null, saving: Promise.resolve(), wanted: null };
  const same = (a, b) => !!a && !!b && a.seed === b.seed && a.variant === b.variant;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function sheet() { return document.getElementById("theme-css"); }

  function apply(t) {  // t: a theme to show, or null for the saved one (fresh from the server)
    const q = t ? `?seed=${encodeURIComponent(t.seed)}&variant=${encodeURIComponent(t.variant)}` +
                  `&contrast=${encodeURIComponent(t.contrast)}&mode=${encodeURIComponent(t.mode || "auto")}`
                : `?v=${++T.seq}`;
    const old = sheet();
    const next = document.createElement("link");
    next.rel = "stylesheet"; next.href = `/theme.css${q}`;
    T.latest = next;
    const prev = T.shown;
    T.shown = t ? { ...t } : (T.saved ? { ...T.saved } : null);
    paint();
    return new Promise((resolve) => {
      next.addEventListener("load", () => {
        if (T.latest !== next) { next.remove(); resolve("superseded"); return; }
        document.querySelectorAll('link[href^="/theme.css"]').forEach((l) => { if (l !== next) l.remove(); });
        next.id = "theme-css";
        resolve("loaded");
      }, { once: true });
      next.addEventListener("error", () => {  // the colours on the page stay as they were, and so does the bar
        next.remove();
        if (T.latest !== next) { resolve("superseded"); return; }
        T.shown = prev;
        paint();
        resolve("failed");
      }, { once: true });
      if (old && old.parentNode) old.parentNode.insertBefore(next, old.nextSibling); else document.head.prepend(next);
    });
  }

  function dark() {
    const mode = (T.shown || T.saved || {}).mode || "auto";
    const forced = document.documentElement.dataset.theme;
    if (forced === "dark" || forced === "light") return forced === "dark";
    return mode === "dark" || (mode === "auto" && !!media && media.matches);
  }

  function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

  // A radio group: one tab stop (the checked button, else the first); arrow keys move and pick, Home and End too.
  function radios(group, onPick) {
    group.addEventListener("keydown", (e) => {
      if (e.altKey || e.metaKey || e.ctrlKey) return;  // a browser shortcut (Alt+Left: back), not a pick
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      const all = [...group.querySelectorAll('[role="radio"]')];
      const at = all.indexOf(document.activeElement);
      if (at < 0) return;
      let to = null;
      if (step) to = all[(at + step + all.length) % all.length];
      else if (e.key === "Home") to = all[0];
      else if (e.key === "End") to = all[all.length - 1];
      if (!to) return;
      e.preventDefault();
      e.stopPropagation();  // used here: a page's own shortcut (the episode page's arrows) must not act on it too
      to.focus();
      onPick(to);
    });
  }

  // The panel opens and closes. Escape gives focus back to the button if it was inside the panel; a press elsewhere
  // or focus moving away leaves it where the listener put it (moving it would scroll the page back to the button).
  function setOpen(open, refocus) {
    if (!T.panel || open === !T.panel.hidden) return;
    const inside = T.panel.contains(document.activeElement);
    T.panel.hidden = !open;
    T.toggle.setAttribute("aria-expanded", String(open));
    if (!open && inside && refocus) T.toggle.focus({ preventScroll: true });
  }

  function build() {
    if (T.bar || !document.body) return;
    const style = el("style"); style.id = "theme-bar-style"; style.textContent = STYLE;
    document.head.appendChild(style);
    const bar = el("div"); bar.id = "theme-bar";
    const toggle = el("button", "tb-toggle");
    toggle.type = "button"; toggle.setAttribute("aria-label", "Theme"); toggle.title = "Theme";
    toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-controls", "theme-panel");
    toggle.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${PALETTE}</svg>`;
    toggle.addEventListener("click", () => setOpen(T.panel.hidden));
    const panel = el("div", "tb-panel"); panel.id = "theme-panel"; panel.hidden = true;
    panel.setAttribute("role", "group"); panel.setAttribute("aria-label", "Theme");
    const looks = el("div", "tb-group"); looks.setAttribute("role", "radiogroup"); looks.setAttribute("aria-label", "Look");
    for (const look of T.looks) {
      const b = el("button", "tb-look");
      b.type = "button"; b.setAttribute("role", "radio"); b.setAttribute("aria-label", look.name); b.title = look.name;
      b.dataset.seed = look.seed; b.dataset.variant = look.variant;
      b.appendChild(el("span"));
      b.addEventListener("click", () => pick({ seed: look.seed, variant: look.variant }));
      looks.appendChild(b);
    }
    radios(looks, (b) => pick({ seed: b.dataset.seed, variant: b.dataset.variant }));
    const modes = el("div", "tb-group"); modes.setAttribute("role", "radiogroup"); modes.setAttribute("aria-label", "Light or dark");
    for (const m of ["light", "dark", "auto"].filter((x) => T.modes.includes(x))) {
      const b = el("button", "tb-mode");
      b.type = "button"; b.setAttribute("role", "radio"); b.setAttribute("aria-label", NAMES[m]); b.title = TIPS[m];
      b.dataset.mode = m;
      b.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS[m]}</svg>`;
      b.addEventListener("click", () => pick({ mode: m }));
      modes.appendChild(b);
    }
    radios(modes, (b) => pick({ mode: b.dataset.mode }));
    const row = el("div", "tb-row");
    row.append(looks, el("span", "tb-sep"), modes);
    const note = el("span", "tb-note");
    panel.append(row, note);
    // Screen readers hear a failed save from here: a live region must already be rendered when its text changes,
    // and the panel (with the visible note) is often closed at that moment.
    const status = el("span", "tb-status"); status.setAttribute("role", "status");
    bar.append(toggle, panel, status);
    // Closed by Escape (from inside the bar, or with nothing focused), a press elsewhere, or focus moving away.
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || panel.hidden) return;
      const at = document.activeElement;
      if (bar.contains(at) || !at || at === document.body) { e.preventDefault(); setOpen(false, true); }
    });
    document.addEventListener("pointerdown", (e) => { if (!bar.contains(e.target)) setOpen(false); }, true);
    bar.addEventListener("focusout", (e) => { if (e.relatedTarget && !bar.contains(e.relatedTarget)) setOpen(false); });
    document.body.prepend(bar);
    T.bar = bar; T.panel = panel; T.toggle = toggle;
    paint();
  }

  function paint() {
    if (!T.bar) return;
    const t = T.shown || T.saved;
    if (!t) return;
    const d = dark();
    const looks = [...T.bar.querySelectorAll(".tb-look")];
    const on = looks.findIndex((b) => same(t, b.dataset));
    looks.forEach((b, i) => {
      const look = T.looks[i];
      b.style.setProperty("--sw", d ? look.swatch.dark : look.swatch.light);  // data from the server, not a page colour
      b.setAttribute("aria-checked", String(i === on));
      b.tabIndex = i === (on < 0 ? 0 : on) ? 0 : -1;
    });
    T.bar.querySelectorAll(".tb-mode").forEach((b) => {
      const yes = b.dataset.mode === (t.mode || "auto");
      b.setAttribute("aria-checked", String(yes));
      b.tabIndex = yes ? 0 : -1;
    });
  }

  function note(text) {  // a failed save opens the panel, so the note is seen even if it was closed meanwhile
    const n = T.bar && T.bar.querySelector(".tb-note");
    if (!n) return;
    n.textContent = text || "";
    T.bar.querySelector(".tb-status").textContent = text || "";
    if (text) setOpen(true);
  }

  const sameAll = (a, b) => same(a, b) && a.contrast === b.contrast && (a.mode || "auto") === (b.mode || "auto");

  // A click: shown at once, then saved. Saves go one after another, each the whole theme as it stood at its click,
  // so the last click is what settings.json ends up with. The base is the saved theme, not an unsaved preview the
  // Settings picker may be showing (the bar never saves someone else's preview); Light, Dark or Auto alone keeps
  // that preview on screen, in the new mode. Every save that goes through is recorded, even when a later click is
  // on its way, so a failure goes back to what the server really holds; subscribers hear once, when the burst of
  // clicks has settled, with everything it changed.
  function pick(change) {
    if (!T.saved) return;
    if (!T.wanted) {  // the first click of a burst: what was on screen, and whether it was someone's preview
      T.before = T.shown ? { ...T.shown } : null;
      T.preview = !!T.before && !sameAll(T.before, T.saved);
      T.burstSaved = false;
      T.burstChange = {};
    }
    T.burstChange = { ...T.burstChange, ...change };
    const want = { ...(T.wanted || T.saved), ...change };
    T.wanted = want;
    note("");
    const modeOnly = Object.keys(change).every((k) => k === "mode");
    const shown = apply(modeOnly && T.shown ? { ...T.shown, mode: want.mode } : want);
    const body = { theme: { seed: want.seed, variant: want.variant, contrast: want.contrast, mode: want.mode } };
    T.saving = T.saving.then(async () => {
      let res, out = {};
      try {
        res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify(body) });
        out = await res.json().catch(() => ({}));
      } catch (e) { res = null; }
      const ok = !!(res && res.ok && out.settings && out.settings.theme);
      if (ok) { T.saved = { ...out.settings.theme }; T.burstSaved = true; }
      if (T.wanted !== want) return;  // a later click is on its way: it decides what is shown
      T.wanted = null;
      const burst = T.burstChange, keepPreview = T.preview && Object.keys(burst).every((k) => k === "mode");
      const tell = (c) => T.subs.forEach((fn) => { try { fn({ ...T.saved }, c); } catch (e) {} });
      if (!ok) {
        note((out && out.detail) || "Couldn't save the theme.");
        if (T.burstSaved) {  // an earlier click of the burst was saved: that is the theme now
          apply(null);
          tell(burst);
        } else apply(T.preview ? T.before : null);  // nothing saved: back to what was on screen
        return;
      }
      if (await shown === "failed") apply(null);  // saved, but its sheet never loaded: load the saved one
      else if (!keepPreview) T.shown = { ...T.saved };
      paint();
      tell(burst);  // last: the picker may put its preview back on top
    });
  }

  async function load() {
    try {
      const res = await fetch("/api/theme", { cache: "no-store" });
      if (!res.ok) return false;
      const got = await res.json();
      T.saved = got.settings; T.looks = got.looks || []; T.modes = got.modes || T.modes;
      if (!T.shown) T.shown = { ...T.saved };
      if (document.body) build(); else document.addEventListener("DOMContentLoaded", build, { once: true });
      paint();
      return true;
    } catch (e) { return false; }
  }

  if (media && media.addEventListener) media.addEventListener("change", paint);

  window.Theme = {
    apply,
    dark,
    get saved() { return T.saved ? { ...T.saved } : null; },
    get looks() { return T.looks.slice(); },
    subscribe(fn) { T.subs.push(fn); },
    saved_by(t) {  // the picker saved t; a new contrast changes the swatches' colours, so the looks are fetched again
      if (!t) return;
      const old = T.saved;
      T.saved = { ...t }; T.shown = { ...t }; T.wanted = null;
      note("");  // saved now: an earlier failure no longer holds
      paint();
      if (!old || old.contrast !== t.contrast) load();
    },
    async refresh() { T.wanted = null; T.shown = null; note(""); await load(); return apply(null); },
    ready: load(),
  };
})();
