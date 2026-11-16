// The library (docs/overnight-chapters.md; overnight/DESIGN.md "Round 2"): a module any page mounts. It builds
// its markup into <section id="library"> (made at the end of <body> if the page has none) and a fixed mini
// player <div id="player"> owning the one <audio id="chapter-audio">. page/index.html mounts it below the orb;
// /library (page/library.html) is a standalone shell around it.
//   #/                 home: Up next (Change by voice, Add a note by text), the latest chapter, every past one
//   #/chapter/<run>    a chapter view in the page: read along (the sentence being heard highlighted, click a
//                      sentence to play from there), reference cards. Built from /runs/<run>/11_episode/episode.json.
// Moving between them never touches the audio: the player keeps playing across views.
//
// The guide's live sessions, on GPT-Live (overnight/live.py):
//   Play    on the latest chapter, when it has an intro brief: the guide opens live (the intro), then start_chapter
//           and the recording plays. If the guide doesn't call the tool, the recording starts after its lead-in
//           line and about 1.5 s of silence, or at 90 s at the latest. "Skip the intro" plays the recording at
//           once, with no session and no microphone. Any other chapter's Play just plays.
//   Ask     a tap while a recording plays pauses it; the guide answers in a new session; resume_chapter
//           (or its lead-back line, or "Back to the chapter") and the recording resumes from the restart
//           point the server picks (ask/aside.py's rewind rules).
//   Change  a conversation about tonight's chapter, ending in set_next_chapter; the card refreshes.
// One WebRTC session at a time: Change waits while another conversation is on (and pauses the recording);
// Play ends a Change conversation first; a tap while one is still connecting abandons it, and the server's
// side of it is ended too. The session is opened through our server (GPT-Live has no client keys). Each
// conversation's audio (both sides) is recorded when the browser can, and kept beside its transcript.
// When the page's own #audio (index.html's orb chapter) starts, the chapter player pauses and any session here
// ends, and the other way round; the chapter view takes #audio over when it plays the same chapter. The orb's
// voice guide (window.Orb) ends before this player plays or a session here opens the mic, and the orb asks
// window.Library.busy()/end() before it listens.
(() => {
  if (window.__libraryMounted) return;
  window.__libraryMounted = true;
  const $ = (id) => document.getElementById(id);
  const now = () => Date.now();

  // --- markup -------------------------------------------------------------------------------------
  const STYLE = `
  #library, #player { --card: #ffffff; --soft: #f3e3d1; }
  @media (prefers-color-scheme: dark) { #library, #player { --card: #1c1c1a; --soft: #3a2a1b; } }
  #library { display: block; width: 100%; max-width: 38em; margin: 0 auto; flex: none; text-align: left;
             padding: 32px 16px calc(48px + var(--lib-player-h, 0px)); font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  #library.lib-wide { max-width: 1100px; }
  #library [hidden] { display: none !important; }
  #lib-home { display: flex; flex-direction: column; gap: 18px; }
  #library .lib-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; }
  #library .lib-kicker { font-size: 13px; font-weight: 400; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); margin: 0 0 8px; }
  #library h2 { font-size: 20px; line-height: 1.25; margin: 0 0 6px; }
  #library .lib-overview { margin: 0 0 10px; }
  #library .lib-summary { margin: 0 0 12px; color: var(--muted); }
  #library .lib-line { font-size: 14px; color: var(--muted); margin: 0; }
  #library .lib-row { display: flex; align-items: center; gap: 14px; margin-top: 14px; flex-wrap: wrap; }
  #library .lib-meta { font-size: 14px; color: var(--muted); margin: 0 0 10px; }
  #library .lib-empty { color: var(--muted); margin: 0; }
  #library a { color: var(--muted); text-underline-offset: 3px; font-size: 14px; }
  .lib-pill { font: inherit; color: var(--accent); background: none; border: 1px solid var(--accent);
              border-radius: 999px; padding: 6px 18px; cursor: pointer; }
  .lib-pill.solid { background: var(--accent); color: var(--bg); }
  .lib-pill:disabled { opacity: .5; cursor: default; }
  .lib-link { font: inherit; font-size: 14px; color: var(--muted); background: none; border: 0; padding: 0;
              text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
  #library.building #up-line { color: var(--accent); }
  #library.failed #up-line { color: var(--fg); font-weight: 600; }
  #library .lib-notes { margin: 0 0 12px; padding-left: 1.1em; font-size: 14px; color: var(--muted); }
  #library .lib-notes li { margin: 2px 0; }
  #note-text { display: block; width: 100%; margin-top: 12px; font: inherit; color: var(--fg); background: transparent;
               border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; resize: vertical; }
  #note-text:focus { outline: none; border-color: var(--accent); }
  #note-msg { margin-top: 10px !important; }
  #live { font-size: 14px; color: var(--muted); margin: 12px 0 0; }
  #live:empty { display: none; }
  #lib-hint { font-size: 13px; color: var(--muted); margin: 8px 0 0; }
  #settings-link { align-self: center; }
  #settings { display: none; }
  #library.lib-settings-open #settings { display: block; }
  #settings label { display: block; margin: 8px 0; font-size: 15px; }
  #settings .days label { display: inline-block; margin-right: 10px; }
  #settings input[type=time] { font: inherit; }
  #settings .note { font-size: 13px; color: var(--muted); }
  #lib-chapter h1 { font-size: 26px; line-height: 1.3; margin: 14px 0 4px; }
  #ch-angle { color: var(--muted); margin: 0; }
  #ch-layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 36px; margin-top: 18px; }
  @media (max-width: 820px) { #ch-layout { grid-template-columns: minmax(0, 1fr); } }
  #ch-text { font-size: 18px; line-height: 1.75; }
  #ch-text h3, #ch-cards h3 { font-size: 13px; font-weight: 400; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 22px 0 6px; }
  #ch-text p { margin: 0 0 12px; }
  #ch-text .lib-s { cursor: pointer; border-radius: 4px; transition: background .2s; }
  #ch-text .lib-s:hover { text-decoration: underline; text-decoration-color: var(--line); }
  #ch-text .lib-s.current { background: var(--soft); }
  #ch-cards .lib-ref { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin: 0 0 10px; font-size: 15px; line-height: 1.45; }
  #ch-cards .lib-ref.latest { border-color: var(--accent); }
  #ch-cards .lib-ref .kind { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  #ch-cards .lib-ref .title { font-weight: 600; }
  #ch-cards .lib-ref a { color: var(--accent); font-size: inherit; }
  #ch-cards .lib-ref .note { color: var(--muted); }
  #player { position: fixed; left: 0; right: 0; bottom: 0; z-index: 10; background: var(--card); color: var(--fg);
            border-top: 1px solid var(--line); font: 16px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  #player[hidden] { display: none; }
  #player .lib-pin { max-width: 38em; margin: 0 auto; padding: 8px 16px; display: flex; align-items: center; gap: 12px; }
  #player-toggle { flex: none; width: 40px; height: 40px; border-radius: 50%; border: 0; padding: 0; background: var(--accent);
                   color: #fff; cursor: pointer; font-size: 14px; }
  #player .lib-mid { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  #player-title { font-size: 14px; color: var(--fg); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #player-line { font-size: 13px; color: var(--muted); }
  #player-line:empty { display: none; }
  #player-seek { width: 100%; margin: 2px 0 0; accent-color: var(--accent); }
  #player-time { font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
  #player-ask { flex: none; font-size: 14px; padding: 5px 14px; }
  html.lib-in-chapter #screen { display: none; }

  /* The library timeline (docs/library-timeline.md, sketches B and D). Colours: the app's own tokens where it
     sets one (--bg, --fg, --muted, --line, --accent, the system font: the spec keeps the app's, PROGRESS.md), the
     sketches' for the rest (accent text, topic colours, surface). */
  #library { --lt-surface: var(--card); --lt-accent-text: #9C4330; --lt-teal: #24585B; --lt-coral: #C8553D;
             --lt-amber: #B7791F; --lt-became: #E8C9BF; }
  @media (prefers-color-scheme: dark) { #library { --lt-accent-text: #E59A82; --lt-became: #6b3a2d; } }
  #library:not(.lib-wide) { max-width: 480px; padding-left: 0; padding-right: 0; }
  #lib-home { gap: 20px; }
  #lib-home > *:not(.lt-stripwrap) { margin-left: 22px; margin-right: 22px; }
  .lt-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; }
  .lt-title { margin: 0 !important; font-size: 30px !important; font-weight: 600; letter-spacing: -0.5px; line-height: 1.1 !important; }
  .lt-sub { margin: 4px 0 0; font-size: 13px; color: var(--muted); }
  .lt-orb { flex: none; width: 44px; height: 44px; border-radius: 50%; background: var(--accent); opacity: .85; }
  html.has-orb .lt-orb { display: none; }
  .lt-stripwrap { position: relative; }
  .lt-strip { display: flex; align-items: flex-start; gap: 14px; overflow-x: auto; scroll-snap-type: x mandatory; scroll-padding-left: 22px;
              padding: 2px 0 6px 22px; scrollbar-width: none; overscroll-behavior-x: contain; }
  .lt-strip::-webkit-scrollbar { display: none; }
  .lt-card { position: relative; flex: 0 0 calc(100% - 78px);  /* the strip less 100px (its 22px gutter is padding) */ min-height: 330px; box-sizing: border-box; border-radius: 22px;
             padding: 22px; display: flex; flex-direction: column; justify-content: space-between; gap: 16px;
             scroll-snap-align: start; scroll-snap-stop: always; }
  .lt-spacer { flex: 0 0 64px; }  /* room for Up next to snap to the start: 100px - the 22px gutter - the 14px gap */
  .lt-ch { color: #fff; background: var(--lt-teal); }
  .lt-ch.coral { background: var(--lt-coral); }
  .lt-ch.amber { background: var(--lt-amber); color: #1E1A17; }  /* white on amber fails contrast: ink instead */
  .lt-cdate { font-size: 12px; font-weight: 600; letter-spacing: .6px; text-transform: uppercase; opacity: .9; }
  .lt-cbottom { display: flex; flex-direction: column; gap: 6px; }
  .lt-ctitle, #library a.lt-ctitle { font-size: 24px; font-weight: 600; line-height: 1.15; margin: 0; color: inherit; text-decoration: none; }
  a.lt-ctitle::after { content: ""; position: absolute; inset: 0; border-radius: 22px; }  /* the whole card opens it */
  a.lt-ctitle:focus-visible { outline: none; }
  a.lt-ctitle:focus-visible::after { outline: 3px solid var(--fg); outline-offset: 3px; }
  .lt-cstatus { font-size: 13px; opacity: .9; }
  .lt-crow { position: relative; z-index: 1; display: flex; align-items: center; gap: 14px; margin-top: 8px; flex-wrap: wrap; }
  .lt-play { font: inherit; font-size: 15px; font-weight: 600; min-height: 44px; padding: 0 22px; border-radius: 22px; border: 0;
             background: #fff; color: #1E1A17; cursor: pointer; }
  .lt-play:focus-visible, .lt-link:focus-visible, .lt-arrow:focus-visible, .lt-pill:focus-visible { outline: 3px solid var(--fg); outline-offset: 2px; }
  .lt-ch .lt-link { color: inherit; }
  .lt-link { font: inherit; font-size: 14px; color: var(--muted); background: none; border: 0; padding: 0; min-height: 44px;
             text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
  .lt-up { background: var(--lt-surface); color: var(--fg); border: 1.5px dashed var(--accent); justify-content: flex-start; gap: 8px; }
  .lt-upkick { font-size: 12px; font-weight: 600; letter-spacing: .6px; text-transform: uppercase; color: var(--lt-accent-text); margin: 0; }
  .lt-up .lt-ctitle { margin: 2px 0 4px; }
  .lt-upover { margin: 0; font-size: 15px; line-height: 1.4; }
  .lt-upsum { margin: 0; font-size: 13px; line-height: 1.45; color: var(--muted); }
  .lt-upline { margin: 4px 0 0; font-size: 13px; color: var(--muted); }
  #library.building .lt-upline { color: var(--lt-accent-text); }
  #library.failed .lt-upline { color: var(--fg); font-weight: 600; }
  .lt-uprow { display: flex; align-items: center; gap: 16px; margin-top: auto; padding-top: 6px; flex-wrap: wrap; }
  .lt-pill { font: inherit; font-size: 14px; font-weight: 600; min-height: 44px; padding: 0 20px; border-radius: 22px; cursor: pointer;
             color: var(--lt-accent-text); background: none; border: 1.5px solid var(--accent); }
  .lt-pill:disabled { opacity: .5; cursor: default; }
  .lt-arrow { display: none; position: absolute; top: 143px; z-index: 2; width: 44px; height: 44px; border-radius: 50%;
              border: 1px solid var(--line); background: var(--lt-surface); color: var(--fg); cursor: pointer; padding: 0;
              align-items: center; justify-content: center; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .lt-arrow:disabled { opacity: .35; cursor: default; }
  #strip-prev { left: 6px; } #strip-next { right: 6px; }
  @media (hover: hover) and (pointer: fine), (min-width: 600px) { .lt-arrow { display: flex; } }
  .lt-scrubwrap { display: flex; flex-direction: column; gap: 4px; }
  .lt-scrub { position: relative; height: 44px; cursor: pointer; touch-action: none; border-radius: 8px; }
  .lt-scrub:focus-visible { outline: 3px solid var(--fg); outline-offset: 2px; }
  .lt-track { position: absolute; left: 0; right: 0; top: 21px; height: 2px; background: var(--line); }
  .lt-tick { position: absolute; width: 2px; transform: translateX(-1px); pointer-events: none; }
  .lt-tick.ch { top: 15px; height: 14px; background: var(--fg); }
  .lt-tick.mo { top: 17px; height: 10px; background: var(--muted); }
  .lt-tick.conv { top: 17px; height: 10px; background: var(--fg); }
  .lt-break { position: absolute; top: 14px; width: 10px; height: 16px; transform: translateX(-5px); background: var(--bg); pointer-events: none; }
  .lt-break::before, .lt-break::after { content: ""; position: absolute; top: 0; width: 2px; height: 16px; background: var(--muted); transform: skewX(-20deg); }
  .lt-break::before { left: 1px; } .lt-break::after { right: 1px; }
  .lt-upring { position: absolute; top: 16px; width: 12px; height: 12px; border-radius: 50%; border: 2px dashed var(--accent);
               box-sizing: border-box; transform: translateX(-6px); background: var(--bg); pointer-events: none; }
  .lt-handle { position: absolute; top: 11px; width: 22px; height: 22px; border-radius: 50%; background: var(--lt-surface);
               border: 2px solid var(--fg); box-sizing: border-box; transform: translateX(-11px); pointer-events: none;
               transition: left .25s ease; }
  .lt-scrub.dragging .lt-handle { transition: none; }
  .lt-labels { position: relative; height: 16px; font-size: 12px; color: var(--muted); }
  .lt-labels span { position: absolute; top: 0; white-space: nowrap; transform: translateX(-50%); }
  .lt-labels span.l { transform: none; } .lt-labels span.r { transform: translateX(-100%); }
  .lt-from { display: flex; flex-direction: column; gap: 10px; }
  .lt-fromhead { display: flex; justify-content: space-between; align-items: center; }
  .lt-kicker { margin: 0; font-size: 12px; font-weight: 600; letter-spacing: .6px; text-transform: uppercase; color: var(--muted); }
  #library a.lt-all { font-size: 14px; font-weight: 500; color: var(--lt-accent-text); text-decoration: none; min-height: 44px;
                      display: inline-flex; align-items: center; }
  .lt-row { display: flex; gap: 12px; align-items: flex-start; padding: 14px; border-radius: 14px; background: var(--lt-surface); }
  .lt-rtime { font-size: 12px; color: var(--muted); min-width: 34px; padding-top: 2px; font-variant-numeric: tabular-nums; }
  .lt-rtext { font-size: 14px; line-height: 1.4; }
  #library a.lt-to, .lt-to { color: var(--lt-accent-text); font-weight: 600; font-size: inherit; text-decoration: none; white-space: nowrap; }
  .lt-empty { margin: 0; font-size: 14px; color: var(--muted); }
  #lib-hint, #live { margin-top: -8px !important; }
  /* the chapter page (sketch D) */
  #lib-page { display: flex; flex-direction: column; gap: 18px; padding: 0 22px; }
  #library a.lt-back { display: inline-flex; align-items: center; gap: 6px; min-height: 44px; font-size: 14px; font-weight: 500;
                       text-decoration: none; color: var(--fg); align-self: flex-start; margin-top: -16px; }
  .lt-phead { display: flex; flex-direction: column; gap: 6px; }
  .lt-pmeta { font-size: 12px; color: var(--muted); }
  #pg-title { margin: 0; font-size: 26px; font-weight: 600; line-height: 1.15; letter-spacing: -0.3px; }
  #pg-summary { margin: 0; font-size: 14px; line-height: 1.45; color: var(--muted); }
  .lt-prog { display: flex; align-items: center; gap: 14px; }
  .lt-bigplay { flex: none; width: 52px; height: 52px; border-radius: 50%; border: 0; background: var(--fg); color: var(--bg);
                display: flex; align-items: center; justify-content: center; cursor: pointer; padding: 0; }
  .lt-bigplay:focus-visible { outline: 3px solid var(--fg); outline-offset: 3px; }
  .lt-progmain { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .lt-bar { position: relative; height: 22px; }
  .lt-bar::before { content: ""; position: absolute; left: 0; right: 0; top: 10px; height: 3px; border-radius: 2px; background: var(--line); }
  .lt-barfill { position: absolute; left: 0; top: 10px; height: 3px; border-radius: 2px; background: var(--fg); width: 0; }
  .lt-mk { position: absolute; top: -11px; width: 44px; height: 44px; margin-left: -22px; border: 0; padding: 0; background: none;
           cursor: pointer; display: flex; align-items: center; justify-content: center; }
  .lt-mk:focus-visible { outline: 3px solid var(--fg); outline-offset: -8px; border-radius: 50%; }
  .lt-mk i { display: block; box-sizing: border-box; }
  .lt-mk.question i, .lt-mk.note i { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--fg); background: var(--bg); }
  .lt-mk.mention i { width: 10px; height: 10px; background: var(--lt-amber); transform: rotate(45deg); }
  .lt-mk.became i { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--accent); background: var(--bg); transform: none; }
  .lt-barlabels { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .lt-moments { display: flex; flex-direction: column; gap: 10px; }
  .lt-mrow { display: flex; flex-direction: column; gap: 6px; padding: 14px; border-radius: 14px; background: var(--lt-surface);
             border: 1px solid transparent; scroll-margin: 90px; transition: box-shadow .4s; }
  .lt-mrow.became { border-color: var(--lt-became); }
  .lt-mrow.flash { box-shadow: 0 0 0 3px var(--accent); }
  .lt-mrow:focus { outline: none; }
  .lt-mhead { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; color: var(--muted); }
  .lt-mout.became, #library a.lt-mout { color: var(--lt-accent-text); font-weight: 600; font-size: 12px; text-decoration: none; text-align: right; }
  .lt-mwords { font-size: 15px; line-height: 1.35; }
  .lt-mreply { font-size: 13px; line-height: 1.4; color: var(--muted); }
  .lt-offer { align-self: flex-start; }
  #library a.lt-tlink { display: flex; justify-content: space-between; align-items: center; min-height: 44px; font-size: 14px;
                        font-weight: 500; text-decoration: none; color: var(--lt-accent-text); }
  `;

  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const NAMES = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
  const ICON_L = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"></path></svg>';
  const ICON_R = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"></path></svg>';
  const ICON_PLAY = '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"></path></svg>';
  const ICON_PAUSE = '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"></path></svg>';
  const MARKUP = `
  <div id="lib-home">
    <div class="lt-head">
      <div><h2 class="lt-title">Library</h2><p class="lt-sub">Swipe through what you’ve explored</p></div>
      <a class="lt-orb" href="/" aria-label="Talk to your guide"></a>
    </div>
    <div class="lt-stripwrap">
      <div id="strip" class="lt-strip" role="region" aria-roledescription="carousel" aria-label="Your chapters, oldest first, then Up next">
        <section class="lt-card lt-up" id="up" aria-label="Up next">
          <p class="lt-upkick" id="up-kicker">Up next · Tonight</p>
          <h3 class="lt-ctitle" id="up-title"></h3>
          <p class="lt-upover" id="up-overview"></p>
          <p class="lt-upsum" id="up-summary"></p>
          <ul class="lib-notes" id="up-notes" aria-label="Your notes for it" hidden></ul>
          <p class="lt-upline" id="up-line" aria-live="polite"></p>
          <div class="lt-uprow"><button class="lt-pill" id="change" hidden>Change</button><button class="lt-link" id="note-open">Add a note</button></div>
        </section>
        <div class="lt-spacer" aria-hidden="true"></div>
      </div>
      <button class="lt-arrow" id="strip-prev" aria-label="Previous chapter">${ICON_L}</button>
      <button class="lt-arrow" id="strip-next" aria-label="Next chapter">${ICON_R}</button>
    </div>
    <p id="lib-hint" hidden>Your guide introduces it live, so Play turns on the microphone.</p>
    <p id="live" aria-live="polite"></p>
    <div id="note-box" hidden>
      <textarea id="note-text" rows="3" aria-label="A note for the next chapter"
                placeholder="Anything for the next chapter: a question to cover, a source, something to leave out"></textarea>
      <div class="lib-row"><button class="lib-pill solid" id="note-send">Add the note</button><button class="lib-link" id="note-cancel">Cancel</button></div>
    </div>
    <p class="lib-line" id="note-msg" hidden></p>
    <div class="lt-scrubwrap">
      <div id="scrubber" class="lt-scrub" role="slider" tabindex="0" aria-label="Your chapters over time">
        <div class="lt-track"></div><div id="scrub-marks"></div><div class="lt-handle" id="scrub-handle"></div>
      </div>
      <div class="lt-labels" id="scrub-labels" aria-hidden="true"></div>
    </div>
    <section class="lt-from" id="from" aria-live="polite">
      <div class="lt-fromhead"><p class="lt-kicker" id="from-kicker">From this chapter</p><a class="lt-all" id="from-all" href="#/">See all</a></div>
      <div id="from-list"></div>
      <p class="lt-empty" id="from-empty" hidden>Nothing saved from this one yet.</p>
    </section>
    <button class="lib-link" id="settings-link">Settings</button>
    <section class="lib-card" id="settings">
      <p class="lib-kicker">Overnight chapters</p>
      <label><input type="checkbox" id="s-enabled"> Build a chapter overnight</label>
      <div class="days" id="s-days"></div>
      <label>Build starts <input type="time" id="s-start"> <span class="note">(changes after this go to the next build)</span></label>
      <label>Plan written <input type="time" id="s-plan"></label>
      <div class="lib-row"><button class="lib-pill" id="s-save">Save</button><span class="note" id="s-note"></span></div>
    </section>
  </div>
  <div id="lib-page" hidden>
    <a href="#/" class="lt-back" id="pg-back">${ICON_L}Library</a>
    <div class="lt-phead"><span class="lt-pmeta" id="pg-meta"></span><h1 id="pg-title"></h1><p id="pg-summary"></p></div>
    <div class="lt-prog">
      <button class="lt-bigplay" id="pg-play" aria-label="Play chapter">${ICON_PLAY}</button>
      <div class="lt-progmain">
        <div class="lt-bar" id="pg-bar"><div class="lt-barfill" id="pg-fill"></div><div id="pg-marks"></div></div>
        <div class="lt-barlabels"><span id="pg-heard"></span><span id="pg-length"></span></div>
      </div>
    </div>
    <section class="lt-moments" aria-labelledby="pg-mkick">
      <p class="lt-kicker" id="pg-mkick">Your moments</p>
      <div id="pg-list"></div>
      <p class="lt-empty" id="pg-empty" hidden>Ask anything while you listen and it’ll show up here.</p>
    </section>
    <a class="lt-tlink" id="pg-transcript" href="#/"><span>Transcript and references</span>${ICON_R}</a>
    <p class="lt-empty" id="pg-demo" hidden>Demo chapter</p>
  </div>
  <div id="lib-chapter" hidden>
    <a href="#/" id="lib-back">← Chapter</a>
    <h1 id="ch-title"></h1>
    <p id="ch-angle"></p>
    <div class="lib-row"><button class="lib-pill solid" id="ch-play">Play</button><span class="lib-meta" id="ch-meta"></span></div>
    <p class="lib-empty" id="ch-problem" hidden></p>
    <div id="ch-layout">
      <div id="ch-text" aria-live="off"></div>
      <aside id="ch-cards"><h3>References</h3><div id="ch-cardlist"></div></aside>
    </div>
  </div>`;
  const PLAYER = `
  <div class="lib-pin">
    <button id="player-toggle" aria-label="Play">▶</button>
    <div class="lib-mid">
      <a id="player-title" href="#/"></a>
      <span id="player-line" aria-live="polite"></span>
      <input id="player-seek" type="range" min="0" max="0" step="0.1" value="0" aria-label="Seek">
    </div>
    <span id="player-time">0:00 / 0:00</span>
    <button class="lib-pill" id="player-ask" hidden>Ask</button>
  </div>
  <audio id="chapter-audio" preload="none"></audio>`;

  const style = document.createElement("style");
  style.textContent = STYLE;
  document.head.appendChild(style);
  let host = $("library");
  if (!host) { host = document.createElement("section"); host.id = "library"; document.body.appendChild(host); }
  document.documentElement.classList.add("has-library");  // index.html leaves past chapters to the library
  if (document.getElementById("orb")) document.documentElement.classList.add("has-orb");  // its own guide circle
  host.innerHTML = MARKUP;
  const player = document.createElement("div");
  player.id = "player";
  player.hidden = true;
  player.innerHTML = PLAYER;
  document.body.appendChild(player);

  const L = { pc: null, dc: null, stream: null, mic: null, session: null, opening: null, connecting: false,
              mode: null, started: false, startedAt: 0,
              out: "", outAt: 0, inp: "", inAt: 0, spokeAt: 0, heardAt: 0, lastGuideAt: 0, lastListenerAt: 0,
              seconds: 0, backend: { model: null, input: 0, output: 0, cached: 0 }, calls: new Set(),
              pending: null, notes: {}, S: {}, how: null, ending: false, toolAt: 0, wrapped: false, startAsked: false,
              run: null, playing: false, aside: null, read: null, epoch: 0, changed: null, rec: null, ac: null,
              loaded: null, button: "Play" };
  window.__library = L;  // for tests
  const audio = $("chapter-audio");
  const pageAudio = $("audio") !== audio ? $("audio") : null;  // index.html's orb chapter, when the page has one
  const remote = new Audio();
  remote.autoplay = true;
  const SUPERSEDED = "superseded";
  let state = null;

  function line(text) { $("live").textContent = text || ""; $("player-line").textContent = text || ""; }
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    if (!res.ok) { let d = ""; try { d = (await res.json()).detail; } catch (e) {} throw new Error(d || `${res.status}`); }
    return res.json();
  }
  const quiet = (url, body) => post(url, body).catch(() => {});
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const plain = (x) => (x || "").replace(/[*_]/g, "");  // markdown emphasis in titles
  function fmt(s) {
    s = Math.max(0, Math.floor(s || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  function lastStarted(items, t) {  // the last item whose start is at or before t, or -1 (items sorted by start)
    let lo = 0, hi = items.length - 1, found = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (items[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1; }
    return found;
  }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function dateLabel(info) {
    const bits = [];
    if (info.date) bits.push(new Date(info.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }));
    if (info.minutes) bits.push(`${info.minutes} min`);
    return bits.join(" · ");
  }
  function infoFor(run) {  // a chapter as /api/library lists it
    const s = state || {};
    const all = [...(s.history || []), ...(s.latest ? [s.latest] : [])];
    return all.find((x) => x.run === run)
      || { run, title: run, overview: "", date: null, minutes: null, audio: `/api/chapters/${encodeURIComponent(run)}/audio`,
           episode: `#/chapter/${encodeURIComponent(run)}`, intro: false };
  }
  // Ask needs the chapter's timed transcript (11_episode/timing.json): `asks` when /api/library gives it,
  // else its read-along (episode.json is built from timing.json).
  const askable = (info) => !!info && (info.asks != null ? !!info.asks : !!info.episode);
  const playingLabel = () => (askable(L.loaded) ? "Ask a question" : "Pause");

  // One voice at a time: the orb's guide (index.html's window.Orb: a voice conversation, or resting with the
  // local listener on) ends before this player plays or a session here opens the microphone.
  function orbOff() { if (window.Orb && Orb.busy()) return Orb.end(); return null; }

  // --- the session ------------------------------------------------------------------------------
  let seq = 0;
  function send(type, fields = {}) {
    if (!L.dc || L.dc.readyState !== "open") return false;
    L.dc.send(JSON.stringify({ type, event_id: `l${++seq}_${now() % 1e7}`, ...fields }));
    return true;
  }
  function mute(on) { if (L.mic) L.mic.enabled = !on; send(on ? "session.input_audio.mute" : "session.input_audio.unmute"); }
  function instruct(text) {  // GPT-Live acts on instructions only with the mic open
    mute(false);
    send("session.instructions.append", { delegation_id: null, content: text });
    if (L.session) quiet("/api/overnight/live/line", { session: L.session, who: "page", text });
  }
  function speakable(text) { send("session.commentary.append", { delegation_id: null, content: text }); }

  async function iceDone(pc, ms) {
    if (pc.iceGatheringState === "complete") return;
    await new Promise((done) => {
      const t = setTimeout(done, ms);
      pc.addEventListener("icegatheringstatechange", () => { if (pc.iceGatheringState === "complete") { clearTimeout(t); done(); } });
    });
  }

  // --- the recording of each conversation (sessions/<id>.webm): never in the conversation's way ------
  function recOpen() {  // before any await: an AudioContext needs the tap's activation
    try {
      if (!window.MediaRecorder || !window.AudioContext) return null;
      L.ac = L.ac || new AudioContext();
      if (L.ac.state === "suspended") L.ac.resume().catch(() => {});
      return { dest: L.ac.createMediaStreamDestination(), nodes: [], mr: null, chunks: [] };
    } catch (e) { return null; }
  }
  function recAdd(rec, stream) {  // the mic, then the guide's voice, mixed into one track
    if (!rec || !stream) return;
    try { const n = L.ac.createMediaStreamSource(stream); n.connect(rec.dest); rec.nodes.push(n); } catch (e) {}
  }
  function recStart(rec) {
    if (!rec || !rec.nodes.length) return;
    try {
      rec.mr = new MediaRecorder(rec.dest.stream);
      rec.mr.ondataavailable = (e) => { if (e.data && e.data.size) rec.chunks.push(e.data); };
      rec.mr.start(1000);
    } catch (e) { rec.mr = null; }
  }
  function recClose(rec, sid) {  // stop; with a session id, upload what was recorded
    if (!rec) return;
    const drop = () => rec.nodes.forEach((n) => { try { n.disconnect(); } catch (e) {} });
    if (!rec.mr || rec.mr.state === "inactive") { drop(); return; }
    rec.mr.onstop = () => {
      drop();
      if (!sid || !rec.chunks.length) return;
      const blob = new Blob(rec.chunks, { type: rec.mr.mimeType || "audio/webm" });
      fetch(`/api/overnight/live/audio/${encodeURIComponent(sid)}`,
            { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob }).catch(() => {});
    };
    try { rec.mr.stop(); } catch (e) { drop(); }
  }

  // A tap while a session is still connecting: the connect in flight sees the epoch move on at its next
  // await, closes its own pieces and ends the server's side if it was opened.
  function abandon() { L.epoch++; L.connecting = false; L.mode = null; L.pending = null; }

  async function connect(mode, body) {
    if (L.connecting) abandon();
    if (L.session) finish("superseded");  // one session at a time (its synchronous part ends it at once)
    const mine = ++L.epoch;
    const gone = () => mine !== L.epoch;
    Object.assign(L, { mode, connecting: true, opening: null, started: false, out: "", inp: "", spokeAt: 0, heardAt: 0,
                       lastGuideAt: 0, lastListenerAt: 0, seconds: 0, backend: { model: null, input: 0, output: 0, cached: 0 },
                       calls: new Set(), pending: null, how: null, ending: false, toolAt: 0, wrapped: false,
                       startAsked: false, changed: null });
    paint();
    const rec = recOpen();
    let stream = null, pc = null, sid = null;
    const check = () => { if (gone()) throw new Error(SUPERSEDED); };
    // Nothing else is heard while the mic is open: the orb's chapter pauses, its guide ends.
    if (pageAudio && !pageAudio.paused) pageAudio.pause();
    try {
      await orbOff();
      check();
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      check();
      L.stream = stream; L.mic = stream.getAudioTracks()[0];
      recAdd(rec, stream);
      pc = new RTCPeerConnection();
      L.pc = pc;
      pc.ontrack = (e) => { if (L.pc !== pc) return; remote.srcObject = e.streams[0]; recAdd(rec, e.streams[0]); };
      pc.addTrack(L.mic, stream);
      const dc = pc.createDataChannel("oai-events");
      L.dc = dc;
      dc.onmessage = (e) => { if (L.dc !== dc) return; try { onEvent(JSON.parse(e.data)); } catch (err) { /* a malformed event: skip it */ } };
      await pc.setLocalDescription(await pc.createOffer());
      check();
      await iceDone(pc, 2500);
      check();
      const got = await post("/api/overnight/live/start", { mode, sdp: pc.localDescription.sdp, ...body });
      sid = got.session;
      check();
      L.opening = sid; L.notes = got.notes || {}; L.S = got.settings || {};
      await pc.setRemoteDescription({ type: "answer", sdp: got.sdp });
      check();
      const t0 = now();
      while (!L.started && !gone() && now() - t0 < 10000) await sleep(50);
      check();
      if (!L.started) throw new Error("Voice couldn't start.");
      Object.assign(L, { session: sid, opening: null, connecting: false, startedAt: now(), rec });
      recStart(rec);
      return got;
    } catch (e) {  // this attempt's pieces only: a newer session may own L's by now
      if (pc) { try { pc.close(); } catch (x) {} }
      if (stream) stream.getTracks().forEach((x) => x.stop());
      if (pc && L.pc === pc) { L.pc = null; L.dc = null; }
      if (stream && L.stream === stream) { L.stream = null; L.mic = null; }
      recClose(rec, null);
      if (sid) {
        if (L.opening === sid) L.opening = null;
        quiet("/api/overnight/live/end", { session: sid, seconds: 0, backend: null, how: gone() ? "abandoned" : "failed" });
      }
      if (gone()) throw new Error(SUPERSEDED);
      L.connecting = false;
      throw e;
    }
  }

  function hangUp() {
    if (L.pc) { try { L.pc.close(); } catch (e) {} }
    L.pc = null; L.dc = null;
    if (L.stream) L.stream.getTracks().forEach((x) => x.stop());
    L.stream = null; L.mic = null;
  }

  // The lines still being heard (the listener's, then the guide's): posted before the session ends, so the
  // last thing said in a short session is kept. Taken out of L at once; the promise settles when both landed.
  function heldLines() {
    const lines = [];
    const inp = L.inp.trim(), out = L.out.trim();
    L.inp = ""; L.out = "";
    if (inp) { L.lastListenerAt = now(); lines.push({ who: "listener", text: inp }); }
    if (out) { L.lastGuideAt = now(); lines.push({ who: "guide", text: out }); if (L.aside) L.aside.lines.push(out); }
    return lines;
  }
  async function flushLines(sid) {
    for (const x of heldLines()) await quiet("/api/overnight/live/line", { session: sid, ...x });
  }

  async function finish(how) {  // close the session, report its cost; the server keeps the transcript
    if (L.ending || !L.session) return null;
    L.ending = true;
    send("session.close");
    const sid = L.session;
    const flushed = flushLines(sid);  // taken now; /end waits for them
    const rec = L.rec; L.rec = null;
    recClose(rec, sid);
    hangUp();
    L.session = null;
    const b = L.backend.model ? { ...L.backend } : null;
    await flushed;
    try { return await post("/api/overnight/live/end", { session: sid, seconds: L.seconds, backend: b, how }); }
    catch (e) { return null; }
  }

  // Closing the tab or leaving the page mid-conversation: end it on the server (cost, aside, memory pass).
  window.addEventListener("pagehide", () => {
    const sid = L.session || L.opening;
    if (!sid) return;
    const b = L.backend.model ? { ...L.backend } : null;
    const beacon = (url, body) => {
      try { navigator.sendBeacon(url, new Blob([JSON.stringify(body)], { type: "application/json" })); } catch (e) {}
    };
    if (L.session) heldLines().forEach((x) => beacon("/api/overnight/live/line", { session: sid, ...x }));
    beacon("/api/overnight/live/end", { session: sid, seconds: L.seconds, backend: b, how: "pagehide" });
    L.epoch++; L.session = null; L.opening = null; L.connecting = false;
    hangUp();
  });

  // --- events -------------------------------------------------------------------------------------
  function onEvent(m) {
    switch (m.type) {
      case "session.started": L.started = true; break;
      case "session.output_transcript.delta": {
        const d = (m.delta || "").replace(/\[[^\]]*\]\s*/g, "");
        L.spokeAt = now();
        if (L.read && L.read.cur) L.read.heard += d;
        else { L.out += d; L.outAt = now(); }
        break;
      }
      case "session.input_transcript.delta": L.inp += m.delta || ""; L.inAt = L.heardAt = now(); break;
      case "response.event": backend(m.event || {}); break;
      case "session.usage.updated": if (m.usage && m.usage.seconds != null) L.seconds = m.usage.seconds; break;
      case "session.closed": if (m.usage && m.usage.seconds != null) L.seconds = m.usage.seconds; break;
    }
  }

  async function backend(e) {
    if (e.type === "response.completed" && e.response && e.response.usage) {
      const u = e.response.usage;
      L.backend.model = e.response.model || L.backend.model;
      L.backend.input += u.input_tokens || 0; L.backend.output += u.output_tokens || 0;
      L.backend.cached += (u.input_tokens_details && u.input_tokens_details.cached_tokens) || 0;
      return;
    }
    if (e.type !== "response.output_item.done" || !e.item || e.item.type !== "function_call") return;
    const item = e.item;
    if (L.calls.has(item.call_id)) return;
    L.calls.add(item.call_id);
    let args = {};
    try { args = JSON.parse(item.arguments || "{}"); } catch (err) {}
    const sid = L.session;
    if (!sid) return;
    let out;
    try { out = await post("/api/overnight/live/tool", { session: sid, name: item.name, args }); }
    catch (err) { out = { error: err.message }; }
    if (sid !== L.session) return;
    send("response.item.create", { item: { type: "function_call_output", call_id: item.call_id, output: JSON.stringify(out) } });
    send("response.create");
    if (item.name === "start_chapter" && L.mode === "intro") { L.toolAt = now(); afterGuide(() => (L.readLive ? readLive("tool") : pivot("tool")), 9000, L.S.intro_quiet_ms || 1500); }
    if (item.name === "resume_chapter" && L.mode === "ask") afterGuide(() => back("guide"), L.S.resume_max_ms || 8000, L.S.resume_quiet_ms || 900);
    if (item.name === "set_next_chapter" && L.mode === "change") { L.changed = out; afterGuide(() => closeChange(out), L.S.change_close_ms || 6000, 1500, 2500); }
  }

  // Run fn once both sides have been quiet for `quietMs` (counted from now or from the guide's last word,
  // whichever is later) and at least `min` ms have passed, or after `max` ms. The lead-in usually comes just
  // before the tool, so speech after the call is not required; `min` leaves room for a line the tool's
  // result asks for (Change: "it'll be the next one").
  function afterGuide(fn, max, quietMs, min = 0) { if (!L.pending) L.pending = { fn, since: now(), max, quietMs, min }; }

  const rx = (k, fallback, flags = "i") => { try { return new RegExp(L.S[k] || fallback, flags); } catch (e) { return new RegExp(fallback, flags); } };

  setInterval(() => {
    const t = now();
    if (!L.session) return;
    const lineQuiet = L.S.line_quiet_ms || 900;
    if (L.inp && t - L.inAt > lineQuiet) {  // the listener's line first: a guide's reply may follow it
      const text = L.inp.trim(); L.inp = "";
      if (text) {
        L.lastListenerAt = t;
        quiet("/api/overnight/live/line", { session: L.session, who: "listener", text });
        if (L.mode === "intro" && startAsked(text)) L.startAsked = true;
      }
    }
    if (L.out && t - L.outAt > lineQuiet) {
      const text = L.out.trim(); L.out = "";
      if (text) {
        L.lastGuideAt = t;
        quiet("/api/overnight/live/line", { session: L.session, who: "guide", text });
        if (L.aside) L.aside.lines.push(text);
        guideSaid(text, t);
      }
    }
    if (L.pending) {
      const p = L.pending;
      const calm = t - Math.max(L.spokeAt, p.since) > p.quietMs && t - L.heardAt > p.quietMs && !L.inp;
      if ((calm && t - p.since >= p.min) || t - p.since > p.max) { L.pending = null; p.fn(); }
    }
    if (L.mode === "intro") introClock(t);
    if (L.mode === "ask") askClock(t);
    if (L.read) readClock(t);
  }, 100);

  const startAsked = (text) => rx("start_asked", "\\b(just play|play it|skip)\\b").test(text);
  function leadIn(text) {  // a lead-in phrase that ends the guide's line (or nearly): "... OK, let's listen."
    const re = rx("lead_in", "\\bhere it is\\b", "gi");
    let m, last = null;
    while ((m = re.exec(text))) { last = m; if (!m[0]) re.lastIndex++; }
    return !!last && text.length - (last.index + last[0].length) <= (L.S.lead_in_tail_chars ?? 40);
  }

  function guideSaid(text, t) {
    // A lead-in counts after intro_min_secs, or at any time once the listener asked to start or skip.
    const early = !(L.startAsked || (L.inp && startAsked(L.inp))) && t - L.startedAt <= (L.S.intro_min_secs ?? 20) * 1000;
    if (L.mode === "intro" && !L.pending && !early && leadIn(text)) {
      afterGuide(() => (L.readLive ? readLive("lead-in") : pivot("lead-in")), 6000, L.S.intro_quiet_ms || 1500);
    }
    if (L.mode === "ask" && L.aside && !L.pending && t - L.aside.t0 > 2000 && rx("lead_back", "\\banyway\\b").test(text)) {
      afterGuide(() => back("lead-back"), L.S.resume_max_ms || 8000, L.S.resume_quiet_ms || 900);
    }
  }

  // --- Play: the intro, then the recording ----------------------------------------------------------
  function introClock(t) {
    const secs = (t - L.startedAt) / 1000, max = L.S.intro_max_secs || 90;
    if (!L.wrapped && secs > max - 30 && !L.pending) { L.wrapped = true; if (L.notes.intro_wrap) instruct(L.notes.intro_wrap); }
    if (secs > max && !L.how) {  // at 90 s at the latest (a listener mid-sentence gets a few seconds more)
      if (L.inp && secs < max + 6) return;
      L.readLive ? readLive("timeout") : pivot("timeout");
    }
  }

  async function pivot(how, at = 0) {  // the intro ends; the recording plays from `at` (a sentence clicked meanwhile)
    if (L.mode !== "intro" || L.how) return;
    L.how = how; L.pending = null;
    if (L.connecting) { abandon(); startRecording(at); return; }  // pressed while connecting: straight to the chapter
    await finish(how);
    L.mode = null;
    startRecording(at);
  }

  function startRecording(at) {  // L.run's recording, from `at`, in the player
    L.loaded = infoFor(L.run);
    if (!audio.src || !audio.src.endsWith(L.audioUrl) || audio.ended) L.pbStart = true;  // a play, for playback.json
    if (!audio.src || !audio.src.endsWith(L.audioUrl)) audio.src = L.audioUrl;
    const seek = () => { try { audio.currentTime = at || 0; } catch (e) {} };
    if (audio.readyState >= 1) seek(); else audio.addEventListener("loadedmetadata", seek, { once: true });
    orbOff();
    audio.play().catch(() => {});  // with preload="none", metadata only arrives once playback is asked for
    L.playing = true;
    line("");
    setButton(playingLabel());
  }

  // The orb's #audio playing this chapter (index.html's ready chapter): the player takes it over where it is
  // (which pauses #audio), so the chapter view's highlight, its Play and the mini player follow it.
  function takeOver(run) {
    if (!pageAudio || pageAudio.paused || L.session || L.connecting) return false;
    let src = "";
    try { src = decodeURIComponent(pageAudio.src || ""); } catch (e) { return false; }
    if (!src.endsWith(`/api/chapters/${run}/audio`)) return false;
    clearFor(run);
    L.run = run; L.audioUrl = infoFor(run).audio;
    startRecording(pageAudio.currentTime);
    return true;
  }

  function setButton(text) { L.button = text; paint(); }

  // Another chapter in the player, or a Change conversation: out of the way before a chapter plays.
  function clearFor(run) {
    if (L.mode === "change") closeChange(L.changed || null);
    if (L.mode === "intro" && L.run !== run) {  // the latest's intro, and they picked another chapter
      L.how = "other"; L.pending = null;
      if (L.connecting) abandon(); else { finish("button"); L.mode = null; }
    }
    if (L.run !== run && !L.mode) { if (!audio.paused) audio.pause(); L.playing = false; L.run = null; L.button = "Play"; }
  }

  async function play(latest) {
    if (!latest) return;
    if (L.mode === "ask") return back("button");
    clearFor(latest.run);  // Play ends a Change conversation first
    if (L.playing && !audio.paused) return askable(L.loaded) ? ask(L.run) : audio.pause();
    if (L.playing) { audio.play().catch(() => {}); setButton(playingLabel()); return; }
    if (L.mode === "read") return stopReading();
    if (L.mode === "intro") return L.readLive ? readLive("button") : pivot("button");  // pressed again: straight to the chapter
    if (takeOver(latest.run)) return;  // already playing from the orb: no intro mid-chapter
    L.run = latest.run; L.audioUrl = latest.audio;
    if (!latest.intro) return startRecording(0);  // an older chapter has no intro brief: just play it
    line("Connecting… your guide introduces this one live.");
    setButton("Skip to the chapter");
    try {
      const got = await connect("intro", { run: latest.run });
      const sid = L.session;
      line("");
      L.readLive = !!got.read_live;
      speakable((L.notes.intro_opened || "").replace("{title}", got.title || latest.title));
      setTimeout(() => { if (L.session === sid && L.mode === "intro" && !L.spokeAt && L.notes.intro_opened) instruct("[" + L.notes.intro_opened.replace("{title}", got.title || latest.title) + "]"); }, L.S.greet_fallback_ms || 5000);
    } catch (e) {
      if (e.message === SUPERSEDED) return;
      L.mode = null;
      startRecording(0);  // no voice: the chapter itself still plays
    }
  }

  // Any chapter, the recording only: Skip the intro, a past chapter's Play, a sentence in the chapter view.
  function playChapter(info, at = 0) {
    if (L.mode === "ask" || L.mode === "read") return;
    if (L.mode === "intro" && L.run === info.run) return pivot("skip", at);  // the intro is on: straight to the chapter
    clearFor(info.run);
    L.run = info.run; L.audioUrl = info.audio;
    startRecording(at);
  }
  // A live Change or intro is closed before the recording plays under it (the same-chapter shortcuts below
  // would otherwise play it with the session's mic still open). True: the intro's own pivot plays it.
  function liveFirst(info, at) {
    if (L.mode === "change") closeChange(L.changed || null);
    if (L.mode === "intro" && L.run === info.run) { pivot("skip", at); return true; }
    return false;
  }
  function playOrPause(info) {  // a chapter's own Play: the one in the player toggles, any other starts
    if (L.mode === "read") return stopReading();
    if (liveFirst(info, 0)) return;
    if (takeOver(info.run)) return;  // playing from the orb: carry on from there, not from the start
    if (L.loaded && L.loaded.run === info.run && L.run === info.run && audio.src) {
      if (L.mode === "ask") return back("button");
      if (audio.paused) { audio.play().catch(() => {}); L.playing = true; setButton(playingLabel()); }
      else audio.pause();
      return;
    }
    playChapter(info, 0);
  }
  function seekTo(info, t) {
    if (L.mode === "ask" || L.mode === "read") return;
    if (liveFirst(info, t)) return;
    if (L.loaded && L.loaded.run === info.run && L.run === info.run && audio.src) {
      try { audio.currentTime = t; } catch (e) {}
      if (audio.paused) { audio.play().catch(() => {}); L.playing = true; setButton(playingLabel()); }
      tick();
      return;
    }
    playChapter(info, t);
  }

  audio.addEventListener("ended", () => { L.playing = false; setButton("Play"); });
  audio.addEventListener("pause", () => { if (!L.aside && L.playing && !audio.ended) setButton("Play"); else paint(); });
  audio.addEventListener("play", () => { orbOff(); if (L.playing) setButton(playingLabel()); else paint(); });

  // The page's own chapter (index.html's orb, #audio) and this player never play over each other, and the
  // orb's chapter never plays under a conversation here.
  if (pageAudio) {
    pageAudio.addEventListener("play", () => { if (!audio.paused) audio.pause(); endLive(); });
    audio.addEventListener("play", () => { if (!pageAudio.paused) pageAudio.pause(); });
  }

  // The orb's guide is starting (index.html), or the orb's chapter started: any conversation here ends and
  // the recording stays paused (Play brings it back where it was).
  async function endLive() {
    if (!L.session && !L.connecting) return;
    if (L.mode === "change") return closeChange(null);
    if (L.mode === "read") return finishReading("orb");
    const intro = L.mode === "intro";
    L.how = L.how || "orb"; L.pending = null; L.aside = null;
    if (L.connecting) abandon();
    const done = finish("orb");
    L.mode = null;
    if (!audio.paused) audio.pause();
    if (intro) L.run = L.loaded ? L.loaded.run : null;  // the player's chapter again, not the latest's intro
    line("");
    setButton("Play");
    await done;
  }

  // --- Ask: a tap while the recording plays -------------------------------------------------------
  async function ask(run) {
    if (!askable(L.loaded)) return;  // no timed transcript: nothing for the guide to know where they are
    const t = audio.currentTime;
    audio.pause();
    L.aside = { t0: now(), t, lines: [], silenced: false };
    setButton("Back to the chapter");
    line("Connecting…");
    try {
      await connect("ask", { run, t });
      line("");
      instruct(L.notes.ask_opened || "[The listener tapped to ask something.]");
    } catch (e) {
      if (e.message === SUPERSEDED) return;  // "Back to the chapter" while it connected: back() resumed it
      L.aside = null; L.mode = null;
      flashLine(e.message || "Voice couldn't start.");  // why it didn't open, for a few seconds
      audio.play().catch(() => {});  // no voice: back to the chapter where it paused
    }
  }
  let flashTimer = 0;
  function flashLine(text) {
    line(text);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { if ($("player-line").textContent === text) line(""); }, 5000);
  }

  function askClock(t) {
    const a = L.aside;
    if (!a || a.silenced || L.pending) return;
    const last = Math.max(L.lastGuideAt, L.lastListenerAt, L.spokeAt, L.heardAt);
    if (L.lastGuideAt && last && t - last > (L.S.aside_silence_secs || 8) * 1000) { a.silenced = true; instruct(L.notes.silence || "[Silence.]"); }
  }

  async function back(how) {
    if (L.mode !== "ask" || !L.aside) return;
    const a = L.aside;
    L.aside = null; L.pending = null;  // a second tap while it ends does nothing
    if (L.connecting) { abandon(); startRecording(a.t); return; }
    const out = await finish(how);
    L.mode = null;
    const at = out && out.restart ? out.restart.t : a.t;
    startRecording(at);
  }

  // --- Change: what tonight's chapter should be ---------------------------------------------------
  async function openChange(up) {
    if (L.session || L.connecting || L.mode) return;  // one conversation at a time
    if (L.playing && !audio.paused) audio.pause();    // the recording waits while they talk it over
    const btn = $("change");
    btn.disabled = true;
    const before = up.card ? up.card.id : null;
    $("up-line").textContent = "Connecting…";
    try {
      const got = await connect("change", {});
      $("up-line").textContent = "Talking it over… (tap Change again to finish)";
      btn.disabled = false; btn.textContent = "Done";
      speakable((L.notes.change_opened || "").replace("{title}", got.title || "tonight's plan"));
      L.changeBefore = before;
    } catch (e) {
      btn.disabled = false;
      if (e.message === SUPERSEDED) return;  // Play ended it while it connected; closeChange reloads the card
      $("up-line").textContent = e.message || "Voice couldn't start.";
      L.mode = null;
    }
  }

  async function closeChange(out) {
    if (L.mode !== "change") return;
    L.changed = null; L.pending = null;
    if (L.connecting) abandon();
    L.mode = null;  // at once: Play may start the intro straight after this call
    const btn = $("change");
    btn.textContent = "Change"; btn.disabled = false;
    await finish(out ? "tool" : "button");
    if (out && out.changing) {
      $("up-line").textContent = "Rewriting the card…";
      const t0 = now();
      while (now() - t0 < 120000) {
        await sleep(3000);
        const s = await (await fetch("/api/library")).json().catch(() => null);
        if (s && s.up_next.card && s.up_next.card.id !== L.changeBefore) { load(); return; }
      }
    }
    load();
  }

  // --- read live (settings playback.read_live): the guide reads the chapter after the intro -----------
  // The same session switches into reading mode: the mic is muted and the chapter goes one paragraph at a
  // time as speakable context; the next is sent once the transcript has heard the current one through
  // (about 90% of its words and a short quiet), or after a longer quiet. A tap stops the reading and ends
  // the session (asking mid-reading is the live-voice page's, once merged).
  async function readLive(how) {
    if (L.mode !== "intro" || L.how) return;
    L.how = how; L.pending = null; L.mode = "read";
    let paras = [];
    try { paras = (await (await fetch(`/api/overnight/paragraphs/${L.run}`)).json()).paragraphs || []; } catch (e) {}
    if (!paras.length) { L.mode = "intro"; L.how = null; return pivot(how); }
    mute(true);
    L.read = { items: paras, next: 0, cur: null, heard: "", sentAt: 0 };
    setButton("Stop");
    readNext();
  }
  const words = (s) => (s.toLowerCase().match(/[a-z0-9']+/g) || []);
  function readNext() {
    const r = L.read;
    if (!r) return;
    if (r.next >= r.items.length) { finishReading("read-live-done"); return; }
    r.cur = r.items[r.next++];
    r.heard = ""; r.sentAt = now(); r.n = words(r.cur.text).length;
    speakable(`${L.notes.read || "Read this passage aloud now, word for word:"} ${r.cur.text}`);
  }
  function readClock(t) {
    const r = L.read;
    if (!r || !r.cur) return;
    const got = words(r.heard).length, quietFor = t - Math.max(L.spokeAt, r.sentAt);
    if ((got >= 0.9 * r.n && quietFor > 700) || (got >= 0.6 * r.n && quietFor > 2500) || quietFor > 5000) {
      r.cur = null;
      const gap = { section: 1200, beat: 900 }[(r.items[r.next] || {}).pause] || 400;
      setTimeout(readNext, gap);
    }
  }
  function stopReading() { instruct(L.notes.read_stop || "[Stop reading now.]"); finishReading("read-live-stopped"); }
  async function finishReading(how) {
    L.read = null;
    await finish(how);
    L.mode = null;
    setButton("Play");
  }

  // --- the library timeline (docs/library-timeline.md): the strip, the scrubber, From this chapter --------------
  // /api/library's `timeline` lists every chapter oldest first, each with its moments, playback and status line.
  // The strip is one card per chapter, then Up next; the scrubber is the same chapters on a line of real dates
  // (a gap over 7 days squeezed to a fixed width with a break mark). Both always show the same chapter: T.i.
  function show(e, on) { if (e) e.hidden = !on; }
  const T = { items: [], i: -1, key: "", target: null, targetAt: 0, xs: [], ticks: [], drag: null, tl: null };
  const BREAK_DAYS = 3;  // the width, in days, a gap over 7 days is squeezed to
  const PAD = 3;         // % kept clear at each end of the scrubber, for the handle
  const dayNum = (iso) => Math.round(Date.parse(`${iso}T12:00:00Z`) / 864e5);
  const isoOf = (n) => new Date(n * 864e5).toISOString().slice(0, 10);
  const shortDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const cardDate = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) : "");
  const chapterRows = () => ((state && state.timeline) || {}).chapters || [];
  const rowFor = (run) => chapterRows().find((c) => c.run === run) || null;
  const statusLine = (c) => [c.status_line, c.counts].filter(Boolean).join(" · ");

  function renderTimeline(s) {
    const tl = s.timeline || { chapters: [] };
    T.tl = tl;
    const key = JSON.stringify([tl.chapters.map((c) => [c.run, c.title, c.date, c.minutes, c.color, c.playable, c.status_line, c.counts]),
                                tl.first, tl.tonight, !!(s.latest && s.latest.intro)]);
    const rebuilt = key !== T.key;
    if (rebuilt) { T.key = key; buildStrip(tl, s); }
    buildScrubber(tl);  // cheap, and the width may have changed
    renderFrom();
    const pageKey = view.page ? JSON.stringify([rowFor(view.page), tl.guide, tl.up_next_short]) : "";
    if (view.page && pageKey !== T.pageKey) showPage(view.page);  // its moments or progress changed
  }

  function buildStrip(tl, s) {
    const strip = $("strip"), up = $("up");
    const before = T.items[T.i] ? (T.items[T.i].run || "up") : null;
    const fe = document.activeElement, fcard = fe && fe.closest && fe.closest(".lt-ch");
    const refocus = fcard ? { run: fcard.dataset.run, sel: fe.id ? `#${fe.id}` : "a.lt-ctitle" } : null;
    strip.querySelectorAll(".lt-ch").forEach((e) => e.remove());
    T.items = tl.chapters.map((c, i) => {
      const card = el("article", `lt-card lt-ch ${c.color || "teal"}`);
      card.dataset.run = c.run; card.dataset.i = i;
      card.setAttribute("aria-label", `${plain(c.title)}, ${cardDate(c.date)}`);
      const date = el("span", "lt-cdate", [cardDate(c.date), c.minutes ? `${c.minutes} min` : null].filter(Boolean).join(" · "));
      const bottom = el("div", "lt-cbottom");
      const title = el("a", "lt-ctitle", plain(c.title));
      title.href = `#/chapter/${encodeURIComponent(c.run)}`;
      bottom.append(title, el("span", "lt-cstatus", statusLine(c)));
      if (c.playable) {  // the latest chapter: Play starts the morning flow (the guide's intro, then the recording)
        date.id = "latest-meta"; title.id = "latest-title";
        const row = el("div", "lt-crow");
        const b = el("button", "lt-play", "Play"); b.id = "lib-play";  // not "play": the orb page has its own
        b.addEventListener("click", () => { if (state) play(state.latest); });
        const skip = el("button", "lt-link", "Skip the intro"); skip.id = "lib-skip"; skip.hidden = true;
        skip.addEventListener("click", () => { if (state && state.latest) playChapter(state.latest, 0); });
        row.append(b, skip);
        bottom.appendChild(row);
      }
      card.append(date, bottom);
      strip.insertBefore(card, up);
      return { run: c.run, el: card, c };
    });
    up.dataset.i = T.items.length;
    T.items.push({ run: null, el: up, c: null });
    let i = before == null ? -1 : T.items.findIndex((x) => (x.run || "up") === before);
    if (i < 0) {  // first time: the latest chapter, else the last one, else Up next
      i = T.items.findIndex((x) => x.run && x.run === tl.latest);
      if (i < 0) i = Math.max(0, T.items.length - 2);
      if (!tl.chapters.length) i = 0;
    }
    T.i = -1;
    if (refocus) {  // a poll rebuilt the cards under the keyboard: back to the same control
      const it = T.items.find((x) => x.run === refocus.run);
      const f = it && (it.el.querySelector(refocus.sel) || it.el.querySelector("a.lt-ctitle"));
      if (f) f.focus({ preventScroll: true });
    }
    requestAnimationFrame(() => goTo(i, false));
  }

  function offsetOf(i) { return T.items[i].el.offsetLeft - T.items[0].el.offsetLeft; }

  function goTo(i, smooth = true) {
    if (!T.items.length) return;
    i = Math.max(0, Math.min(T.items.length - 1, i));
    const strip = $("strip");
    T.target = i; T.targetAt = now();
    strip.scrollTo({ left: offsetOf(i), behavior: smooth ? "smooth" : "auto" });
    setIndex(i);
  }

  function setIndex(i) {
    if (i === T.i) return;
    T.i = i;
    host.dataset.card = String(i);  // for tests
    $("strip-prev").disabled = i <= 0;
    $("strip-next").disabled = i >= T.items.length - 1;
    T.items.forEach((x, k) => x.el.classList.toggle("lt-current", k === i));
    placeHandle();
    renderFrom();
    paint();  // the intro hint follows the card in view
  }

  function onStripScroll() {
    if (!T.items.length) return;
    const left = $("strip").scrollLeft;
    let best = 0;
    T.items.forEach((x, k) => { if (Math.abs(offsetOf(k) - left) < Math.abs(offsetOf(best) - left)) best = k; });
    if (T.target != null) {  // a scroll we asked for: let it arrive before following it
      if (best !== T.target && now() - T.targetAt < 2000) return;
      T.target = null;
    }
    setIndex(best);
  }
  let stripRaf = 0;
  $("strip").addEventListener("scroll", () => { cancelAnimationFrame(stripRaf); stripRaf = requestAnimationFrame(onStripScroll); }, { passive: true });
  ["pointerdown", "touchstart", "wheel"].forEach((ev) => $("strip").addEventListener(ev, () => { T.target = null; }, { passive: true }));
  $("strip-prev").addEventListener("click", () => goTo(T.i - 1));
  $("strip-next").addEventListener("click", () => goTo(T.i + 1));
  $("strip").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    if (e.target.closest && e.target.closest("textarea, input")) return;
    e.preventDefault();
    const inCard = e.target.closest && e.target.closest(".lt-card");
    goTo(T.i + (e.key === "ArrowRight" ? 1 : -1));
    if (inCard) {  // keep the keyboard on the card that came into view
      const it = T.items[T.i];
      const f = it && (it.el.querySelector("a.lt-ctitle") || it.el.querySelector("button:not([hidden])"));
      if (f) f.focus({ preventScroll: true });
    }
  });

  // The scrubber. x positions in % of its width, from real dates; ticks for the first conversation, each chapter
  // (tall), each moment (short, just after its chapter's), Up next's dashed ring at Tonight.
  function scale(tl) {
    const days = new Set(tl.chapters.filter((c) => c.date).map((c) => dayNum(c.date)));
    if (tl.first && tl.first.date) days.add(dayNum(tl.first.date));
    days.add(dayNum(tl.tonight));
    const ds = [...days].sort((a, b) => a - b);
    const cum = [0], breaks = [];
    for (let k = 1; k < ds.length; k++) {
      const g = ds[k] - ds[k - 1], w = g > 7 ? BREAK_DAYS : g;
      cum.push(cum[k - 1] + w);
      if (g > 7) breaks.push(cum[k - 1] + w / 2);
    }
    const W = cum[cum.length - 1] || 1;
    const pct = (x) => PAD + (x / W) * (100 - 2 * PAD);
    const at = (d) => {  // a day (fractional within a gap) on the squeezed line
      if (ds.length < 2) return 100 - PAD;
      if (d <= ds[0]) return pct(0);
      for (let k = 1; k < ds.length; k++) {
        if (d <= ds[k]) { const g = ds[k] - ds[k - 1]; return pct(cum[k - 1] + ((d - ds[k - 1]) / g) * (cum[k] - cum[k - 1])); }
      }
      return pct(W);
    };
    return { at, breaks: breaks.map(pct), ds };
  }

  function buildScrubber(tl) {
    const marks = $("scrub-marks");
    const sc = scale(tl);
    const parts = [], ticks = [];
    T.xs = [];
    tl.chapters.forEach((c, i) => {
      const d0 = dayNum(c.date || tl.tonight), x = sc.at(d0);
      T.xs.push(x);
      ticks.push({ x, i });
      parts.push(`<div class="lt-tick ch" style="left:${x}%"></div>`);
      (c.moments || []).forEach((m) => {
        const f = c.duration_s ? Math.min(1, (m.position_s || 0) / c.duration_s) : 0.5;
        const mx = sc.at(d0 + 0.2 + 0.55 * f);
        ticks.push({ x: mx, i });
        parts.push(`<div class="lt-tick mo" style="left:${mx}%"></div>`);
      });
    });
    if (tl.first && tl.first.date) parts.unshift(`<div class="lt-tick conv" style="left:${sc.at(dayNum(tl.first.date))}%"></div>`);
    sc.breaks.forEach((x) => parts.push(`<div class="lt-break" style="left:${x}%"></div>`));
    const tx = sc.at(dayNum(tl.tonight));
    T.xs.push(tx);
    ticks.push({ x: tx, i: tl.chapters.length });
    parts.push(`<div class="lt-upring" style="left:${tx}%"></div>`);
    const html = parts.join("");
    if (marks.dataset.html !== html) { marks.innerHTML = html; marks.dataset.html = html; }
    T.ticks = ticks;
    renderLabels(tl, sc);
    const sl = $("scrubber");
    sl.setAttribute("aria-valuemin", "1");
    sl.setAttribute("aria-valuemax", String(T.items.length || 1));
    placeHandle();
  }

  function renderLabels(tl, sc) {
    const cands = [];
    const dated = tl.chapters.filter((c) => c.date).map((c) => c.date);
    const firstDate = (tl.first && tl.first.date) || dated[0];
    if (firstDate) cands.push({ x: sc.at(dayNum(firstDate)), text: shortDate(firstDate) });
    let month = firstDate ? firstDate.slice(0, 7) : null;
    dated.forEach((d) => { if (d.slice(0, 7) !== month) { month = d.slice(0, 7); cands.push({ x: sc.at(dayNum(d)), text: shortDate(d) }); } });
    const weekStart = dayNum(tl.tonight) - 7;
    const wk = dated.find((d) => dayNum(d) > weekStart);
    if (wk) cands.push({ x: sc.at(dayNum(wk)), text: "This week" });
    cands.push({ x: sc.at(dayNum(tl.tonight)), text: "Tonight" });
    const width = $("scrubber").offsetWidth || 346;
    const minGap = (70 / width) * 100;
    const kept = [];  // from the newest back: the oldest are dropped when crowded, and at most five
    for (let k = cands.length - 1; k >= 0; k--) {
      const c = cands[k];
      if (kept.length && kept[kept.length - 1].x - c.x < minGap) continue;
      kept.push(c);
      if (kept.length === 5) break;
    }
    kept.reverse();
    $("scrub-labels").replaceChildren(...kept.map((c) => {
      const e = el("span", c.x < 10 ? "l" : c.x > 90 ? "r" : null, c.text);
      e.style.left = `${c.x < 10 ? Math.max(0, c.x - 1) : c.x > 90 ? Math.min(100, c.x + 1) : c.x}%`;
      return e;
    }));
  }

  function placeHandle(x) {
    const h = $("scrub-handle"), sl = $("scrubber");
    if (x == null) x = T.xs[T.i];
    if (x == null) { h.hidden = true; return; }
    h.hidden = false;
    h.style.left = `${x}%`;
    const it = T.items[T.i];
    if (it) {
      sl.setAttribute("aria-valuenow", String(T.i + 1));
      sl.setAttribute("aria-valuetext", it.c ? `${plain(it.c.title)}, ${cardDate(it.c.date)}` : "Up next, tonight");
    }
  }

  function pctAt(e) { const r = $("scrubber").getBoundingClientRect(); return ((e.clientX - r.left) / r.width) * 100; }
  function nearest(x, ticks) { let b = null; ticks.forEach((t) => { if (!b || Math.abs(t.x - x) < Math.abs(b.x - x)) b = t; }); return b; }
  const scrub = $("scrubber");
  scrub.addEventListener("pointerdown", (e) => {
    if (!T.items.length) return;
    T.drag = { x0: e.clientX, moved: false };
    try { scrub.setPointerCapture(e.pointerId); } catch (err) {}
    scrub.focus({ preventScroll: true });
  });
  scrub.addEventListener("pointermove", (e) => {
    if (!T.drag) return;
    if (!T.drag.moved && Math.abs(e.clientX - T.drag.x0) < 5) return;
    T.drag.moved = true;
    scrub.classList.add("dragging");
    const x = Math.max(0, Math.min(100, pctAt(e)));
    const n = nearest(x, T.xs.map((tx, i) => ({ x: tx, i })));  // the handle marks a chapter (or Up next)
    if (n && n.i !== T.i) goTo(n.i, false);
    placeHandle(x);
  });
  function endDrag(e) {
    if (!T.drag) return;
    const tap = !T.drag.moved;
    T.drag = null;
    scrub.classList.remove("dragging");
    if (tap && e && e.type === "pointerup") {  // a tap on a tick, a chapter's or a moment's: that chapter
      const n = nearest(pctAt(e), T.ticks);
      if (n) goTo(n.i);
    }
    placeHandle();
  }
  scrub.addEventListener("pointerup", endDrag);
  scrub.addEventListener("pointercancel", endDrag);
  scrub.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -3, PageUp: 3 }[e.key];
    if (step) { e.preventDefault(); goTo(T.i + step); }
    else if (e.key === "Home") { e.preventDefault(); goTo(0); }
    else if (e.key === "End") { e.preventDefault(); goTo(T.items.length - 1); }
  });
  addEventListener("resize", () => { if (T.tl) { buildScrubber(T.tl); if (T.items.length) $("strip").scrollTo({ left: offsetOf(T.i) }); } });

  // A moment as a line: "You asked: “…”", "You mentioned *Upstream*, Mary Oliver", "You saved: “…”".
  function momentText(m, into) {
    if (m.kind === "mention" && m.title) {
      into.append("You mentioned ", el("em", null, m.title), m.creator ? `, ${m.creator}` : "");
    } else {
      into.append(`${m.kind === "note" ? "You saved" : m.kind === "mention" ? "You mentioned" : "You asked"}: “${m.text}”`);
    }
  }
  function outcomeTo(m) {  // where a moment's outcome points: "→ Tonight", or a past chapter, linked
    if (m.tonight) return el("span", "lt-to", "→ Tonight");
    if (m.outcome_run) {
      const a = el("a", "lt-to", `→ ${plain((rowFor(m.outcome_run) || {}).title || m.outcome_label).split(":")[0]}`);
      a.href = `#/chapter/${encodeURIComponent(m.outcome_run)}`;
      return a;
    }
    return null;
  }
  function pickThree(ms) {  // up to three, in time order; one that became a chapter is never left out
    const became = ms.filter((m) => m.outcome === "became_chapter");
    const rest = ms.filter((m) => m.outcome !== "became_chapter");
    const take = [...became.slice(0, 3), ...rest].slice(0, 3);
    return take.sort((a, b) => (a.position_s || 0) - (b.position_s || 0));
  }

  function renderFrom() {
    const it = T.items[T.i];
    const list = $("from-list");
    if (!it) { show($("from"), false); return; }
    show($("from"), true);
    let ms, kicker = "From this chapter";
    const c = it.run ? rowFor(it.run) || it.c : null;  // the latest state's row: its moments may have changed
    if (c) {
      ms = pickThree(c.moments || []);
      $("from-all").href = `#/chapter/${encodeURIComponent(c.run)}`;
      show($("from-all"), true);
    } else {  // Up next: the moment it came from, when it came from one
      ms = [];
      chapterRows().forEach((c) => (c.moments || []).forEach((m) => { if (m.tonight) ms.push({ ...m, _from: c }); }));
      kicker = "Where it came from";
      show($("from-all"), false);
    }
    $("from-kicker").textContent = kicker;
    const key = JSON.stringify([T.i, kicker, ms.map((m) => [m.id, m.outcome_label, m._from && m._from.run])]);
    if (list.dataset.key !== key) {
      list.dataset.key = key;
      list.replaceChildren(...ms.map((m) => {
        const row = el("div", "lt-row");
        row.dataset.moment = m.id;
        const text = el("span", "lt-rtext");
        momentText(m, text);
        const to = c ? outcomeTo(m) : null;
        if (to) text.append(" ", to);
        if (m._from) text.append(` (${plain(m._from.title).split(":")[0]})`);
        row.append(el("span", "lt-rtime", fmt(m.position_s)), text);
        return row;
      }));
    }
    show($("from-empty"), !!c && ms.length === 0);
    if (!c && !ms.length) show($("from"), false);
  }

  // --- the chapter page (#/chapter/<run>, sketch D) ---------------------------------------------------------
  function showPage(run) {
    view.page = run;
    T.pageKey = JSON.stringify([rowFor(run), ((state || {}).timeline || {}).guide, ((state || {}).timeline || {}).up_next_short]);
    const c = rowFor(run);
    const info = infoFor(run);
    const s = state || {};
    const guide = (s.timeline || {}).guide;
    if (!c) {  // not (yet) in the library: what /api/library says about it
      $("pg-meta").textContent = dateLabel(info);
      $("pg-title").textContent = plain(info.title);
      $("pg-summary").textContent = info.overview || "";
      show($("pg-play"), false);
      $("pg-list").replaceChildren(); $("pg-marks").replaceChildren();
      show($("pg-empty"), true);
      return;
    }
    $("pg-meta").textContent = [cardDate(c.date), c.minutes ? `${c.minutes} min` : null, guide ? `with ${guide}` : null].filter(Boolean).join(" · ");
    $("pg-title").textContent = plain(c.title);
    $("pg-summary").textContent = c.summary || c.overview || "";
    show($("pg-play"), !!c.playable);
    $("pg-length").textContent = c.duration_s ? fmt(c.duration_s) : "";
    const marks = (c.moments || []).map((m) => {
      const b = el("button", `lt-mk ${m.outcome === "became_chapter" ? "became" : m.kind}`);
      b.style.left = `${c.duration_s ? Math.min(100, (100 * (m.position_s || 0)) / c.duration_s) : 0}%`;
      b.dataset.moment = m.id;
      b.setAttribute("aria-label", `${fmt(m.position_s)}: ${m.kind === "mention" ? "you mentioned" : m.kind === "note" ? "you saved" : "you asked"} ${m.kind === "mention" && m.title ? m.title : m.text}`);
      b.appendChild(el("i"));
      b.addEventListener("click", () => toMoment(c, m));
      return b;
    });
    $("pg-marks").replaceChildren(...marks);
    $("pg-list").replaceChildren(...(c.moments || []).map((m) => momentRow(c, m, guide)));
    show($("pg-empty"), !(c.moments || []).length);
    $("pg-transcript").href = `#/chapter/${encodeURIComponent(c.run)}/transcript`;
    show($("pg-transcript"), !!c.episode);
    show($("pg-demo"), !c.episode);
    $("pg-demo").textContent = c.demo ? "Demo chapter" : "No transcript yet";
    pageProgress();
  }

  function momentRow(c, m, guide) {
    const row = el("div", `lt-mrow${m.outcome === "became_chapter" ? " became" : ""}`);
    row.id = `moment-${m.id}`; row.tabIndex = -1; row.dataset.moment = m.id;
    const head = el("div", "lt-mhead");
    const kind = { question: "You asked", mention: "You mentioned", note: "You saved" }[m.kind] || "";
    head.appendChild(el("span", null, `${fmt(m.position_s)} · ${kind}`));
    if (m.outcome_run) {
      const a = el("a", "lt-mout", plain(m.outcome_label)); a.href = `#/chapter/${encodeURIComponent(m.outcome_run)}`;
      head.appendChild(a);
    } else head.appendChild(el("span", `lt-mout${m.outcome === "became_chapter" ? " became" : ""}`, m.outcome_label || ""));
    row.appendChild(head);
    const words = el("span", "lt-mwords");
    if (m.kind === "mention" && m.title) words.append(el("em", null, m.title), m.creator ? `, ${m.creator}` : "");
    else words.textContent = `“${m.text}”`;
    row.appendChild(words);
    let second = m.reply ? `${guide || "Your guide"}: ${m.reply}` : null;
    if (!second && m.tonight) second = `Saved for later. Up next: ${plain(((state || {}).timeline || {}).up_next_short || "")}.`;
    if (second) row.appendChild(el("span", "lt-mreply", second));
    if (c.playable) {
      const b = el("button", "lt-pill lt-offer", `Play from ${fmt(Math.max(0, (m.position_s || 0) - 10))}`);
      b.hidden = true;
      b.addEventListener("click", () => seekTo(infoFor(c.run), Math.max(0, (m.position_s || 0) - 10)));
      row.appendChild(b);
    }
    return row;
  }

  function toMoment(c, m) {  // a marker tapped: its moment comes into view; on the playable chapter, an offer to play
    const row = document.getElementById(`moment-${m.id}`);
    if (!row) return;
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    row.classList.add("flash");
    setTimeout(() => row.classList.remove("flash"), 1600);
    const offer = row.querySelector(".lt-offer");
    $("pg-list").querySelectorAll(".lt-offer").forEach((b) => { if (b !== offer) b.hidden = true; });
    if (offer) { offer.hidden = false; offer.focus({ preventScroll: true }); } else row.focus({ preventScroll: true });
  }

  function pageProgress() {  // the bar filled to the furthest point heard (or further, while it plays here)
    const c = view.page && rowFor(view.page);
    if (!c) return;
    const pb = c.playback || {};
    const here = L.loaded && L.loaded.run === c.run && audio.src;
    const furthest = Math.max(pb.furthest_s || 0, here ? audio.currentTime || 0 : 0);
    const d = c.duration_s || (here && Number.isFinite(audio.duration) ? audio.duration : 0);
    const finished = pb.finished || (d && furthest >= 0.95 * d);
    $("pg-fill").style.width = `${finished ? 100 : d ? Math.min(100, (100 * furthest) / d) : 0}%`;
    const heard = Math.max(1, Math.floor(furthest / 60));
    $("pg-heard").textContent = finished ? "Finished" : furthest >= 30 ? (d ? `${heard} of ${Math.round(d / 60)} min heard` : `${heard} min heard`) : "Not started";
    const playing = here && !audio.paused;
    const b = $("pg-play");
    b.innerHTML = playing ? ICON_PAUSE : ICON_PLAY;
    b.setAttribute("aria-label", playing ? "Pause chapter" : "Play chapter");
  }
  $("pg-play").addEventListener("click", () => {
    const c = view.page && rowFor(view.page);
    if (!c || !c.playable || !state) return;
    if (L.loaded && L.loaded.run === c.run && audio.src && !L.mode) return playOrPause(infoFor(c.run));
    play(state.latest);  // the latest chapter's Play: the morning flow, as on its card
  });

  // --- the player's progress: playback.json (every 10 seconds while playing, and on pause and at the end) ------
  let reportedAt = 0;
  function report(start) {
    if (!L.loaded || !audio.src) return;
    reportedAt = now();
    const d = Number.isFinite(audio.duration) ? audio.duration : null;
    const run = L.loaded.run;
    post(`/api/chapters/${encodeURIComponent(run)}/playback`, { t: audio.currentTime || 0, duration: d, start: !!start })
      .then((pb) => {  // the card's status line and the page's bar follow at once, not at the next poll
        const c = rowFor(run);
        if (c) { c.playback = pb; if (pb.finished && c.status !== "finished") load(); }
        pageProgress();
      }).catch(() => {});
  }
  audio.addEventListener("playing", () => { if (L.pbStart) { L.pbStart = false; report(true); } });
  audio.addEventListener("timeupdate", () => { if (!audio.paused && now() - reportedAt >= 10000) report(false); pageProgress(); });
  audio.addEventListener("pause", () => { report(false); setTimeout(load, 400); });
  audio.addEventListener("ended", () => report(false));
  addEventListener("pagehide", () => {
    if (!L.loaded || !audio.src || audio.paused) return;
    const d = Number.isFinite(audio.duration) ? audio.duration : null;
    try { navigator.sendBeacon(`/api/chapters/${encodeURIComponent(L.loaded.run)}/playback`,
                                new Blob([JSON.stringify({ t: audio.currentTime || 0, duration: d })], { type: "application/json" })); } catch (e) {}
  });

  // --- the Up next card and the rest of the page ------------------------------------------------------------
  function render(s) {
    state = s;
    const up = s.up_next, card = up.card;
    host.classList.remove("planned", "building", "failed", "waiting", "none", "off");
    host.classList.add(up.state);
    $("up-title").textContent = card ? card.title : (up.state === "off" ? "Overnight chapters are off" : "Nothing planned yet");
    $("up-overview").textContent = card ? (card.overview || "") : "";
    $("up-summary").textContent = card ? (card.summary || "") : "";
    $("up-kicker").textContent = card ? "Up next · Tonight" : "Up next";
    const notes = (card && card.notes_added) || [];
    $("up-notes").replaceChildren(...notes.map((n) => el("li", null, n.text || "")));
    show($("up-notes"), notes.length > 0);
    if (L.mode !== "change") $("up-line").textContent = up.line || "";
    show($("change"), !!up.change);
    show($("note-open"), up.state !== "off");
    renderTimeline(s);
    if (!host.classList.contains("lib-settings-open")) fillSettings(s.settings);
    paint();
  }

  function fillSettings(st) {
    if (!st) return;
    const o = st.overnight;
    $("s-enabled").checked = !!o.enabled;
    $("s-start").value = o.start;
    $("s-plan").value = o.plan_at;
    $("s-days").innerHTML = DAYS.map((d) =>
      `<label><input type="checkbox" value="${d}" ${o.days.includes(d) ? "checked" : ""}> ${NAMES[d]}</label>`).join("");
  }

  // Everything that follows the player's state: the latest card's button and Skip, the history's rows, the
  // chapter view's Play, the mini player.
  function paint() {
    const l = state && state.latest;
    const mine = !!l && L.run === l.run;
    const pb = $("lib-play");
    if (pb) pb.textContent = mine || L.mode === "ask" ? L.button : "Play";
    const offerIntro = !!(l && l.intro) && !(mine && (L.playing || L.mode));
    show($("lib-skip"), offerIntro);
    show($("lib-hint"), offerIntro && T.items[T.i] && T.items[T.i].run === l.run);
    const on = L.loaded && !audio.paused ? L.loaded.run : null;
    if (view.run) $("ch-play").textContent = on === view.run ? "Pause" : "Play";
    pageProgress();
    const info = L.loaded;
    player.hidden = !info;
    if (info) {
      $("player-title").textContent = plain(info.title);
      $("player-title").href = `#/chapter/${encodeURIComponent(info.run)}`;
      $("player-toggle").textContent = audio.paused ? "▶" : "❚❚";
      $("player-toggle").setAttribute("aria-label", audio.paused ? "Play" : "Pause");
      const asking = L.mode === "ask";
      show($("player-ask"), asking || (L.playing && !audio.paused && askable(info)));
      $("player-ask").textContent = asking ? "Back to the chapter" : "Ask";
    }
    document.documentElement.style.setProperty("--lib-player-h", player.hidden ? "0px" : `${player.offsetHeight}px`);
  }

  let dragging = false;
  function tick() {
    const d = Number.isFinite(audio.duration) ? audio.duration : 0;
    const t = audio.currentTime || 0;
    $("player-seek").max = d;
    if (!dragging) $("player-seek").value = t;
    $("player-time").textContent = `${fmt(t)} / ${fmt(d)}`;
    highlight();
  }
  function loop() { tick(); if (!audio.paused) requestAnimationFrame(loop); }
  audio.addEventListener("play", () => requestAnimationFrame(loop));
  ["pause", "seeked", "timeupdate", "loadedmetadata", "durationchange"].forEach((e) => audio.addEventListener(e, tick));

  $("player-toggle").addEventListener("click", () => {
    if (!L.loaded) return;
    if (L.mode === "ask") return back("button");
    playOrPause(L.loaded);
  });
  $("player-ask").addEventListener("click", () => {
    if (L.mode === "ask") return back("button");
    if (L.playing && !audio.paused && !L.mode) ask(L.run);
  });
  const seekBar = $("player-seek");
  seekBar.addEventListener("pointerdown", () => { dragging = true; });
  addEventListener("pointerup", () => { dragging = false; });
  seekBar.addEventListener("input", () => { try { audio.currentTime = +seekBar.value; } catch (e) {} tick(); });

  // --- the chapter view (#/chapter/<run>) ------------------------------------------------------------
  const view = { run: null, page: null, sentences: [], refs: [], cur: -1, refK: -2, scrolledAt: 0, autoAt: 0 };
  const episodes = {};

  async function showChapter(run) {
    if (view.run === run && view.sentences.length) return highlight();
    Object.assign(view, { run, sentences: [], refs: [], cur: -1, refK: -2 });
    const info = infoFor(run);
    $("ch-title").textContent = plain(info.title);
    $("ch-angle").textContent = info.overview || "";
    $("ch-meta").textContent = dateLabel(info);
    $("ch-text").replaceChildren();
    $("ch-cardlist").replaceChildren();
    show($("ch-problem"), false);
    paint();
    let ep = episodes[run];
    if (!ep) {
      try {
        const res = await fetch(`/runs/${encodeURIComponent(run)}/11_episode/episode.json`, { cache: "no-store" });
        if (!res.ok) throw new Error(res.status);
        ep = episodes[run] = await res.json();
      } catch (e) {
        if (view.run !== run) return;
        $("ch-problem").textContent = (rowFor(run) || {}).playable ? "This chapter has no read-along yet. Play still plays it."
          : "This chapter has no read-along yet.";
        show($("ch-problem"), true);
        return;
      }
    }
    if (view.run !== run) return;
    const row = rowFor(run);  // the title the library knows it by (its card's), else the episode's
    $("ch-title").textContent = plain((row && row.title) || ep.title || info.title);
    show($("ch-play"), !!(row && row.playable));  // only the latest chapter plays (docs/library-timeline.md)
    if (ep.angle && plain(ep.angle) !== plain(ep.title)) $("ch-angle").textContent = plain(ep.angle);
    renderText(ep);
    renderRefs(ep.references || []);
    view.cur = -1; view.refK = -2;
    highlight();
  }

  function renderText(ep) {
    const out = [];
    (ep.segments || []).forEach((s, si) => {
      const sec = el("section");
      sec.appendChild(el("h3", null, `Part ${si + 1}`));
      const p = el("p");
      (s.sentences || []).forEach((x) => {
        const text = plain(x.text || (x.words || []).map((w) => w.w).join(" "));
        const se = el("span", "lib-s", text);
        se.dataset.i = view.sentences.length;
        se.dataset.id = x.id;
        view.sentences.push({ start: x.start, el: se });
        p.append(se, " ");
      });
      sec.appendChild(p);
      out.push(sec);
    });
    $("ch-text").replaceChildren(...out);
  }

  function renderRefs(refs) {
    view.refs = refs.map((r) => {
      const c = el("div", `lib-ref ${r.kind || ""}`);
      c.appendChild(el("div", "kind", `${{ source: "Source", work: "Work", person: "Person", quote: "Quote" }[r.kind] || "Reference"} · ${fmt(r.start)}`));
      const title = el("div", "title");
      const label = r.kind === "quote" ? `“${r.title}”` : r.title;
      if (r.url) { const a = el("a", null, label); a.href = r.url; a.target = "_blank"; a.rel = "noopener"; title.appendChild(a); }
      else title.textContent = label;
      c.appendChild(title);
      if (r.creator) c.appendChild(el("div", "note", r.creator));
      if (r.note) c.appendChild(el("div", "note", r.note));
      return { start: r.start || 0, el: c };
    });
    $("ch-cardlist").replaceChildren(...view.refs.map((r) => r.el));
    show($("ch-cards"), view.refs.length > 0);
  }

  // The sentence being heard, when the player has this chapter; the page follows it unless the listener
  // scrolled in the last few seconds.
  function highlight() {
    if (!view.run || !view.sentences.length) return;
    const here = L.loaded && L.loaded.run === view.run && audio.src;
    const t = here ? audio.currentTime : -1;
    const i = here ? lastStarted(view.sentences, t) : -1;
    if (i !== view.cur) {
      if (view.cur >= 0 && view.sentences[view.cur]) view.sentences[view.cur].el.classList.remove("current");
      view.cur = i;
      if (i >= 0) {
        const se = view.sentences[i].el;
        se.classList.add("current");
        host.dataset.sentence = se.dataset.id;  // for tests
        if (!audio.paused && now() - view.scrolledAt > 6000) { view.autoAt = now(); se.scrollIntoView({ block: "center", behavior: "smooth" }); }
      } else host.dataset.sentence = "";
    }
    const k = here ? lastStarted(view.refs, t) : -1;
    if (k !== view.refK) {
      view.refK = k;
      view.refs.forEach((r, j) => r.el.classList.toggle("latest", j === k));
    }
  }
  ["wheel", "touchmove"].forEach((e) => addEventListener(e, () => { view.scrolledAt = now(); }, { passive: true }));
  addEventListener("keydown", (e) => { if (/^(Arrow|Page|Home|End|Space)/.test(e.code)) view.scrolledAt = now(); });

  $("ch-text").addEventListener("click", (e) => {
    const se = e.target.closest && e.target.closest(".lib-s");
    if (!se || !view.run) return;
    if (!(rowFor(view.run) || {}).playable) return;  // an older chapter reads, but doesn't play
    seekTo(infoFor(view.run), view.sentences[+se.dataset.i].start);
  });
  $("ch-play").addEventListener("click", () => { if (view.run && (rowFor(view.run) || {}).playable) playOrPause(infoFor(view.run)); });

  // --- the router: location.hash; the player's audio is never touched (the orb's #audio playing this chapter is
  // handed to it, since the chapter view hides the orb) ---------------------------------------------------------
  let homeScroll = 0, inChapter = false, shownHash = "";
  function route() {
    const m = /^#\/chapter\/([^/]+)(\/transcript)?$/.exec(location.hash);
    const run = m ? decodeURIComponent(m[1]) : null;
    const reading = !!(m && m[2]);
    if (run && !inChapter) homeScroll = scrollY;
    document.documentElement.classList.toggle("lib-in-chapter", !!run);
    host.classList.toggle("lib-wide", reading);
    show($("lib-home"), !run);
    show($("lib-page"), !!run && !reading);
    show($("lib-chapter"), reading);
    if (run) takeOver(run);  // the orb hides: its chapter moves to the player
    if (run && reading) { view.page = null; $("lib-back").href = `#/chapter/${encodeURIComponent(run)}`; showChapter(run); }
    else if (run) { view.run = null; showPage(run); }
    else { view.run = null; view.page = null; }
    if (run && location.hash !== shownHash) scrollTo(0, 0);
    else if (!run && inChapter) requestAnimationFrame(() => {  // back home: the strip and the scrubber were hidden
      scrollTo(0, homeScroll);
      if (T.tl) buildScrubber(T.tl);  // its labels are spaced by its width
      if (T.items.length) $("strip").scrollTo({ left: offsetOf(T.i) });
    });
    shownHash = location.hash;
    inChapter = !!run;
    paint();
  }
  addEventListener("hashchange", route);

  // --- notes for the next chapter (typed) -----------------------------------------------------------
  function noteMessage(text) { $("note-msg").textContent = text || ""; show($("note-msg"), !!text); }
  $("note-open").addEventListener("click", () => {
    const open = $("note-box").hidden;
    show($("note-box"), open);
    if (open) { noteMessage(""); $("note-text").focus(); }
  });
  $("note-cancel").addEventListener("click", () => show($("note-box"), false));
  async function sendNote() {
    const text = $("note-text").value.trim();
    if (!text) return $("note-text").focus();
    $("note-send").disabled = true;
    try {
      const out = await post("/api/overnight/note", { text });
      $("note-text").value = "";
      show($("note-box"), false);
      noteMessage(out.message || "Noted for the next chapter.");
      load();
    } catch (e) {
      noteMessage(e.message ? `Couldn't add the note: ${e.message}` : "Couldn't add the note.");
    } finally {
      $("note-send").disabled = false;
    }
  }
  $("note-send").addEventListener("click", sendNote);
  $("note-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendNote(); } });

  // --- settings, Play, Change ---------------------------------------------------------------------
  $("settings-link").addEventListener("click", () => {
    host.classList.toggle("lib-settings-open");
    if (state) fillSettings(state.settings);
  });
  $("s-save").addEventListener("click", async () => {
    const days = [...$("s-days").querySelectorAll("input:checked")].map((x) => x.value);
    const body = { overnight: { enabled: $("s-enabled").checked, days, start: $("s-start").value, plan_at: $("s-plan").value } };
    const res = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const out = await res.json().catch(() => ({}));
    $("s-note").textContent = res.ok ? "Saved." : (out.detail || "Couldn't save that.");
    if (res.ok) { host.classList.remove("lib-settings-open"); load(); }
  });
  $("change").addEventListener("click", () => {
    if (!state) return;
    return L.mode === "change" ? closeChange(L.changed || null) : openChange(state.up_next);
  });

  async function load() {
    try {
      const res = await fetch("/api/library");
      if (res.ok) render(await res.json());
    } catch (e) { /* the server may be restarting; the next poll tries again */ }
  }

  window.Library = { state: () => state, reload: load, busy: () => !!(L.session || L.connecting), end: endLive };
  window.Morning = { play };
  window.Change = { open: (up) => (L.mode === "change" ? closeChange(L.changed || null) : openChange(up)) };
  route();
  load().then(() => { if (view.run) $("ch-meta").textContent = dateLabel(infoFor(view.run)); });
  setInterval(load, 15000);
})();
