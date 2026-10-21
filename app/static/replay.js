/* The portal's own Replay of past traffic: plays the hub's heatmap files (half hours of positions every few seconds, /api/history/chunk/DAY/NN.bin) back as the same aircraft list the live map uses.
   The file layout is described in app/histchunks.py. Aircraft are shown with the last position they sent; one that has been quiet for a minute is dropped, like the live map does.
   var R = ArkReplay.create({onFrame: fn({now, aircraft, totals}), onState: fn(state)})   state = {playing, speed, loading, error, ts (ms), missing}
   R.open(Date)  start at a moment (UTC instant);  R.play() / R.pause() / R.toggle();  R.speed(n);  R.seek(Date);  R.step(seconds, may be negative);  R.close();  R.active() */
(function () {
  "use strict";
  var MAGIC = 0x0E7F7C9D, SRC = ["adsb_icao", "adsb_icao_nt", "adsr_icao", "tisb_icao", "adsc", "mlat", "other", "mode_s", "adsb_other", "adsr_other", "tisb_trackfile", "tisb_other", "mode_ac"], EMG = {"7500": 1, "7600": 1, "7700": 1};

  function decode(buf) {
    var n = buf.byteLength >> 4, i32 = new Int32Array(buf, 0, n * 4), u8 = new Uint8Array(buf), slices = [], cur = null, k, hex, call, j;
    for (k = 0; k < n; k++) {
      var o = k * 4, w0 = i32[o], w1 = i32[o + 1], w2 = i32[o + 2], w3 = i32[o + 3];
      if (w0 === MAGIC) { cur = {t: (w2 >>> 0) / 1000 + (w1 >>> 0) * 4294967.296, ival: (w3 & 65535) / 1000, pts: [], info: {}}; slices.push(cur); continue; }
      if (!cur) continue;
      hex = ((w0 & 0xFFFFFF) >>> 0).toString(16).padStart(6, "0"); if (w0 & 0x1000000) hex = "~" + hex;
      if (w1 >= 1073741824) {
        call = ""; if (u8[(o + 2) * 4] !== 0) { for (j = 0; j < 8; j++) call += String.fromCharCode(u8[(o + 2) * 4 + j]); }
        cur.info[hex] = {call: call.replace(/[\0 ]+$/, ""), squawk: String(w1 & 0xFFFF).padStart(4, "0")}; continue;
      }
      var alt = w3 & 65535; if (alt & 32768) alt |= -65536;
      var gs = w3 >> 16, src = (w0 >>> 27) & 0x1F;
      cur.pts.push({hex: hex, lat: w1 / 1e6, lon: w2 / 1e6, alt: alt === -123 ? "ground" : alt === -124 ? null : alt * 25, gs: gs === -1 ? null : gs / 10, src: SRC[src] || "unknown"});
    }
    return slices;
  }
  function bearing(la1, lo1, la2, lo2) {
    var r = Math.PI / 180, y = Math.sin((lo2 - lo1) * r) * Math.cos(la2 * r), x = Math.cos(la1 * r) * Math.sin(la2 * r) - Math.sin(la1 * r) * Math.cos(la2 * r) * Math.cos((lo2 - lo1) * r);
    return (Math.atan2(y, x) / r + 360) % 360;
  }
  function away(la1, lo1, la2, lo2) { var dy = (la2 - la1) * 111320, dx = (lo2 - lo1) * 111320 * Math.cos(la1 * Math.PI / 180); return Math.sqrt(dx * dx + dy * dy); }
  function dayOf(ms) { return new Date(ms).toISOString().slice(0, 10); }
  function indexOf(ms) { var d = new Date(ms); return 2 * d.getUTCHours() + (d.getUTCMinutes() >= 30 ? 1 : 0); }

  function create(o) {
    var chunks = {}, order = [], loading = null, ts = 0, cur = null, pos = 0, ac = {}, timer = null, abort = null, on = false, S = {playing: false, speed: 10, loading: false, error: "", ts: 0, missing: false};
    function say() { S.ts = ts; if (o.onState) o.onState(S); }
    async function load(ms) {
      var day = dayOf(ms), idx = indexOf(ms), key = day + "/" + idx;
      if (chunks[key]) return chunks[key];
      if (abort) abort.abort(); abort = window.AbortController ? new AbortController() : null;
      var r = await fetch("/api/history/chunk/" + day + "/" + idx + ".bin", {signal: abort ? abort.signal : undefined});
      if (!r.ok) return null;
      var sl = decode(await r.arrayBuffer());
      if (!sl.length) return null;
      var c = {key: key, day: day, idx: idx, start: Date.UTC(+day.slice(0, 4), +day.slice(5, 7) - 1, +day.slice(8, 10), idx >> 1, (idx & 1) * 30), slices: sl};
      chunks[key] = c; order.push(key); while (order.length > 6) delete chunks[order.shift()];
      return c;
    }
    function apply(sl) {
      var seen = {}, t = sl.t, keep = Math.max(60, sl.ival * 4);
      sl.pts.forEach(function (p) {
        var a = ac[p.hex] || (ac[p.hex] = {hex: p.hex, flight: null, squawk: null, track: null, lat: null, lon: null}), inf = sl.info[p.hex];
        if (inf) { if (inf.call) a.flight = inf.call; a.squawk = inf.squawk; }
        if (a.lat != null && away(a.lat, a.lon, p.lat, p.lon) > 30) a.track = Math.round(bearing(a.lat, a.lon, p.lat, p.lon));
        a.lat = p.lat; a.lon = p.lon; a.alt = p.alt === "ground" ? 0 : p.alt; a.gnd = p.alt === "ground"; a.gs = p.gs; a.src = p.src; a.heard = t; seen[p.hex] = 1;
      });
      Object.keys(ac).forEach(function (h) { if (t - ac[h].heard > keep) delete ac[h]; });
      Object.keys(sl.info).forEach(function (h) { if (ac[h] && !seen[h]) { var inf = sl.info[h]; if (inf.call) ac[h].flight = inf.call; ac[h].squawk = inf.squawk; } });
    }
    function frame(sl) {
      var list = Object.keys(ac).map(function (h) {
        var a = ac[h], emg = !!EMG[a.squawk];
        return {hex: a.hex, flight: a.flight, r: null, t: null, alt: a.alt, gnd: a.gnd, gs: a.gs, track: a.track, lat: a.lat, lon: a.lon, cat: null, squawk: a.squawk, vr: null, mil: false, db: 0, emg: emg, rssi: null, seen_pos: 0, seen: 0, src: a.src};
      });
      return {now: sl.t, aircraft: list, totals: {aircraft: list.length, with_position: list.length, messages_per_second: 0, military: 0, highest: null}, replay: true};
    }
    function show(sl) { ts = Math.round(sl.t * 1000); if (o.onFrame) o.onFrame(frame(sl)); say(); }
    // jump: the state at a moment is built by playing the half hour's slices up to it (without showing them)
    async function seek(date) {
      if (!on) return; clearTimeout(timer);
      var ms = date instanceof Date ? date.getTime() : date;
      S.loading = true; S.error = ""; S.missing = false; ts = ms; say();
      var c;
      try { c = await load(ms); } catch (e) { if (e && e.name === "AbortError") return; c = null; }
      if (!on) return;
      S.loading = false;
      if (!c) { S.missing = true; S.error = "The hub has no history for this moment."; ac = {}; if (o.onFrame) o.onFrame({now: ms / 1000, aircraft: [], totals: {aircraft: 0, with_position: 0, messages_per_second: 0, military: 0, highest: null}, replay: true}); say(); return; }
      cur = c; ac = {}; var i = 0, t = ms / 1000;
      while (i < c.slices.length - 1 && c.slices[i + 1].t <= t) { apply(c.slices[i]); i++; }
      pos = i; apply(c.slices[i]); show(c.slices[i]); pos = i + 1;
      if (S.playing) schedule();
    }
    function schedule() { clearTimeout(timer); var iv = cur && cur.slices[Math.max(0, pos - 1)] ? cur.slices[Math.max(0, pos - 1)].ival || 15 : 15; timer = setTimeout(tick, Math.max(40, iv / S.speed * 1000)); }
    async function tick() {
      if (!on || !S.playing || !cur) return;
      if (pos >= cur.slices.length) {   // the next half hour
        var next = cur.start + 30 * 60 * 1000;
        if (next > Date.now() - 20000) { S.playing = false; say(); return; }   // caught up with now
        S.loading = true; say();
        var c = null; try { c = await load(next); } catch (e) { if (e && e.name === "AbortError") return; }
        S.loading = false;
        if (!c) { S.playing = false; S.error = "No history for the next half hour."; say(); return; }
        cur = c; pos = 0;
      }
      apply(cur.slices[pos]); show(cur.slices[pos]); pos++;
      schedule();
    }
    return {
      open: function (date) { on = true; S.error = ""; return seek(date); },
      seek: function (date) { return seek(date); },
      play: function () { if (!on) return; S.playing = true; say(); schedule(); },
      pause: function () { S.playing = false; clearTimeout(timer); say(); },
      toggle: function () { if (S.playing) this.pause(); else this.play(); },
      speed: function (n) { S.speed = n; if (S.playing) schedule(); say(); },
      step: function (sec) { return seek(ts + sec * 1000); },
      close: function () { on = false; S.playing = false; clearTimeout(timer); if (abort) abort.abort(); ac = {}; cur = null; say(); },
      active: function () { return on; }, time: function () { return ts; }, state: function () { return S; }, decode: decode
    };
  }
  window.ArkReplay = {create: create, decode: decode};
})();
