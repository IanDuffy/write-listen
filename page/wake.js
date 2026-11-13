// The page's local listener: what brings the guide back after it has stepped back.
//
// While the guide rests there is no voice connection and no model running: only this, on the
// listener's own machine. It hears the microphone and says "wake" when a sound in the voice band
// stays well above the room's own noise for a moment: someone speaking, or any sustained loud sound.
// A click, a cough or a door is too short; a steady hum becomes the room. Nothing it hears is kept
// or sent anywhere.
//
// One file, three uses: an AudioWorklet module (the page loads it with audioWorklet.addModule, so
// it keeps running when the tab is in the background), a plain script (the page's Activity, for
// "has anyone spoken lately" during a conversation), and a Node module (tests/test_wake.py).
(function (root) {
  "use strict";

  const DEFAULTS = {
    ratio: 4,          // a frame is "loud" at this many times the room's noise floor
    min_rms: 0.006,    // and at least this loud (0-1; about -44 dBFS), however quiet the room
    need_ms: 240,      // this much loud sound
    window_ms: 600,    // within this long wakes the guide
    frame_ms: 20,
    warmup_ms: 500,    // a microphone opening clicks, and the floor needs a moment
    floor_rise: 1.5,   // the floor may rise this many times a second (a new steady noise becomes the room in a few seconds)
    low_hz: 150, high_hz: 3800,  // the voice band
  };

  const SILENT = 1e-6;  // below this a frame is digital silence, not a quiet room

  // A second-order filter section (RBJ cookbook), one sample at a time.
  function biquad(kind, hz, rate) {
    const w = 2 * Math.PI * hz / rate, cos = Math.cos(w), alpha = Math.sin(w) / (2 * Math.SQRT1_2);
    const b1 = kind === "high" ? -(1 + cos) : 1 - cos, b0 = Math.abs(b1) / 2, a0 = 1 + alpha;
    const c = { b0: b0 / a0, b1: b1 / a0, b2: b0 / a0, a1: -2 * cos / a0, a2: (1 - alpha) / a0 };
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    return (x) => {
      const y = c.b0 * x + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2;
      x2 = x1; x1 = x; y2 = y1; y1 = y;
      return y;
    };
  }

  class WakeDetector {
    constructor(sampleRate, options) {
      this.o = Object.assign({}, DEFAULTS, options || {});
      this.rate = sampleRate;
      // Two high-pass sections: mains hum, traffic rumble and footsteps sit below the voice.
      const h1 = biquad("high", this.o.low_hz, sampleRate), h2 = biquad("high", this.o.low_hz, sampleRate);
      this.high = (x) => h2(h1(x));
      this.low = biquad("low", this.o.high_hz, sampleRate);
      this.frameLen = Math.round(sampleRate * this.o.frame_ms / 1000);
      this.sum = 0; this.count = 0;   // the frame being filled
      this.ms = 0;                    // sound heard so far
      this.floor = null;              // the room's noise level (RMS in the voice band)
      this.loud = [];                 // times (ms) of recent loud frames
      this.woke = false;
    }

    // Samples (a Float32Array, -1..1). True once, when the sound has earned a wake.
    push(samples) {
      let wake = false;
      for (let i = 0; i < samples.length; i++) {
        const y = this.low(this.high(samples[i]));
        this.sum += y * y;
        if (++this.count === this.frameLen) {
          if (this.frame(Math.sqrt(this.sum / this.count))) wake = true;
          this.sum = 0; this.count = 0;
        }
      }
      return wake;
    }

    // One frame's loudness in the voice band. Public so the decision can be tested without audio.
    frame(rms) {
      const o = this.o;
      this.ms += o.frame_ms;
      if (this.ms <= o.warmup_ms) {  // learn the room; never wake on the microphone opening
        // Not from its first 100 ms, nor from digital silence (a capture often starts with a few
        // frames of exact zeros): a floor of zero would make any steady room noise a wake.
        if (this.ms > 100 && rms >= SILENT) this.floor = this.floor === null ? rms : Math.min(this.floor, rms);
        return false;
      }
      if (this.floor === null) {
        if (rms < SILENT) return false;  // still nothing real to measure the room by
        this.floor = rms;
      }
      const isLoud = rms > Math.max(o.min_rms, this.floor * o.ratio);
      // The floor drops at once to a quieter frame and climbs slowly towards a louder one, so
      // speech (which stops) does not become the room, and a fan that starts does.
      if (rms < this.floor && rms >= SILENT) this.floor = rms;
      else this.floor = Math.min(rms, this.floor * Math.pow(o.floor_rise, o.frame_ms / 1000) + 1e-7);
      if (isLoud) this.loud.push(this.ms);
      while (this.loud.length && this.loud[0] <= this.ms - o.window_ms) this.loud.shift();
      if (!this.woke && this.loud.length * o.frame_ms >= o.need_ms) {
        this.woke = true;
        return true;
      }
      return false;
    }
  }

  // "Has anyone spoken lately?" during a conversation, from the voice client's input volume (0-1,
  // the mean of the voice band's byte frequency data: a decibel scale, so the room's level is far
  // from zero). The room's level is tracked the same way; a reading clearly above it is speech.
  class Activity {
    constructor(margin) { this.margin = margin || 0.08; this.floor = null; }
    heard(volume) {
      if (!(volume >= 0)) return false;
      if (this.floor === null || volume < this.floor) this.floor = volume;
      else this.floor += (volume - this.floor) * 0.01;
      return volume > this.floor + this.margin;
    }
  }

  if (typeof registerProcessor === "function") {  // loaded as an AudioWorklet module
    registerProcessor("wake", class extends AudioWorkletProcessor {
      constructor(init) {
        super();
        this.detector = new WakeDetector(sampleRate, init && init.processorOptions);
      }
      process(inputs) {
        const channel = inputs[0] && inputs[0][0];
        if (channel && this.detector.push(channel)) this.port.postMessage("wake");
        return true;
      }
    });
  }
  const api = { WakeDetector, Activity, DEFAULTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else if (root) root.Wake = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
