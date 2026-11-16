// Following the guide's reading word by word (page/live.js): GPT-Live's output transcript, matched
// against the paragraph being read. Pure functions, so tests run them under Node (tests/live/).
//
// What the live runs taught (2026-11-13): transcript deltas can split a word ("list" + "ening",
// "Sud" + "denly"); a new word's delta starts with a space, so a delta that doesn't end in one leaves
// a partial word to join with the next. The voice may skip, add or change a word, and the
// transcript may run words together ("relistening" for "re-listening"), so a word is looked for a
// few words ahead, two of the text's words may make one heard word, and a longer word may be
// found further ahead (the reading has run on past a stretch the transcript garbled).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Align = factory();
}(typeof self !== "undefined" ? self : this, function () {
  function norm(text) {
    return String(text).toLowerCase()
      .replace(/[’‘]/g, "'")
      .replace(/([a-z0-9])-(?=[a-z0-9])/g, "$1")   // re-listening -> relistening, on both sides
      .replace(/[^a-z0-9' ]+/g, " ")
      .replace(/(^|\s)'+|'+(?=\s|$)/g, " ")         // quotes, not apostrophes
      .split(/\s+/).filter(Boolean);
  }

  // Deltas in, whole words out: the last word of a delta that doesn't end in a space waits for the next.
  function Tokens() { this.partial = ""; }
  Tokens.prototype.push = function (delta) {
    const text = this.partial + delta;
    if (/\s$/.test(text)) { this.partial = ""; return norm(text); }
    const cut = text.search(/\S+$/);
    this.partial = text.slice(cut);
    return norm(text.slice(0, cut));
  };
  Tokens.prototype.flush = function () {
    const out = norm(this.partial);
    this.partial = "";
    return out;
  };

  const NEAR = 4, FAR = 15, FAR_MIN_LEN = 4;

  // The index just after `w` in `words`, looking from `from`; -1 if it isn't there.
  function match(words, from, w) {
    const near = Math.min(words.length, from + NEAR + 1);
    for (let j = from; j < near; j++) if (words[j] === w) return j + 1;
    for (let j = from; j < near - 1; j++) if (words[j] + words[j + 1] === w) return j + 2;
    if (w.length >= FAR_MIN_LEN) {
      const far = Math.min(words.length, from + FAR + 1);
      for (let j = near; j < far; j++) if (words[j] === w) return j + 1;
    }
    return -1;
  }

  // Is `w` one of the first words of `words` (a paragraph beginning)?
  function opens(words, w, k = 6) { return words.slice(0, k).indexOf(w) !== -1; }

  return { norm, Tokens, match, opens, NEAR, FAR };
}));
