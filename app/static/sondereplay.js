/* Replay of recorded radiosonde and balloon flights on the Radiosonde Map: a marker for each flight moves along the positions the site recorded, with a trail, the burst and the landing marked, and a bar with play,
   pause, speed and a slider over the time of the flight(s). ArkSondeReplay.start(map, container, serials, {back: "/sondes/map"}). Needs the flight tracks from /api/sondes/<serial>/track.json. */
(function () {
  "use strict";
  var COLORS = ["#3ee6a8", "#ffb454", "#6fb3ff", "#ff6b9d", "#c79bff", "#ffd24f", "#4fd1c5", "#ff8c42"], SPEEDS = [1, 10, 30, 60, 120, 300, 600];
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function when(sec) { try { return new Date(sec * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit"}); } catch (e) { return new Date(sec * 1000).toISOString(); } }
  function span(sec) { sec = Math.max(0, Math.round(sec)); var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60); return h ? h + " h " + m + " min" : m + " min"; }
  function icon(kind, color, size) { return L.divIcon({className: "", html: window.SondeIcons ? SondeIcons.marker(kind, color, "", {size: size || 30}) : "", iconSize: [0, 0]}); }   // the same balloon, parachute, burst, launch and landed icons as the Radiosonde Map
  function at(f, T) {   // the state of one flight at a moment: index of the last position at or before T, and the place between that one and the next
    var t = f.t, n = t.length;
    if (T <= t[0]) return {i: 0, lat: f.lat[0], lon: f.lon[0], alt: f.alt[0], live: false};
    if (T >= t[n - 1]) return {i: n - 1, lat: f.lat[n - 1], lon: f.lon[n - 1], alt: f.alt[n - 1], live: false, done: true};
    var lo = 0, hi = n - 1;
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (t[mid] <= T) lo = mid; else hi = mid; }
    var k = (T - t[lo]) / ((t[hi] - t[lo]) || 1), a0 = f.alt[lo], a1 = f.alt[hi];
    return {i: lo, lat: f.lat[lo] + (f.lat[hi] - f.lat[lo]) * k, lon: f.lon[lo] + (f.lon[hi] - f.lon[lo]) * k, alt: a0 == null || a1 == null ? (a0 == null ? a1 : a0) : a0 + (a1 - a0) * k, live: true};
  }
  window.ArkSondeReplay = {
    start: function (map, container, serials, o) {
      o = o || {};
      var bar = document.createElement("div"); bar.id = "sr-bar"; bar.className = "lv-replay"; bar.setAttribute("role", "group"); bar.setAttribute("aria-label", "Replay of recorded balloon flights");
      var EMB = '<button class="btn small" type="button" id="sr-play">Pause</button><button class="btn small neutral" type="button" id="sr-back">Stop Replay</button><label style="margin:0">Speed <select id="sr-speed" style="width:auto">' + [10, 30, 60, 120, 300].map(function (s) { return '<option value="' + s + '"' + (s === 60 ? " selected" : "") + ">" + s + "x</option>"; }).join("") + '</select></label><input type="range" id="sr-slide" min="0" max="1000" step="1" value="0" style="flex:1 1 160px;min-width:120px" aria-label="Time of the flight"><span id="sr-time" class="mono"></span>' +
        '<span hidden><span id="sr-state"></span><button type="button" id="sr-b"></button><button type="button" id="sr-f"></button><input type="checkbox" id="sr-follow" checked><label id="sr-alignl"><input type="checkbox" id="sr-align"></label><span id="sr-read"></span></span>';   // on a flight's own page the bar is the same one the aircraft Flight page has
      bar.innerHTML = '<div class="rp-top"><b>Balloon Replay</b> <span id="sr-time" class="mono"></span> <span id="sr-state" class="dim"></span>' + (o.embed ? '<button class="btn small neutral" type="button" id="sr-back" style="margin:0 0 0 auto">Stop Replay</button>' : '<a class="btn small neutral" id="sr-back" href="' + esc(o.back || "/sondes/map") + '" style="margin:0 0 0 auto">Back to the Map</a>') + '</div>' +
        '<div class="rp-ctl"><button class="btn small" type="button" id="sr-play">Play</button><button class="btn small neutral" type="button" id="sr-b" title="Back one minute of the flight">&minus;1 min</button><button class="btn small neutral" type="button" id="sr-f" title="Forward one minute of the flight">+1 min</button>' +
        '<label>Speed <select id="sr-speed">' + SPEEDS.map(function (s) { return '<option value="' + s + '">' + s + "x</option>"; }).join("") + '</select></label><label><input type="checkbox" id="sr-follow" checked> Follow</label><label id="sr-alignl" style="display:none"><input type="checkbox" id="sr-align"> Start every flight together</label></div>' +
        '<input type="range" id="sr-slide" min="0" max="1000" step="1" value="0" aria-label="Time of the flight"><div id="sr-read" class="dim" style="font-size:.8rem;margin-top:4px"></div>';
      if (o.embed) { bar = container; bar.id = "sr-bar"; bar.className = "dim"; bar.style.cssText = "display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:.85rem"; bar.innerHTML = EMB; } else container.appendChild(bar);
      var $ = function (id) { return document.getElementById(id); };
      $("sr-state").textContent = "Loading the flights...";
      Promise.all(serials.map(function (s) { return fetch("/api/sondes/" + encodeURIComponent(s) + "/track.json").then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); })).then(function (all) {
        var F = [];
        all.forEach(function (d, k) {
          if (!d || !d.track || !d.track.t || d.track.t.length < 2) return;
          var tr = d.track, color = COLORS[F.length % COLORS.length], hi = 0;
          tr.alt.forEach(function (a, i) { if (a != null && (tr.alt[hi] == null || a > tr.alt[hi])) hi = i; });
          var f = {serial: d.serial, t: tr.t, lat: tr.lat, lon: tr.lon, alt: tr.alt, color: color, hi: hi, shift: 0, type: d.type || "", site: d.site || "", launch: d.launch};
          f.mark = L.marker([f.lat[0], f.lon[0]], {icon: icon("climbing", color, 32), zIndexOffset: 500}).addTo(map).bindTooltip(esc(f.serial), {permanent: true, direction: "right", offset: [18, 0], className: "lv-tip"}); f.kind = "climbing";
          f.start = L.marker([f.lat[0], f.lon[0]], {icon: icon("launch", "#4fb3ff", 26), zIndexOffset: 100}).addTo(map).bindTooltip("Launch" + (d.site ? " from " + esc(d.site) : ""), {className: "lv-tip", direction: "top"});
          f.trail = L.polyline([], {color: color, weight: 3, opacity: .9}).addTo(map);
          f.full = L.polyline(f.lat.map(function (la, i) { return [la, f.lon[i]]; }), {color: color, weight: 1, opacity: .25, dashArray: "4 4", interactive: false}).addTo(map);
          f.burst = null; f.land = null; f.shown = -1;
          F.push(f);
        });
        if (!F.length) { $("sr-state").textContent = "No recorded positions were found for " + (serials.length === 1 ? "that flight" : "those flights") + "."; return; }
        var all_pts = []; F.forEach(function (f) { f.lat.forEach(function (la, i) { all_pts.push([la, f.lon[i]]); }); });
        map.fitBounds(all_pts, {padding: [40, 40]});
        var T0, T1, T = 0, speed = 60, playing = false, last = 0, aligned = false;
        function timeline() {
          F.forEach(function (f) { f.shift = aligned ? F[0].t[0] - f.t[0] : 0; });
          T0 = Math.min.apply(null, F.map(function (f) { return f.t[0] + f.shift; })); T1 = Math.max.apply(null, F.map(function (f) { return f.t[f.t.length - 1] + f.shift; }));
        }
        timeline();
        if (F.length > 1 && (T1 - T0) > 6 * 3600) { $("sr-alignl").style.display = ""; }
        T = T0;
        function draw() {
          F.forEach(function (f) {
            var s = at(f, T - f.shift);
            f.mark.setLatLng([s.lat, s.lon]);
            var kind = s.done ? "landed" : (T - f.shift >= f.t[f.hi] && f.hi > 0 && f.hi < f.t.length - 1) ? "falling" : "climbing";   // a balloon while it climbs, a parachute after the burst, the sonde on the ground at the end
            if (kind !== f.kind) { f.kind = kind; f.mark.setIcon(icon(kind, f.color, 32)); }
            if (s.i !== f.shown) {   // the trail only changes when the flight passes a recorded position
              var pts = []; for (var i = 0; i <= s.i; i++) pts.push([f.lat[i], f.lon[i]]);
              f.trail.setLatLngs(pts); f.shown = s.i;
            }
            var past_burst = T - f.shift >= f.t[f.hi] && f.hi > 0 && f.hi < f.t.length - 1;
            if (past_burst && !f.burst) f.burst = L.marker([f.lat[f.hi], f.lon[f.hi]], {icon: icon("burst", "#ffb454", 26), zIndexOffset: 200}).addTo(map).bindTooltip("Burst: " + Math.round(f.alt[f.hi] || 0) + " m", {className: "lv-tip"});
            if (!past_burst && f.burst) { map.removeLayer(f.burst); f.burst = null; }
            if (s.done && !f.land) f.land = L.marker([f.lat[f.lat.length - 1], f.lon[f.lon.length - 1]], {icon: icon("landed", "#3ee6a8", 24), zIndexOffset: 200}).addTo(map).bindTooltip("Landed", {className: "lv-tip"});
            if (!s.done && f.land) { map.removeLayer(f.land); f.land = null; }
            f.state = s;
          });
          $("sr-time").textContent = when(T) + (o.embed && F[0].state && F[0].state.alt != null ? " · " + (window.ArkUnits && ArkUnits.avia ? ArkUnits.avia.alt(F[0].state.alt * 3.28084) : Math.round(F[0].state.alt) + " m") : "");
          $("sr-slide").value = String(Math.round((T - T0) / ((T1 - T0) || 1) * 1000));
          $("sr-read").innerHTML = F.map(function (f) { var s = f.state; return '<span style="color:' + f.color + '">&#9679;</span> ' + esc(f.serial) + ": " + (s.alt == null ? "" : Math.round(s.alt).toLocaleString() + " m, ") + (s.done ? "landed" : s.live ? "in the air" : "not launched yet"); }).join(" &nbsp; ");
          $("sr-state").textContent = playing ? "Playing, " + speed + "x" : "Paused";
          if ($("sr-follow").checked && F.length === 1) { var ll = F[0].mark.getLatLng(); if (!map.getBounds().pad(-0.25).contains(ll)) map.panTo(ll, {animate: true}); }
        }
        function tick(now) {
          if (!playing) return;
          var dt = last ? (now - last) / 1000 : 0; last = now;
          T = Math.min(T1, T + dt * speed);
          draw();
          if (T >= T1) { playing = false; $("sr-play").textContent = "Replay Again"; draw(); return; }
          requestAnimationFrame(tick);
        }
        function play() { if (T >= T1) T = T0; playing = true; last = 0; $("sr-play").textContent = "Pause"; requestAnimationFrame(tick); }
        function pause() { playing = false; $("sr-play").textContent = "Play"; draw(); }
        $("sr-play").addEventListener("click", function () { if (playing) pause(); else play(); });
        $("sr-b").addEventListener("click", function () { T = Math.max(T0, T - 60); draw(); });
        $("sr-f").addEventListener("click", function () { T = Math.min(T1, T + 60); draw(); });
        $("sr-speed").value = "60"; $("sr-speed").addEventListener("change", function () { speed = parseFloat(this.value) || 60; if (!playing) draw(); });
        $("sr-align").addEventListener("change", function () { aligned = this.checked; timeline(); T = T0; F.forEach(function (f) { f.shown = -1; }); draw(); });
        $("sr-slide").addEventListener("input", function () { T = T0 + (+this.value) / 1000 * (T1 - T0); draw(); });
        $("sr-state").textContent = F.length + " flight" + (F.length === 1 ? "" : "s") + ", " + span(T1 - T0);
        $("sr-speed").value = "60";
        function stop() {   // an embedded replay (on a flight's own page) takes its markers and bar away and leaves the page's map as it was
          playing = false;
          F.forEach(function (f) { [f.mark, f.start, f.trail, f.full, f.burst, f.land].forEach(function (l) { if (l) map.removeLayer(l); }); });
          if (o.embed) { bar.removeAttribute("id"); bar.removeAttribute("class"); bar.removeAttribute("style"); bar.innerHTML = ""; } else bar.remove(); window.smReplay = null; if (o.onStop) o.onStop();
        }
        if (o.embed) $("sr-back").addEventListener("click", stop);
        window.smReplay = {stop: stop, play: play, pause: pause, seek: function (frac) { T = T0 + frac * (T1 - T0); draw(); }, state: function () { return {playing: playing, T: T, T0: T0, T1: T1, flights: F.map(function (f) { return {serial: f.serial, i: f.state && f.state.i, alt: f.state && f.state.alt, done: !!(f.state && f.state.done), burst: !!f.burst, landed: !!f.land, kind: f.kind, launch: !!f.start}; })}; }};
        draw();
        setTimeout(play, 400);
      });
    }
  };
})();
