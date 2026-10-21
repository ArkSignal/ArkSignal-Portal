/* Live weather across the whole map from every station that reports (members, APRS, airports): a heat map of the chosen reading, and wind streams that flow with the measured wind.
   Both are worked out in the browser from the stations' latest readings by distance weighting (the nearer a station, the more it counts; far from every station the map stays clear), so they follow
   whatever is switched on in Weather Sources and the filters. Public: ArkWxSurface.create(map, {points: fn -> [{lat, lon, r}], metric: fn -> a key of ArkWx.METRICS, mode: "off|heat|wind|both"}) -> {setMode, refresh}. */
(function () {
  "use strict";
  var R = Math.PI / 180, MAXKM = 140, WSOFT = 2500;   // a station counts out to this far (kilometers)
  function hex(c) { return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)]; }

  function stops(scale) {   // the colors of a scale as points along the values, so a value between two bins blends
    var out = [], prevHi = null;
    scale.forEach(function (s, i) {
      var hi = s[0] >= 1e8 ? null : s[0], lo = prevHi, mid;
      if (hi == null) mid = lo == null ? 0 : lo * 1.2 + 1e-6;
      else if (lo == null) mid = hi - ((scale[1] && scale[1][0] < 1e8 ? scale[1][0] : hi * 1.5) - hi) / 2;
      else mid = (lo + hi) / 2;
      out.push([mid, hex(s[1])]); if (hi != null) prevHi = hi;
    });
    return out;
  }
  function colorAt(st, v) {
    if (v <= st[0][0]) return st[0][1];
    for (var i = 1; i < st.length; i++) if (v <= st[i][0]) { var a = st[i - 1], b = st[i], t = (v - a[0]) / ((b[0] - a[0]) || 1); return [a[1][0] + (b[1][0] - a[1][0]) * t, a[1][1] + (b[1][1] - a[1][1]) * t, a[1][2] + (b[1][2] - a[1][2]) * t]; }
    return st[st.length - 1][1];
  }

  function create(map, o) {
    var mode = o.mode || "off", heat = document.createElement("canvas"), flow = document.createElement("canvas"), box = map.getContainer(), raf = 0, field = null, parts = [], fctx = flow.getContext("2d");
    // the canvases live in a map pane under the station markers and labels (the marker pane is above 450), so the labels are always on top
    var pane = map.getPane("wxsurf") || map.createPane("wxsurf"); pane.style.zIndex = 450; pane.style.pointerEvents = "none";
    [heat, flow].forEach(function (c) { c.style.cssText = "position:absolute;left:0;top:0;pointer-events:none;"; pane.appendChild(c); });
    function place() { var p = map.containerPointToLayerPoint([0, 0]); [heat, flow].forEach(function (c) { L.DomUtil.setPosition(c, p); }); }   // a drawing is made for the view as it stands, then moves with the map until the next one
    function size() { place(); var s = map.getSize(); [heat, flow].forEach(function (c) { if (c.width !== s.x || c.height !== s.y) { c.width = s.x; c.height = s.y; } }); }

    function stationsNow() {
      var m = ArkWx.METRICS[o.metric()] || ArkWx.METRICS.temp, f = m.f, pts = [];
      (o.points() || []).forEach(function (p) {
        var v = p.r ? p.r[f] : null; if (v != null && isNaN(v)) v = null;   // a station without the heat map's reading still carries wind for the streams (the two are independent)
        var u = null, w = null; if (p.r.wind_mph != null && p.r.wind_dir != null) { u = -p.r.wind_mph * Math.sin(p.r.wind_dir * R); w = -p.r.wind_mph * Math.cos(p.r.wind_dir * R); }
        if (v == null && u == null) return; pts.push({lat: p.lat, lon: p.lon, v: v, u: u, w: w});
      });
      return {pts: pts, metric: m};
    }
    // stations are kept in a coarse grid so a place asks only the few around it (a map of a whole region has hundreds of stations)
    var GLAT = 1.6, GLON = 2.0;
    function index(pts) { var g = {}; pts.forEach(function (p) { var k = Math.floor(p.lat / GLAT) + "," + Math.floor(p.lon / GLON); (g[k] = g[k] || []).push(p); }); return g; }
    function around(g, lat, lon) {
      var ci = Math.floor(lat / GLAT), cj = Math.floor(lon / GLON), out = [];
      for (var i = ci - 1; i <= ci + 1; i++) for (var j = cj - 1; j <= cj + 1; j++) { var b = g[i + "," + j]; if (b) for (var n = 0; n < b.length; n++) out.push(b[n]); }
      return out;
    }
    function idw(pts, lat, lon, key, soft) {   // the weighted value at a place, or null when no station is near enough
      var sw = 0, sv = 0, best = 1e9, cl = Math.cos(lat * R);
      for (var i = 0; i < pts.length; i++) {
        var p = pts[i], pv = key ? p[key] : p.v; if (pv == null) continue;
        var dx = (p.lon - lon) * cl * 111.32, dy = (p.lat - lat) * 111.32, d2 = dx * dx + dy * dy, d = Math.sqrt(d2);
        if (d > MAXKM) continue; if (d < best) best = d;
        var wgt = 1 / (d2 + (soft || 25)); sw += wgt; sv += wgt * pv;
      }
      return sw ? {v: sv / sw, near: best} : null;
    }

    var passTok = 0;
    function drawHeat() {   // in passes, from a coarse picture to the full one: something shows at once, and a newer view cancels what is still being drawn
      var ctx = heat.getContext("2d"); ctx.clearRect(0, 0, heat.width, heat.height);
      var tok = ++passTok;
      if (mode !== "heat" && mode !== "both") return;
      var d = stationsNow(), st = stops(d.metric.scale), w = heat.width, h = heat.height;
      if (!d.pts.length) return;
      var grid = index(d.pts), cells = [40, 16, 8];
      (function pass(n) {
        if (tok !== passTok || n >= cells.length) return;
        var cell = cells[n], img = ctx.createImageData(Math.ceil(w / cell), Math.ceil(h / cell)), W = img.width;
        for (var cy = 0; cy < img.height; cy++) for (var cx = 0; cx < W; cx++) {
          var ll = map.containerPointToLatLng([cx * cell + cell / 2, cy * cell + cell / 2]), g = idw(around(grid, ll.lat, ll.lng), ll.lat, ll.lng, null);
          if (!g) continue;
          var c = colorAt(st, g.v), a = 150 * Math.max(0, Math.min(1, 1 - (g.near - 40) / (MAXKM - 40))), k = (cy * W + cx) * 4;
          img.data[k] = c[0]; img.data[k + 1] = c[1]; img.data[k + 2] = c[2]; img.data[k + 3] = a;
        }
        var tmp = document.createElement("canvas"); tmp.width = img.width; tmp.height = img.height; tmp.getContext("2d").putImageData(img, 0, 0);
        ctx.clearRect(0, 0, w, h); ctx.imageSmoothingEnabled = true; ctx.drawImage(tmp, 0, 0, w, h);   // stretched, so the cells blend into one smooth surface
        setTimeout(function () { pass(n + 1); }, 30);
      })(0);
    }

    function buildField(step) {   // wind vectors on a coarse grid of the view, so a thousand particles ask a table rather than every station
      var s = map.getSize(), all = stationsNow().pts.filter(function (p) { return p.u != null; }), grid = index(all), gw = Math.ceil(s.x / step) + 1, gh = Math.ceil(s.y / step) + 1, U = new Float32Array(gw * gh), V = new Float32Array(gw * gh), OK = new Uint8Array(gw * gh);
      for (var y = 0; y < gh; y++) for (var x = 0; x < gw; x++) {
        var ll = map.containerPointToLatLng([x * step, y * step]), near = around(grid, ll.lat, ll.lng), gu = idw(near, ll.lat, ll.lng, "u", WSOFT), gv = idw(near, ll.lat, ll.lng, "w", WSOFT);   // a wide, soft blend: wind bends smoothly between stations instead of staying flat around each one
        if (gu && gv) { U[y * gw + x] = gu.v; V[y * gw + x] = gv.v; OK[y * gw + x] = 1; }
      }
      field = {gw: gw, gh: gh, step: step, U: U, V: V, OK: OK};
    }
    function sample(x, y) {   // bilinear: the wind at a pixel blends the four grid points around it, so streams curve instead of running in straight cells
      var f = field, fx = x / f.step, fy = y / f.step, gx = Math.floor(fx), gy = Math.floor(fy); if (gx < 0 || gy < 0 || gx >= f.gw - 1 || gy >= f.gh - 1) return null;
      var tx = fx - gx, ty = fy - gy, i00 = gy * f.gw + gx, i10 = i00 + 1, i01 = i00 + f.gw, i11 = i01 + 1;
      if (!(f.OK[i00] && f.OK[i10] && f.OK[i01] && f.OK[i11])) return f.OK[i00] ? [f.U[i00], f.V[i00]] : null;
      var w00 = (1 - tx) * (1 - ty), w10 = tx * (1 - ty), w01 = (1 - tx) * ty, w11 = tx * ty;
      return [f.U[i00] * w00 + f.U[i10] * w10 + f.U[i01] * w01 + f.U[i11] * w11, f.V[i00] * w00 + f.V[i10] * w10 + f.V[i01] * w01 + f.V[i11] * w11];
    }
    function spawn(p) { var s = map.getSize(); p.x = Math.random() * s.x; p.y = Math.random() * s.y; p.age = 0; p.life = 90 + Math.random() * 130; }
    function frame() {
      raf = 0; if (mode !== "wind" && mode !== "both") return;
      if (document.hidden) { raf = requestAnimationFrame(frame); return; }
      var s = map.getSize(); fctx.globalCompositeOperation = "destination-out"; fctx.fillStyle = "rgba(0,0,0,.06)"; fctx.fillRect(0, 0, s.x, s.y); fctx.globalCompositeOperation = "source-over";
      fctx.lineWidth = 1.6; fctx.lineCap = "round";
      parts.forEach(function (p) {
        var v = field && sample(p.x, p.y);
        if (!v || p.age++ > p.life || p.x < 0 || p.y < 0 || p.x > s.x || p.y > s.y) { spawn(p); return; }
        var sp = Math.sqrt(v[0] * v[0] + v[1] * v[1]), k = 0.28 * Math.max(0.6, Math.min(2.4, Math.pow(1.3, 7 - map.getZoom())));   // mph to pixels per frame; zoomed out the same wind covers fewer pixels, so it is sped up to keep the streams flowing
        var mid = sample(p.x + v[0] * k / 2, p.y - v[1] * k / 2) || v, nx = p.x + mid[0] * k, ny = p.y - mid[1] * k;   // a midpoint step: the path follows the bend of the wind field
        fctx.strokeStyle = sp < 5 ? "rgba(190,225,255,.75)" : sp < 12 ? "rgba(160,255,225,.85)" : sp < 22 ? "rgba(255,230,120,.9)" : "rgba(255,150,100,.95)";
        fctx.beginPath(); fctx.moveTo(p.x, p.y); fctx.lineTo(nx, ny); fctx.stroke(); p.x = nx; p.y = ny;
      });
      raf = requestAnimationFrame(frame);
    }
    function refresh() {
      size(); drawHeat(); fctx.clearRect(0, 0, flow.width, flow.height);
      if (mode === "wind" || mode === "both") {
        buildField(56); setTimeout(function () { if (mode === "wind" || mode === "both") buildField(28); }, 40);   // a coarse wind field first, the finer one right after
        var n = Math.min(2200, Math.round(map.getSize().x * map.getSize().y / 450)); while (parts.length < n) { var p = {}; spawn(p); p.age = Math.random() * 60; parts.push(p); } parts.length = n;
        if (!raf) raf = requestAnimationFrame(frame);
      }
    }
    var t = null; function later() { clearTimeout(t); fctx.clearRect(0, 0, flow.width, flow.height); heat.style.opacity = "0.35"; t = setTimeout(function () { heat.style.opacity = ""; refresh(); }, 250); }
    map.on("moveend zoomend resize", later); map.on("movestart zoomstart", function () { fctx.clearRect(0, 0, flow.width, flow.height); });
    return {setMode: function (m) { mode = m; refresh(); }, refresh: function () { if (mode !== "off") { clearTimeout(t); refresh(); } }, mode: function () { return mode; }};
  }
  window.ArkWxSurface = {create: create};
})();
