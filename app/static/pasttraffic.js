/* Past traffic on the aircraft map: the Replay bar and the Heat Map section, wired to static/replay.js and static/heatlayer.js. The page gives the hooks it owns.
   ArkPast.init({map, ingest: fn(frame), clear: fn(), altitude: fn -> {min, max} in feet, speed: 10, startAgoMinutes: 60, heatDuration: "24", heatMaxDays: 30, onMode: fn(replayOn)}) -> {replayOn, openReplay, closeReplay, heatOn, params(), apply(URLSearchParams)}
   Elements (live.html): #h-heat (tick), #h-heatopts, #h-dur, #h-end, #h-from, #h-to, #h-strength, #h-radius, #h-status, #h-replay (tick), and the bar #lv-replay with #rp-time, #rp-state, #rp-live, #rp-b5, #rp-b1, #rp-play, #rp-f1, #rp-f5, #rp-speed, #rp-date, #rp-clock, #rp-slide.
   The times typed and shown are in the display time zone chosen on the site (the same as every other page); the hub's files are in UTC. */
(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  function zone() { return window.fmtZone ? window.fmtZone() : undefined; }
  function parts(ms) {   // the wall clock in the display zone
    var o = {}; new Intl.DateTimeFormat("en-CA", {timeZone: zone(), hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"}).formatToParts(new Date(ms)).forEach(function (p) { o[p.type] = p.value; });
    return o;
  }
  function wall(ms) { var p = parts(ms); return {y: +p.year, mo: +p.month, d: +p.day, h: +p.hour % 24, mi: +p.minute, s: +p.second}; }
  function zoned(y, mo, d, h, mi) {   // the instant at which the wall clock of the display zone reads this
    var want = Date.UTC(y, mo - 1, d, h, mi), ms = want, k, w;
    for (k = 0; k < 3; k++) { w = wall(ms); ms -= Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi) - want; }
    return ms;
  }
  function p2(n) { return String(n).padStart(2, "0"); }
  function dateStr(w) { return w.y + "-" + p2(w.mo) + "-" + p2(w.d); }
  function clockStr(w) { return p2(w.h) + ":" + p2(w.mi); }
  function fromInputs(dv, tv) { var d = /^(\d{4})-(\d\d)-(\d\d)$/.exec(dv || ""), t = /^(\d\d):(\d\d)/.exec(tv || ""); return d && t ? zoned(+d[1], +d[2], +d[3], +t[1], +t[2]) : NaN; }
  function utcText(ms) { var d = new Date(ms); return p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()) + ":" + p2(d.getUTCSeconds()) + " UTC"; }

  function init(o) {
    var map = o.map, rp = null, replayOn = false, dragging = false, hrsMax = o.heatMaxDays || 30;
    var STRENGTH = {low: .65, mid: 1, high: 1.7}, RADIUS = {small: 1.6, mid: 2.2, large: 3.4};
    function pref(k, v) { try { if (v === undefined) return localStorage.getItem("past." + k); localStorage.setItem("past." + k, v); } catch (e) { return null; } return null; }

    // ---- heat map
    var heat = window.ArkHeat ? ArkHeat.create(map, {
      range: heatRange, altitude: function () { return o.altitude(); },
      onState: function (s) {
        var t = "";
        if (s.error) t = s.error; else if (s.loading) t = "Loading the history" + (s.asked ? " (" + s.found + " of " + s.asked + " half hours so far)" : "") + "...";
        else if (heatOn()) t = s.cells.toLocaleString() + " squares from " + s.found + " of " + s.asked + " half hours" + (s.truncated ? "; only the busiest squares are drawn, zoom in for the rest" : "") + ".";
        $("h-status").textContent = t; document.dispatchEvent(new CustomEvent("ark-heat-state"));
      }
    }) : null;
    function heatOn() { return $("h-heat").checked; }
    function customOn() { return $("h-dur").value === "custom"; }
    function heatRange() {
      if (customOn()) {
        var a = fromInputs(($("h-from").value || "").slice(0, 10), ($("h-from").value || "").slice(11, 16)), b = fromInputs(($("h-to").value || "").slice(0, 10), ($("h-to").value || "").slice(11, 16));
        if (!isNaN(a) && !isNaN(b) && b > a) { b = Math.min(b, Date.now()); a = Math.max(a, b - hrsMax * 86400000); return {from: new Date(a), to: new Date(b)}; }
      }
      var hrs = parseFloat($("h-dur").value) || 24, ago = parseFloat($("h-end").value) || 0, to = new Date(Date.now() - ago * 3600000);
      return {from: new Date(to.getTime() - hrs * 3600000), to: to};
    }
    function heatSettings() { if (heat) heat.style({radius: RADIUS[$("h-radius").value] || 2.2, strength: STRENGTH[$("h-strength").value] || 1}); }
    function heatChanged() {
      $("h-customrow").style.display = customOn() ? "" : "none"; $("h-end").disabled = customOn();
      pref("heat", heatOn() ? "1" : "0"); pref("dur", $("h-dur").value); pref("end", $("h-end").value); pref("strength", $("h-strength").value); pref("radius", $("h-radius").value);
      $("h-heatopts").style.display = heatOn() ? "" : "none"; heatSettings();
      if (heat) heat.show(heatOn()); document.dispatchEvent(new CustomEvent("ark-heat-state"));
    }
    ["h-heat", "h-dur", "h-end", "h-strength", "h-radius", "h-from", "h-to"].forEach(function (id) { $(id).addEventListener("change", heatChanged); });
    $("h-dur").value = pref("dur") || o.heatDuration || "24"; if ($("h-dur").value !== (pref("dur") || o.heatDuration || "24")) $("h-dur").value = "24";
    $("h-end").value = pref("end") || "0"; $("h-strength").value = pref("strength") || "mid"; $("h-radius").value = pref("radius") || "mid";
    (function () {   // the dates start as the last day, in the display zone
      var n = Date.now(), a = wall(n - 86400000), b = wall(n);
      $("h-from").value = dateStr(a) + "T" + clockStr(a); $("h-to").value = dateStr(b) + "T" + clockStr(b);
    })();
    var altTimer = null;
    ["f-min", "f-max"].forEach(function (id) { if ($(id)) $(id).addEventListener("input", function () { clearTimeout(altTimer); altTimer = setTimeout(function () { if (heat && heatOn()) heat.refresh(); }, 600); }); });
    setInterval(function () { if (heat && heatOn() && !document.hidden && !customOn() && ($("h-end").value === "0")) heat.refresh(); }, 5 * 60000);   // a window that ends now moves on

    // ---- replay
    var bar = $("lv-replay"); $("lv").appendChild(bar);   // the bar sits on the map's bottom edge, so it is always on the screen with the map
    function barSpace() { $("lv").style.setProperty("--rp", bar.style.display === "none" ? "0px" : (bar.offsetHeight + 12) + "px"); }   // the panels stop above it
    if (window.ResizeObserver) new ResizeObserver(barSpace).observe(bar);
    window.addEventListener("resize", barSpace); barSpace();
    function currentWall() { return wall(rp && rp.time() ? rp.time() : Date.now()); }
    function show(s) {
      if (!replayOn) return;
      var ms = s.ts || Date.now(), w = wall(ms);
      $("rp-time").textContent = window.fmtTime ? window.fmtTime(new Date(ms), "dts") + " · " + utcText(ms) : utcText(ms);
      $("rp-play").textContent = s.playing ? "Pause" : "Play";
      $("rp-state").textContent = s.loading ? "Loading..." : s.error ? s.error : s.playing ? "Playing at " + s.speed + "x" : "Paused";
      if (!dragging) { $("rp-slide").value = w.h * 60 + w.mi; if (document.activeElement !== $("rp-date")) $("rp-date").value = dateStr(w); if (document.activeElement !== $("rp-clock")) $("rp-clock").value = clockStr(w); }
    }
    function makePlayer() {
      return ArkReplay.create({onFrame: function (f) { o.ingest(f); }, onState: show});
    }
    function seekTo(ms) { if (!isNaN(ms)) { o.clear(); rp.seek(new Date(Math.min(ms, Date.now() - 60000))); } }
    function openReplay(ms) {
      if (replayOn || !window.ArkReplay) return;
      replayOn = true; rp = makePlayer(); bar.style.display = ""; barSpace(); $("h-replay").checked = true; pref("replay", "1");
      if (o.onMode) o.onMode(true);
      var start = ms && !isNaN(ms) ? ms : Date.now() - (o.startAgoMinutes || 60) * 60000;
      o.clear(); $("rp-speed").value = String(pref("speed") || o.speed || 10); rp.speed(parseFloat($("rp-speed").value) || 10);
      rp.open(new Date(Math.min(start, Date.now() - 60000))).then(function () { rp.play(); });
      if (map.invalidateSize) setTimeout(function () { map.invalidateSize(); }, 50);
    }
    function closeReplay() {
      if (!replayOn) return;
      replayOn = false; rp.close(); rp = null; bar.style.display = "none"; barSpace(); $("h-replay").checked = false; pref("replay", "0");
      o.clear(); if (o.onMode) o.onMode(false);
      if (map.invalidateSize) setTimeout(function () { map.invalidateSize(); }, 50);
    }
    $("h-replay").addEventListener("change", function () { if (this.checked) openReplay(); else closeReplay(); });
    $("rp-live").addEventListener("click", closeReplay);
    $("rp-play").addEventListener("click", function () { if (rp) rp.toggle(); });
    [["rp-b5", -300], ["rp-b1", -60], ["rp-f1", 60], ["rp-f5", 300]].forEach(function (b) { $(b[0]).addEventListener("click", function () { if (rp) { o.clear(); rp.step(b[1]); } }); });
    $("rp-speed").addEventListener("change", function () { pref("speed", this.value); if (rp) rp.speed(parseFloat(this.value) || 10); });
    function jump() { var ms = fromInputs($("rp-date").value, $("rp-clock").value); if (rp && !isNaN(ms)) seekTo(ms); }
    $("rp-date").addEventListener("change", jump); $("rp-clock").addEventListener("change", jump);
    $("rp-slide").addEventListener("input", function () { dragging = true; var m = +this.value, w = currentWall(); $("rp-clock").value = p2(Math.floor(m / 60)) + ":" + p2(m % 60); $("rp-date").value = dateStr(w); });
    $("rp-slide").addEventListener("change", function () { dragging = false; var m = +this.value, w = currentWall(); if (rp) seekTo(zoned(w.y, w.mo, w.d, Math.floor(m / 60), m % 60)); });

    return {
      replayOn: function () { return replayOn; }, heatOn: heatOn, openReplay: openReplay, closeReplay: closeReplay, player: function () { return rp; }, heat: function () { return heat; },
      toggleReplay: function () { if (replayOn) closeReplay(); else openReplay(); },
      heatLegend: function () { return heat && heatOn() ? '<h6>Heat map of past traffic</h6>' + heat.legend() + '<div class="dim" style="font-size:.74rem;margin-top:4px">Each square counts the positions the hub recorded in it during the period; the busiest squares are red.</div>' : ""; },
      params: function (p) {
        if (heatOn()) { p.set("heat", "1"); if (customOn()) { p.set("heatFrom", Math.floor(heatRange().from.getTime() / 1000)); p.set("heatTo", Math.floor(heatRange().to.getTime() / 1000)); } else { p.set("heatHours", $("h-dur").value); if ($("h-end").value !== "0") p.set("heatEnd", $("h-end").value); } }
        if (replayOn && rp) { p.set("replay", String(Math.floor(rp.time() / 1000))); p.set("replaySpeed", $("rp-speed").value); }
      },
      apply: function (q) {
        if (q.get("heatFrom") && q.get("heatTo")) {
          var a = new Date(+q.get("heatFrom") * 1000), b = new Date(+q.get("heatTo") * 1000);
          if (!isNaN(a) && !isNaN(b)) { var wa = wall(a), wb = wall(b); $("h-dur").value = "custom"; $("h-from").value = dateStr(wa) + "T" + clockStr(wa); $("h-to").value = dateStr(wb) + "T" + clockStr(wb); }
        } else {
          if (q.get("heatHours") && Array.prototype.some.call($("h-dur").options, function (x) { return x.value === q.get("heatHours"); })) $("h-dur").value = q.get("heatHours");
          if (q.get("heatEnd") && Array.prototype.some.call($("h-end").options, function (x) { return x.value === q.get("heatEnd"); })) $("h-end").value = q.get("heatEnd");
        }
        if (q.get("heat") === "1") $("h-heat").checked = true;
        if (q.get("replaySpeed")) pref("speed", q.get("replaySpeed"));
        heatChanged();
        if (q.has("replay")) { var t = +q.get("replay"); openReplay(t > 1e9 ? t * 1000 : NaN); }
      },
      boot: function () { heatChanged(); }   // the heat map is a mode you ask for (its menu entry, the box, a link): it never comes back by itself on the next visit or the next map you switch to
    };
  }
  window.ArkPast = {init: init};
})();
