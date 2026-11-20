// The earpiece view (Ian, 2026-11-20): "it would be great to see what the director is feeding to the narrator in real
// time ... a floating window of that for an admin view". Loaded by page/live.js (loadEarpiece) only when
// /api/live/start says this listener has the view on (tester.json earpiece_view, or live.director.view off demo
// mode); /earpiece.js is a 404 for everyone else. It reads Live.L.dirEvents (a replay on load) and the events
// live.js hands it (dirEmit), and Live.S.director.enabled. It never calls into Live, never posts and never
// changes L: what it shows cannot change what the page does. The admin's history (/admin/earpiece) is built from
// the transcript on the server, not from this.
(function () {
  "use strict";
  if (window.Earpiece) return;
  const Live = window.Live || { L: { dirEvents: [] }, S: {} };
  const MAX = 200;
  const KEY = "wl.earpiece.open";
  const REASONS = {
    not_here: "the page wasn't in a state to send",
    stale_after_start: "the chapter had started",
    explained: "the guide had already said what it does",
    listener_spoke: "the listener spoke after the look was asked",
    gap: "too soon after the last whisper",
    replaced: "a newer note replaced it",
    forgotten: "a rest, a wake or a new session cleared it",
    down: "the earpiece went down",
    ended: "the conversation ended",
  };

  // --- state ---------------------------------------------------------------------------------------------
  let items = [];           // oldest first; rendered newest first
  const looks = new Map();  // "<session>:<id>" -> look item
  const sentTexts = new Set();
  let session = null, down = false, forgotPending = false, seq = 0;
  let prevPlan = null;      // the previous look with a plan in this conversation

  const keyOf = (s, id) => `${s || ""}:${id}`;
  const norm = (x) => String(x || "").toLowerCase().replace(/\s+/g, " ").trim();
  const mss = (secs) => (secs == null || !isFinite(secs) ? "–" : `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, "0")}`);
  const clock = (at) => { const d = new Date(at); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((v) => String(v).padStart(2, "0")).join(":"); };
  const cut = (x, n) => { x = String(x || ""); return x.length > n ? x.slice(0, n - 1) + "…" : x; };

  function add(item) {
    item.seq = ++seq;
    items.push(item);
    if (items.length > MAX) {
      const gone = items.splice(0, items.length - MAX);
      gone.forEach((g) => { if (g.kind === "look") looks.delete(keyOf(g.session, g.id)); });
    }
    return item;
  }

  // The shared plan rule: against the previous look with a plan in the same conversation, items matched by text.
  function planChanges(prev, plan) {
    if (!prev) return { plan: plan.map((p) => ({ ...p, change: null })), removed: [] };
    const before = new Map(prev.map((p) => [norm(p.item), p.state]));
    const now = new Set(plan.map((p) => norm(p.item)));
    return {
      plan: plan.map((p) => {
        const k = norm(p.item);
        return { ...p, change: !before.has(k) ? "new" : before.get(k) !== p.state ? p.state : null };
      }),
      removed: prev.filter((p) => !now.has(norm(p.item))).map((p) => p.item),
    };
  }

  // --- events ----------------------------------------------------------------------------------------------
  function take(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
      case "conversation":
        session = ev.session; down = false; prevPlan = null; forgotPending = false;
        add({ kind: "divider", ev, text: `Conversation ${ev.session || ""}${ev.handoff ? " (handed over)" : ""}` });
        break;
      case "session":
        add({ kind: "divider", ev, text: `New voice session (${ev.reason || "?"})` });
        break;
      case "ask": {
        if (ev.session && ev.session !== session) session = ev.session;
        const it = add({ kind: "look", ev, session: ev.session, id: ev.id, trigger: ev.trigger, speaking: ev.speaking,
                         state: "asking", got: null, fate: null, held: null, reply: null });
        looks.set(keyOf(ev.session, ev.id), it);
        break;
      }
      case "look": {
        const got = ev.got || {};
        if (got.error) break;  // the "fail" event that follows says so
        let it = looks.get(keyOf(ev.session, ev.id));
        if (!it) { it = add({ kind: "look", ev, session: ev.session, id: ev.id, trigger: "?", speaking: "?", fate: null, held: null, reply: null }); looks.set(keyOf(ev.session, ev.id), it); }
        it.state = "done"; it.got = got; it.lookEv = ev;
        if (Array.isArray(got.plan) && got.plan.length) {
          const ch = planChanges(prevPlan, got.plan);
          it.plan = ch.plan; it.removed = ch.removed;
          prevPlan = got.plan;
        }
        break;
      }
      case "fail": {
        const it = ev.id != null && looks.get(keyOf(ev.session, ev.id));
        if (it) { it.kind = "fail"; it.why = ev.why; it.failEv = ev; }
        else add({ kind: "fail", ev, why: ev.why, trigger: null });
        break;
      }
      case "down":
        down = true;
        add({ kind: "down", ev });
        break;
      case "fate": {
        const it = ev.id != null && looks.get(keyOf(ev.session, ev.id));
        if (ev.reason === "forgotten") forgotPending = true;
        if (!it) break;
        if (ev.fate === "held") { it.held = ev; break; }
        it.fate = ev;
        if (ev.fate === "sent" && ev.text) sentTexts.add(ev.text);
        break;
      }
      case "note":
        if (sentTexts.has(ev.text)) break;  // a whisper: shown on its look
        add({ kind: "note", ev, text: ev.text });
        break;
      case "forget": {
        items.forEach((x) => { if (x.kind === "look" && x.state === "asking" && x.session === ev.session) x.state = "unused"; });
        add({ kind: "forget", ev, why: ev.why, pending: forgotPending });
        forgotPending = false;
        break;
      }
      case "line":
        if (ev.who !== "guide") break;
        items.forEach((x) => {
          if (x.kind === "look" && x.fate && x.fate.fate === "sent" && !x.reply && x.session === ev.session
              && (ev.began || ev.at) >= x.fate.at) {
            x.reply = { after: ((ev.began || ev.at) - x.fate.at) / 1000, text: ev.text };
          }
        });
        break;
      default:
        break;
    }
  }

  // --- DOM ---------------------------------------------------------------------------------------------------
  const CSS = `
#earpiece { --ep-accent: var(--accent, #d9822b); --ep-fg: var(--fg, #1d1d1b); --ep-bg: var(--bg, #fafaf8);
  --ep-muted: var(--muted, #77756f); --ep-line: var(--line, #e4e2dc);
  position: fixed; z-index: 12; left: calc(12px + env(safe-area-inset-left, 0px)); top: calc(12px + env(safe-area-inset-top, 0px));
  width: min(340px, calc(100vw - 24px)); box-sizing: border-box; overflow: auto; overscroll-behavior: contain;
  font: 13px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ep-fg); background: var(--ep-bg);
  border: 1px solid var(--ep-line); border-radius: 10px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
@media (max-width: 599px) { #earpiece { top: calc(56px + env(safe-area-inset-top, 0px)); } }
body.handoff:not(.revealed) #earpiece, body.debrief #earpiece { display: none; }
#earpiece-head { position: sticky; top: 0; display: flex; gap: 6px; align-items: baseline; width: 100%; box-sizing: border-box;
  font: inherit; color: inherit; background: var(--ep-bg); border: 0; border-bottom: 1px solid var(--ep-line);
  padding: 7px 10px; cursor: pointer; text-align: left; }
#earpiece-head b { color: var(--ep-accent); }
#earpiece-head .meta { color: var(--ep-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
#earpiece-head .state { color: var(--ep-accent); font-weight: 600; }
#earpiece[data-open="false"] #earpiece-list { display: none; }
#earpiece[data-open="false"] #earpiece-head { border-bottom: 0; }
#earpiece-list { list-style: none; margin: 0; padding: 0 10px 6px; }
#earpiece-list > li { padding: 7px 0; border-bottom: 1px solid var(--ep-line); overflow-wrap: anywhere; }
#earpiece-list > li:last-child { border-bottom: 0; }
#earpiece .stamp { color: var(--ep-muted); font-variant-numeric: tabular-nums; margin-right: 6px; }
#earpiece .muted, #earpiece li.note, #earpiece li.forget { color: var(--ep-muted); }
#earpiece li.divider { color: var(--ep-muted); font-weight: 600; text-align: center; border-bottom-style: dashed; }
#earpiece li.fail { color: var(--ep-accent); }
#earpiece .down { color: var(--ep-accent); font-weight: 600; }
#earpiece .plan { list-style: none; margin: 4px 0; padding: 0; }
#earpiece .plan li::before { content: "○ "; color: var(--ep-muted); }
#earpiece .plan li[data-state="done"]::before { content: "✓ "; color: var(--ep-accent); }
#earpiece .plan li[data-state="dropped"] { text-decoration: line-through; color: var(--ep-muted); }
#earpiece .plan li[data-change]:not([data-change=""]) { font-weight: 600; }
#earpiece .plan .chg { color: var(--ep-accent); font-weight: 400; font-size: 11px; margin-left: 4px; }
#earpiece .whisper { margin-top: 4px; padding-left: 8px; border-left: 2px solid var(--ep-accent); }
#earpiece .fate { margin-top: 2px; }
#earpiece .fate[data-fate="sent"] { color: var(--ep-accent); }
#earpiece .fate[data-fate="dropped"] { color: var(--ep-muted); }
#earpiece .reply { margin-top: 2px; color: var(--ep-muted); font-style: italic; }
`;

  let root = null, head = null, list = null, style = null, fitTimer = null;
  function readOpen() { try { return localStorage.getItem(KEY) !== "0"; } catch (e) { return true; } }
  function saveOpen(on) { try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* fine */ } }

  function build() {
    style = document.createElement("style");
    style.id = "earpiece-style";
    style.textContent = CSS;
    document.head.appendChild(style);
    root = document.createElement("aside");
    root.id = "earpiece";
    root.setAttribute("aria-label", "Earpiece");
    head = document.createElement("button");
    head.id = "earpiece-head";
    head.type = "button";
    list = document.createElement("ol");
    list.id = "earpiece-list";
    root.append(head, list);
    document.body.appendChild(root);
    head.addEventListener("click", () => open(root.dataset.open !== "true"));
    open(readOpen(), false);
    window.addEventListener("resize", fit);
    window.addEventListener("scroll", fit, { passive: true });
    // A player bar can appear, or the circle move, with no event, resize or scroll (live.js renders on its own
    // ticks; the library adds its bar later): fit again at live.js's render cadence while the panel is up.
    fitTimer = setInterval(fit, 500);
  }

  function open(on, save = true) {
    if (!root) return;
    root.dataset.open = on ? "true" : "false";
    head.setAttribute("aria-expanded", on ? "true" : "false");
    if (save) saveOpen(on);
    fit();
  }

  // What is painted, not the attribute: a hidden element has no box, and one shown by style despite its attribute
  // is still in the way.
  const visible = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return r.width && r.height ? r : null; };

  // Never over the circle, the text box, End demo or the player bars: the panel's height stops above whatever is
  // below it in its column, the bars at the bottom included (floor 96 px).
  function fit() {
    if (!root) return;
    const top = root.getBoundingClientRect().top;
    const left = root.getBoundingClientRect().left, right = left + root.offsetWidth;
    let max = innerHeight - top - 12;
    for (const id of ["player", "lib-player"]) {
      const r = visible(document.getElementById(id));
      if (r && r.top < innerHeight) max = Math.min(max, r.top - top - 12);
    }
    for (const id of ["orb", "form", "end-demo"]) {
      const r = visible(document.getElementById(id));
      if (!r || r.right <= left || r.left >= right || r.bottom <= top || r.top >= innerHeight) continue;
      max = Math.min(max, r.top - top - 8);
    }
    root.style.maxHeight = `${Math.max(96, Math.floor(max))}px`;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function stamp(ev) { return el("span", "stamp", `${mss(ev.secs)} · ${clock(ev.at)}`); }

  function fateLine(it) {
    const f = it.fate;
    const d = el("div", "fate");
    if (!f) {
      d.dataset.fate = ""; d.dataset.reason = "";
      d.textContent = it.state === "unused" ? "Not used (the page moved on)" : "waiting";
      return d;
    }
    d.dataset.fate = f.fate; d.dataset.reason = f.reason || "";
    if (f.fate === "sent") d.textContent = it.held ? `Held ${mss(it.held.secs)}, sent ${mss(f.secs)}` : `Sent to the guide at ${mss(f.secs)}`;
    else {
      const why = f.reason === "not_here" && f.detail ? `not here (${f.detail})`
        : (REASONS[f.reason] || f.reason || "?") + (f.reason === "gap" && f.detail ? ` (${f.detail})` : "")
          + (f.reason === "forgotten" && f.detail ? ` (${f.detail})` : "");
      d.textContent = `Dropped at ${mss(f.secs)}: ${why}`;
    }
    return d;
  }

  function lookLi(it) {
    const li = el("li", "look");
    li.dataset.session = it.session || ""; li.dataset.id = it.id == null ? "" : String(it.id);
    const top = el("div");
    top.append(stamp(it.ev));
    if (it.state === "asking") { top.append(document.createTextNode(`Looking… (${it.trigger})`)); li.append(top); return li; }
    if (it.state === "unused" && !it.got) { top.append(document.createTextNode(`${it.trigger} · answer not used (the page moved on)`)); li.append(top); return li; }
    top.append(el("span", "what", `${it.trigger} · ${it.speaking} speaking`));
    li.append(top);
    const g = it.got || {};
    if (g.read) li.append(el("div", "read muted", g.read));
    if (it.plan) {
      const ul = el("ul", "plan");
      it.plan.forEach((p) => {
        const x = el("li", null, p.item);
        x.dataset.state = p.state; x.dataset.change = p.change || "";
        if (p.change) x.append(el("span", "chg", p.change));
        ul.append(x);
      });
      li.append(ul);
      if (it.removed && it.removed.length) li.append(el("div", "removed muted", "removed: " + it.removed.join("; ")));
    }
    if (g.next) li.append(el("div", "next", "Next: " + g.next));
    if (g.changed && g.note) {
      const w = el("div", "whisper");
      w.append(el("div", "note-text", g.note));
      w.append(el("div", "muted", `${g.kind || "when_it_fits"} · ${g.about || "–"}`));
      w.append(fateLine(it));
      if (it.fate && it.fate.fate === "sent" && it.reply) {
        w.append(el("div", "reply", `Guide began ${it.reply.after.toFixed(1)} s after: “${cut(it.reply.text, 140)}”`));
      }
      li.append(w);
    }
    return li;
  }

  function itemLi(it) {
    switch (it.kind) {
      case "look": return lookLi(it);
      case "fail": { const li = el("li", "fail"); li.append(stamp(it.failEv || it.ev), document.createTextNode(`Look failed: ${it.why}`)); return li; }
      case "down": { const li = el("li"); const d = el("div", "down"); d.append(stamp(it.ev), document.createTextNode("Earpiece down: the page's clocks have taken over")); li.append(d); return li; }
      case "forget": { const li = el("li", "forget"); li.append(stamp(it.ev), document.createTextNode(it.pending ? `Pending note forgotten (${it.why || "?"})` : `Cleared (${it.why || "?"})`)); return li; }
      case "note": { const li = el("li", "note"); li.title = it.text; li.append(stamp(it.ev), document.createTextNode(cut(it.text, 120))); return li; }
      case "divider": { const li = el("li", "divider"); li.append(stamp(it.ev), document.createTextNode(it.text)); return li; }
      default: return el("li", null, "");
    }
  }

  function renderHead() {
    const mine = items.filter((x) => x.session === session && x.kind === "look");
    const n = mine.length;
    const sent = mine.filter((x) => x.fate && x.fate.fate === "sent").length;
    const dropped = mine.filter((x) => x.fate && x.fate.fate === "dropped").length;
    const off = !((Live.S && Live.S.director) || {}).enabled;
    head.replaceChildren(el("b", null, "Earpiece"),
      el("span", "meta", `${session || ""} · ${n} look${n === 1 ? "" : "s"} · ${sent} sent · ${dropped} dropped`));
    if (off || down) head.append(el("span", "state", off ? "off" : "down"));
  }

  function render() {
    if (!root) return;
    renderHead();
    const frag = document.createDocumentFragment();
    for (let i = items.length - 1; i >= 0; i--) frag.append(itemLi(items[i]));
    list.replaceChildren(frag);
    fit();
  }

  function event(ev) { take(ev); render(); }

  function remove() {
    window.removeEventListener("resize", fit);
    window.removeEventListener("scroll", fit);
    clearInterval(fitTimer);
    fitTimer = null;
    if (root) root.remove();
    if (style) style.remove();
    root = head = list = style = null;
    try { delete window.Earpiece; } catch (e) { window.Earpiece = undefined; }
    try { if (window.Live && window.Live.L) window.Live.L.earpieceLoaded = false; } catch (e) { /* fine */ }
  }

  build();
  ((Live.L && Live.L.dirEvents) || []).forEach(take);
  render();
  window.Earpiece = { event, open: (on) => open(!!on), remove };
})();
