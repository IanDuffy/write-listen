// Pure timing functions for the episode page: no DOM, so they can be tested without a browser
// (tests/episode/test_page_timing.py runs them under Node). The Python twins are in episode/timing.py.
(function (root) {
  "use strict";

  // Every word in order: {seg, sent, word, start, end, sentenceId}.
  function flattenWords(episode) {
    const out = [];
    episode.segments.forEach((s, si) => s.sentences.forEach((x, xi) => x.words.forEach((w, wi) =>
      out.push({ seg: si, sent: xi, word: wi, start: w.start, end: w.end, sentenceId: x.id }))));
    return out;
  }

  function flattenSentences(episode) {
    const out = [];
    episode.segments.forEach((s) => s.sentences.forEach((x) => out.push({ id: x.id, start: x.start, end: x.end })));
    return out;
  }

  // Index of the last item whose start is at or before t, or -1 (binary search; items sorted by start).
  function lastStartedIndex(items, t) {
    let lo = 0, hi = items.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (items[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return found;
  }

  // The word being spoken at t: the last word that has started. In a gap between words the
  // highlight stays on the word just said; before the first word, -1.
  function currentWordIndex(words, t) { return lastStartedIndex(words, t); }

  function currentSentenceIndex(sentences, t) { return lastStartedIndex(sentences, t); }

  // Arrow keys: forward, the start of the next sentence (in the last one, stay put); back, the start
  // of this one (if more than `grace` seconds in) or the previous one, never before the first.
  function sentenceStep(sentences, t, dir, grace = 1.0) {
    if (!sentences.length) return 0;
    const i = currentSentenceIndex(sentences, t);
    if (dir > 0) return i + 1 < sentences.length ? sentences[i + 1].start : t;
    if (i < 0) return sentences[0].start;
    if (t - sentences[i].start > grace) return sentences[i].start;
    return sentences[Math.max(0, i - 1)].start;
  }

  // Reference cards spoken by time t (references sorted by start).
  function referencesUpTo(refs, t) { return refs.slice(0, lastStartedIndex(refs, t) + 1); }

  function formatTime(s) {
    s = Math.max(0, Math.floor(s || 0));
    const m = Math.floor(s / 60), r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  const api = { flattenWords, flattenSentences, currentWordIndex, currentSentenceIndex, sentenceStep,
                referencesUpTo, formatTime };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.EpisodeTiming = api;
})(typeof window !== "undefined" ? window : globalThis);
