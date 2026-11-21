"""page/speaker.js in headless Chromium: the Android "Speakerphone" retry and the audio_route records (Ian's Android
phone, 2026-11-21: the voice came from the ear speaker, very quiet).

The page is speaker.js as stages/common.with_speaker serves it, on a routed origin (nothing is listened on).
navigator.mediaDevices (enumerateDevices with Chrome's Android labels, getUserMedia recording each call), the user
agent and navigator.userAgentData are stubbed; POST /api/demo/event is caught by the route and recorded.
"""

import json

import pytest

from stages import common

sync_api = pytest.importorskip("playwright.sync_api")

ORIGIN = "http://localhost:9"  # a secure context (mediaDevices), answered by the route
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 "
              "Mobile Safari/537.36")
MAC_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/142.0.0.0 Safari/537.36")
PHONE = [{"kind": "audioinput", "label": "Speakerphone", "deviceId": "sp-id"},
         {"kind": "audioinput", "label": "Headset earpiece", "deviceId": "ear-id"},
         {"kind": "audiooutput", "label": "Speakerphone", "deviceId": "out-id"}]

# `window.__order`: when each capture opened and each track stopped; `window.__gum`: each getUserMedia's constraints.
STUB = r"""
(({ ua, android, devices }) => {
  if (ua) Object.defineProperty(navigator, "userAgent", { configurable: true, get: () => ua });
  if (android) Object.defineProperty(navigator, "userAgentData", { configurable: true, value: {
    platform: "Android", mobile: true, brands: [{ brand: "Google Chrome", version: "142" }],
    getHighEntropyValues: async (keys) => ({ platform: "Android", model: "Pixel 8", platformVersion: "16.0.0",
      fullVersionList: [{ brand: "Not_A Brand", version: "99.0.0.0" }, { brand: "Google Chrome", version: "142.0.7444.52" }] }) } });
  window.__devices = devices;
  window.__gum = [];
  window.__order = [];
  window.__failSecond = false;
  window.__hangSecond = false;
  window.__failNext = false;   // the next capture (any) fails
  window.__toHeadset = false;  // a capture with no device asked for is the listed headset's (as Chrome would pick)
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  const byId = (id) => (window.__devices.find((d) => d.deviceId === id) || {}).label;
  function track(n, label, id) {
    const t = dest.stream.getAudioTracks()[0].clone();
    Object.defineProperty(t, "label", { value: label });
    t.getSettings = () => ({ deviceId: id, echoCancellation: true, noiseSuppression: true, autoGainControl: true, sampleRate: 48000 });
    t.getCapabilities = () => ({ echoCancellation: [true, false] });
    const stop = t.stop.bind(t);
    t.stop = () => { window.__order.push("stop" + n); stop(); };
    return t;
  }
  navigator.mediaDevices.enumerateDevices = async () => window.__devices.map((d) => ({ ...d, groupId: "g", toJSON() { return d; } }));
  navigator.mediaDevices.getUserMedia = async (c) => {
    window.__gum.push(JSON.parse(JSON.stringify(c)));
    const n = window.__gum.length;
    const want = c.audio && c.audio.deviceId && c.audio.deviceId.exact;
    if (window.__failNext) { window.__failNext = false; throw new DOMException("could not start", "NotReadableError"); }
    if (want && window.__failSecond) throw new DOMException("could not start", "NotReadableError");
    if (want && window.__hangSecond) await new Promise((r) => setTimeout(r, 1500));
    const id = want || "default";
    const hs = window.__toHeadset && window.__devices.find((d) => d.kind === "audioinput" && /^(Wired|Bluetooth) headset|^USB audio/.test(d.label));
    const s = new MediaStream([track(n, want ? byId(want) : hs ? hs.label : "Headset earpiece", id)]);
    window.__order.push("open" + n);
    return s;
  };
})
"""


class Page:
    def __init__(self, *, ua=ANDROID_UA, android=True, devices=PHONE, cfg=None):
        cfg = cfg if cfg is not None else {"demo": {"enabled": True}, "speaker": {"report_gap_ms": 1500}}
        js = common.with_speaker("window.__served = true;", cfg)
        self.posts = []
        self.pw = sync_api.sync_playwright().start()
        self.browser = self.pw.chromium.launch()
        self.page = self.browser.new_context(user_agent=ua).new_page()
        self.page.add_init_script(STUB + f"({json.dumps({'ua': ua, 'android': android, 'devices': devices})});")

        def route(r):
            if r.request.method == "POST" and r.request.url.endswith("/api/demo/event"):
                self.posts.append(json.loads(r.request.post_data or "{}"))
                return r.fulfill(status=200, content_type="application/json", body='{"ok": true}')
            if r.request.url.endswith("/speaker-test.js"):
                return r.fulfill(status=200, content_type="text/javascript", body=js)
            return r.fulfill(status=200, content_type="text/html",
                             body='<!doctype html><body><script src="/speaker-test.js"></script></body>')
        self.page.route(ORIGIN + "/**", route)
        self.page.goto(ORIGIN + "/")
        self.page.wait_for_function("window.__served && window.Speaker")

    def js(self, code, arg=None):
        return self.page.evaluate(code, arg)

    def mic(self):
        """Speaker.mic() as a page's tap calls it; what it resolved with."""
        return self.js("""async () => { const s = await Speaker.mic(); window.__got = s;
            return { label: s.getAudioTracks()[0].label, live: s.getAudioTracks()[0].readyState }; }""")

    def audio(self, reason=None, n=None, timeout=3000):
        """The audio_route records posted (waiting for `n` of them)."""
        if n is not None:
            deadline = timeout
            while sum(1 for b in self.posts if reason is None or b["data"]["reason"] == reason) < n and deadline > 0:
                self.page.wait_for_timeout(50)
                deadline -= 50
        return [b for b in self.posts if b.get("event") == "audio_route" and (reason is None or b["data"]["reason"] == reason)]

    def close(self):
        self.browser.close()
        self.pw.stop()


@pytest.fixture
def make():
    pages = []

    def go(**kw):
        p = Page(**kw)
        pages.append(p)
        return p
    yield go
    for p in pages:
        p.close()


def test_android_reopens_the_mic_on_the_speakerphone_input_before_letting_the_first_go(make):
    p = make()
    got = p.mic()
    gum = p.js("window.__gum")
    assert len(gum) == 2
    assert gum[0]["audio"] == {"echoCancellation": True, "noiseSuppression": True, "autoGainControl": True}
    assert gum[1]["audio"] == {"echoCancellation": True, "noiseSuppression": True, "autoGainControl": True,
                               "deviceId": {"exact": "sp-id"}}
    order = p.js("window.__order")
    assert order.index("open2") < order.index("stop1")   # the first stopped only once the second was open
    assert "stop2" not in order
    assert got["label"] == "Speakerphone" and got["live"] == "live"   # the stream resolved is the second
    assert p.js("window.__got.getAudioTracks()[0].getSettings().deviceId") == "sp-id"
    assert p.js("Speaker.live()")
    rec = p.audio("speakerphone", 1)
    assert [r["data"]["result"] for r in rec] == ["switched"]
    assert rec[0]["data"]["track_label"] == "Speakerphone"
    # the mic record: every field, the model from the client hints
    mic = p.audio("mic", 1)[0]["data"]
    assert mic["ua"] == ANDROID_UA
    assert mic["ua_hints"] == {"model": "Pixel 8", "platform": "Android", "android": "16.0.0", "chrome": "142.0.7444.52"}
    assert mic["inputs"] == ["Speakerphone", "Headset earpiece"] and mic["outputs"] == ["Speakerphone"]
    assert mic["track_label"] == "Headset earpiece"
    assert mic["settings"] == {"deviceId": "default", "echoCancellation": True, "noiseSuppression": True,
                               "autoGainControl": True, "sampleRate": 48000}
    assert mic["ec_modes"] == [True, False] and mic["live_mic_tracks"] == 1
    # stopping the stream it resolved with frees the mic
    p.js("window.__got.getTracks().forEach((t) => t.stop()); Speaker.free(); true")
    assert not p.js("Speaker.live()")


@pytest.mark.parametrize("label", ["Bluetooth headset", "Wired headset", "USB audio device"])
def test_with_a_headset_listed_there_is_no_second_capture(make, label):
    p = make(devices=PHONE + [{"kind": "audioinput", "label": label, "deviceId": "hs-id"}])
    assert p.mic()["label"] == "Headset earpiece"
    assert len(p.js("window.__gum")) == 1 and "stop1" not in p.js("window.__order")
    assert [r["data"]["result"] for r in p.audio("speakerphone", 1)] == ["headset present"]


def test_no_speakerphone_label_no_second_capture(make):
    p = make(devices=[{"kind": "audioinput", "label": "", "deviceId": "x"}])
    p.mic()
    assert len(p.js("window.__gum")) == 1
    assert [r["data"]["result"] for r in p.audio("speakerphone", 1)] == ["no label"]


def test_a_desktop_never_opens_a_second_capture(make):
    p = make(ua=MAC_UA, android=False, devices=PHONE)  # even with a "Speakerphone" input listed
    assert p.mic()["label"] == "Headset earpiece"
    p.page.wait_for_timeout(300)
    assert len(p.js("window.__gum")) == 1
    assert p.audio("speakerphone") == []
    mic = p.audio("mic", 1)[0]["data"]
    assert mic["ua"] == MAC_UA and not (mic["ua_hints"] or {}).get("android")


def test_a_failing_second_capture_keeps_the_first(make):
    p = make()
    p.js("window.__failSecond = true")
    got = p.mic()
    assert got["label"] == "Headset earpiece" and got["live"] == "live"
    assert len(p.js("window.__gum")) == 2 and "stop1" not in p.js("window.__order")
    assert p.js("Speaker.live()")
    assert [r["data"]["result"] for r in p.audio("speakerphone", 1)] == ["failed"]


def test_a_second_capture_that_never_opens_in_time_keeps_the_first_and_is_stopped_when_it_does(make):
    p = make(cfg={"demo": {"enabled": True}, "speaker": {"switch_timeout_ms": 300}})
    p.js("window.__hangSecond = true")
    got = p.mic()
    assert got["label"] == "Headset earpiece" and got["live"] == "live"
    p.page.wait_for_function("window.__order.includes('stop2')", timeout=4000)   # opened late: let go of at once
    assert "stop1" not in p.js("window.__order") and p.js("Speaker.live()")
    assert [r["data"]["result"] for r in p.audio("speakerphone", 1)] == ["failed"]


def test_the_retry_can_be_turned_off(make):
    p = make(cfg={"demo": {"enabled": True}, "speaker": {"android_speakerphone": False}})
    assert p.mic()["label"] == "Headset earpiece"
    p.page.wait_for_timeout(300)
    assert len(p.js("window.__gum")) == 1 and p.audio("speakerphone") == []


def test_records_are_rate_limited_per_reason_and_cover_device_changes_and_the_voice_playing(make):
    p = make()
    p.mic()
    p.audio("mic", 1)
    p.js("Speaker.mic().then(() => true)")   # a second mic open inside the gap: not recorded again
    assert len(p.js("window.__gum")) == 4   # the second mic() ran before the headset was listed: it switched too
    # a headset paired later: recorded, and each live Speakerphone track moved off it (one capture each, below)
    p.js("window.__devices.push({ kind: 'audioinput', label: 'Bluetooth headset', deviceId: 'bt' }); true")
    p.js("navigator.mediaDevices.dispatchEvent(new Event('devicechange')); "
         "navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    p.js("const s = new MediaStream(); Speaker.playing(s); Speaker.playing(s); Speaker.playing(new MediaStream()); true")
    p.page.wait_for_timeout(500)
    assert len(p.audio("mic")) == 1
    assert len(p.audio("devicechange")) == 1 and "Bluetooth headset" in p.audio("devicechange")[0]["data"]["inputs"]
    assert len(p.audio("playing")) == 1
    gum_before = len(p.js("window.__gum"))
    assert gum_before == 6 and all("deviceId" not in c["audio"] for c in p.js("window.__gum")[4:])
    p.page.wait_for_timeout(1300)   # past report_gap_ms: recorded again
    p.js("navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    assert len(p.audio("devicechange", 2)) == 2
    assert len(p.js("window.__gum")) == gum_before   # nothing left on Speakerphone: a device change opens nothing
    assert all(set(b) == {"event", "data"} for b in p.posts)


def test_nothing_is_posted_off_tester_mode(make):
    p = make(cfg={})   # Ian's own app: the route isn't there
    p.mic()
    p.page.wait_for_timeout(400)
    assert p.posts == [] and len(p.js("window.__gum")) == 2   # the retry still runs


# --- a headset connected after the switch (Chrome keeps an asked-for device while it is there: the loudspeaker always
# is, so a headset would never be chosen over it) ------------------------------------------------------------------

def test_a_headset_connected_after_the_switch_takes_the_mic_back_in_the_same_stream(make):
    p = make()
    p.mic()
    p.js("window.__toHeadset = true")
    p.js("""window.__followed = []; Speaker.follow((from, to, stream) => window.__followed.push({
        same: stream === window.__got, from: from.label, to: to.label, fromLive: from.readyState,
        inStream: stream.getAudioTracks().length === 1 && stream.getAudioTracks()[0] === to })); true""")
    p.js("window.__got.getAudioTracks()[0].enabled = false; true")   # muted, as a page keeps it between turns
    p.js("navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")   # no headset yet: nothing
    p.page.wait_for_timeout(300)
    assert len(p.js("window.__gum")) == 2 and p.js("window.__followed") == []
    p.js("window.__devices.push({ kind: 'audioinput', label: 'Bluetooth headset', deviceId: 'bt' }); "
         "navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    p.page.wait_for_function("window.__followed.length === 1")
    gum = p.js("window.__gum")
    assert len(gum) == 3 and gum[2]["audio"] == {"echoCancellation": True, "noiseSuppression": True,
                                                 "autoGainControl": True}   # no device asked for: Chrome's choice
    # opened beside the Speakerphone track, put in its place in the same stream, followers told, then it stopped
    assert p.js("window.__followed") == [{"same": True, "from": "Speakerphone", "to": "Bluetooth headset",
                                          "fromLive": "live", "inStream": True}]
    order = p.js("window.__order")
    assert order.index("open3") < order.index("stop2") and "stop3" not in order
    t = p.js("(() => { const t = window.__got.getAudioTracks()[0]; return [t.label, t.readyState, t.enabled]; })()")
    assert t == ["Bluetooth headset", "live", False]   # enabled as the old one was
    assert p.js("Speaker.live()")
    rec = p.audio("headset", 1)
    assert [r["data"]["result"] for r in rec] == ["moved"] and rec[0]["data"]["track_label"] == "Bluetooth headset"
    # once moved, nothing is left on Speakerphone: a later change opens nothing
    p.page.wait_for_timeout(1600)
    p.js("navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    p.page.wait_for_timeout(300)
    assert len(p.js("window.__gum")) == 3 and len(p.js("window.__followed")) == 1
    p.js("window.__got.getTracks().forEach((t) => t.stop()); Speaker.free(); true")
    assert not p.js("Speaker.live()")


def test_a_failed_move_keeps_the_speakerphone_track_and_a_later_change_tries_again(make):
    p = make()
    p.mic()
    p.js("window.__toHeadset = true")
    p.js("window.__failNext = true; window.__devices.push({ kind: 'audioinput', label: 'Wired headset', deviceId: 'w' }); "
         "navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    assert [r["data"]["result"] for r in p.audio("headset", 1)] == ["failed"]
    assert p.js("window.__got.getAudioTracks()[0].label") == "Speakerphone" and "stop2" not in p.js("window.__order")
    p.page.wait_for_timeout(1600)
    p.js("navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    p.page.wait_for_function("window.__got.getAudioTracks()[0].label === 'Wired headset'")
    assert [r["data"]["result"] for r in p.audio("headset", 2)] == ["failed", "moved"]


def test_a_stopped_speakerphone_track_is_left_alone(make):
    p = make()
    p.mic()
    p.js("window.__got.getTracks().forEach((t) => t.stop()); Speaker.free(); "
         "window.__devices.push({ kind: 'audioinput', label: 'Bluetooth headset', deviceId: 'bt' }); "
         "navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    p.page.wait_for_timeout(400)
    assert len(p.js("window.__gum")) == 2 and p.audio("headset") == []


def test_with_no_switch_at_the_tap_a_later_headset_moves_nothing(make):
    q = make(devices=PHONE + [{"kind": "audioinput", "label": "Bluetooth headset", "deviceId": "bt"}])
    q.mic()   # a headset at the tap: no switch, so nothing to move
    q.js("window.__devices.push({ kind: 'audioinput', label: 'Wired headset', deviceId: 'w' }); "
         "navigator.mediaDevices.dispatchEvent(new Event('devicechange')); true")
    q.page.wait_for_timeout(400)
    assert len(q.js("window.__gum")) == 1 and q.audio("headset") == []
