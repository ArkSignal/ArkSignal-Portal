/* Small SVG charts for the balloon flight page: a line chart with a hover readout (x can be time or altitude) and a Skew-T log-P diagram. No libraries.
   SondeCharts.line(el, {x: [...], xlabel, xfmt, series: [{y: [...], color, label, unit}], reverseY: false, ylabel, height})
   SondeCharts.skewT(el, track)  track = {alt: [...m], temp: [...C], hum: [...%], press: [...hPa]} */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, parent) { var e = document.createElementNS(NS, tag); Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); }); if (parent) parent.appendChild(e); return e; }
  function esc(s) { return String(s).replace(/[&<>]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]; }); }
  function nice(lo, hi, n) {
    var span = (hi - lo) || 1, step = Math.pow(10, Math.floor(Math.log10(span / n))), err = span / n / step;
    step *= err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    var t = [], v = Math.ceil(lo / step) * step; for (; v <= hi + step * 1e-6; v += step) t.push(+v.toFixed(10)); return t;
  }
  function cssVar(n, d) { var v = getComputedStyle(document.documentElement).getPropertyValue(n).trim(); return v || d; }

  function line(host, o) {
    var W = 640, H = o.height || 215, m = {l: 54, r: 14, t: 10, b: 34}, pw = W - m.l - m.r, ph = H - m.t - m.b;
    host.innerHTML = "";
    var svg = el("svg", {viewBox: "0 0 " + W + " " + H, width: "100%", style: "display:block;width:100%;height:auto", role: "img", "aria-label": o.title || ""}, host);
    var xs = o.x, x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    xs.forEach(function (v) { if (v != null) { x0 = Math.min(x0, v); x1 = Math.max(x1, v); } });
    o.series.forEach(function (s) { s.y.forEach(function (v) { if (v != null) { y0 = Math.min(y0, v); y1 = Math.max(y1, v); } }); });
    if (!isFinite(x0) || !isFinite(y0)) { host.innerHTML = '<div class="dim" style="padding:14px">No data for this chart.</div>'; return; }
    if (y0 === y1) { y0 -= 1; y1 += 1; } var pad = (y1 - y0) * .06; y0 -= pad; y1 += pad; if (o.zero && y0 > 0) y0 = 0;
    function X(v) { return m.l + (v - x0) / ((x1 - x0) || 1) * pw; }
    function Y(v) { return m.t + (1 - (v - y0) / (y1 - y0)) * ph; }
    var grid = cssVar("--line", "#25324a"), dim = cssVar("--dim", "#8a9bb3");
    nice(y0, y1, 5).forEach(function (t) { el("line", {x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), stroke: grid, "stroke-width": 1}, svg); var tx = el("text", {x: m.l - 6, y: Y(t) + 4, "text-anchor": "end", fill: dim, "font-size": 11}, svg); tx.textContent = (o.yfmt ? o.yfmt(t) : t); });
    nice(x0, x1, 6).forEach(function (t) { el("line", {x1: X(t), x2: X(t), y1: m.t, y2: H - m.b, stroke: grid, "stroke-width": 1, opacity: .5}, svg); var tx = el("text", {x: X(t), y: H - m.b + 15, "text-anchor": "middle", fill: dim, "font-size": 11}, svg); tx.textContent = o.xfmt ? o.xfmt(t) : t; });
    var xl = el("text", {x: m.l + pw / 2, y: H - 3, "text-anchor": "middle", fill: dim, "font-size": 11}, svg); xl.textContent = o.xlabel || "";
    var yl = el("text", {x: 12, y: m.t + ph / 2, "text-anchor": "middle", fill: dim, "font-size": 11, transform: "rotate(-90 12 " + (m.t + ph / 2) + ")"}, svg); yl.textContent = o.ylabel || "";
    o.series.forEach(function (s) {
      var d = "", pen = false;
      xs.forEach(function (xv, i) { var yv = s.y[i]; if (xv == null || yv == null) { pen = false; return; } d += (pen ? "L" : "M") + X(xv).toFixed(1) + "," + Y(yv).toFixed(1); pen = true; });
      el("path", {d: d, fill: "none", stroke: s.color, "stroke-width": 1.8, "stroke-linejoin": "round"}, svg);
    });
    (o.marks || []).forEach(function (mk) { if (mk.x >= x0 && mk.x <= x1) { el("line", {x1: X(mk.x), x2: X(mk.x), y1: m.t, y2: H - m.b, stroke: mk.color || dim, "stroke-dasharray": "4 4", opacity: .8}, svg); var t = el("text", {x: X(mk.x) + 4, y: m.t + 11, fill: mk.color || dim, "font-size": 10}, svg); t.textContent = mk.label; } });
    var cross = el("line", {y1: m.t, y2: H - m.b, stroke: dim, opacity: 0, "stroke-width": 1}, svg), tip = document.createElement("div");
    tip.style.cssText = "position:absolute;pointer-events:none;background:var(--card-bg,#111b2e);border:1px solid var(--line,#25324a);border-radius:8px;padding:4px 8px;font:600 .75rem 'JetBrains Mono',monospace;display:none;white-space:nowrap;z-index:5";
    host.style.position = "relative"; host.appendChild(tip);
    svg.addEventListener("mousemove", function (e) {
      var r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W; if (px < m.l || px > W - m.r) { tip.style.display = "none"; cross.setAttribute("opacity", 0); return; }
      var xv = x0 + (px - m.l) / pw * (x1 - x0), bi = 0, bd = Infinity; xs.forEach(function (v, i) { if (v != null && Math.abs(v - xv) < bd) { bd = Math.abs(v - xv); bi = i; } });
      cross.setAttribute("x1", X(xs[bi])); cross.setAttribute("x2", X(xs[bi])); cross.setAttribute("opacity", .7);
      tip.innerHTML = esc(o.xlabel ? o.xlabel.replace(/ \(.*/, "") : "") + " " + esc(o.xfmt ? o.xfmt(xs[bi]) : xs[bi]) + o.series.map(function (s) { var v = s.y[bi]; return "<br><span style='color:" + s.color + "'>" + esc(s.label) + "</span> " + (v == null ? "-" : esc(o.yfmt ? o.yfmt(v) : v.toFixed(1)) + " " + esc(s.unit || "")); }).join("");
      tip.style.display = "block"; tip.style.left = Math.min(r.width - 150, Math.max(0, (X(xs[bi]) / W * r.width) + 10)) + "px"; tip.style.top = "6px";
    });
    svg.addEventListener("mouseleave", function () { tip.style.display = "none"; cross.setAttribute("opacity", 0); });
  }

  // Bar chart with the same grid, axis look and hover readout as line(). o = {data: [{n, label, tip}], height, ylabel, nfmt}
  function bars(host, o) {
    var d = o.data || [];
    host.innerHTML = "";
    if (!d.length) { host.innerHTML = '<div class="dim" style="padding:14px">Nothing in these dates and filters.</div>'; return; }
    var W = 640, H = o.height || 215, m = {l: 54, r: 14, t: 10, b: 34}, pw = W - m.l - m.r, ph = H - m.t - m.b, max = 0;
    d.forEach(function (r) { max = Math.max(max, r.n); }); if (!max) max = 1;
    var svg = el("svg", {viewBox: "0 0 " + W + " " + H, width: "100%", style: "display:block;width:100%;height:auto", role: "img", "aria-label": o.title || ""}, host);
    var grid = cssVar("--line", "#25324a"), dim = cssVar("--dim", "#8a9bb3"), acc = cssVar("--acc", "#4fb3ff"), bw = pw / d.length;
    function Y(v) { return m.t + (1 - v / max) * ph; }
    nice(0, max, 5).forEach(function (t) { el("line", {x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), stroke: grid, "stroke-width": 1}, svg); var tx = el("text", {x: m.l - 6, y: Y(t) + 4, "text-anchor": "end", fill: dim, "font-size": 11}, svg); tx.textContent = t; });
    var every = Math.ceil(d.length / 8), rects = [];
    d.forEach(function (r, i) {
      var x = m.l + i * bw, bh = r.n / max * ph;
      rects.push(el("rect", {x: x + 1, y: m.t + ph - bh, width: Math.max(1, bw - 2), height: bh, fill: acc, opacity: .85}, svg));
      if (d.length <= 12 || i % every === 0) { var tx = el("text", {x: x + bw / 2, y: H - m.b + 15, "text-anchor": "middle", fill: dim, "font-size": 11}, svg); tx.textContent = r.label; }
    });
    if (o.ylabel) { var yl = el("text", {x: 12, y: m.t + ph / 2, "text-anchor": "middle", fill: dim, "font-size": 11, transform: "rotate(-90 12 " + (m.t + ph / 2) + ")"}, svg); yl.textContent = o.ylabel; }
    var tip = document.createElement("div");
    tip.style.cssText = "position:absolute;pointer-events:none;background:var(--card-bg,#111b2e);border:1px solid var(--line,#25324a);border-radius:8px;padding:4px 8px;font:600 .75rem 'JetBrains Mono',monospace;display:none;white-space:nowrap;z-index:5";
    host.style.position = "relative"; host.appendChild(tip);
    var cur = -1;
    function off() { tip.style.display = "none"; if (cur >= 0) rects[cur].setAttribute("opacity", .85); cur = -1; }
    svg.addEventListener("mousemove", function (e) {
      var r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W, i = Math.floor((px - m.l) / bw);
      if (px < m.l || px > W - m.r || i < 0 || i >= d.length) { off(); return; }
      if (cur !== i) { if (cur >= 0) rects[cur].setAttribute("opacity", .85); rects[i].setAttribute("opacity", 1); cur = i; }
      tip.innerHTML = esc(d[i].tip || d[i].label) + "<br>" + (o.nfmt ? esc(o.nfmt(d[i].n)) : d[i].n);
      tip.style.display = "block"; tip.style.left = Math.min(r.width - 150, Math.max(0, ((m.l + (i + .5) * bw) / W * r.width) + 10)) + "px"; tip.style.top = "6px";
    });
    svg.addEventListener("mouseleave", off);
  }

  // standard-atmosphere pressure (hPa) from altitude (m), used when the sonde sent no pressure
  function stdPress(h) { return h < 11000 ? 1013.25 * Math.pow(1 - 2.25577e-5 * h, 5.25588) : 226.32 * Math.exp(-(h - 11000) / 6341.6); }
  function dew(t, rh) { if (t == null || rh == null || rh <= 0) return null; var a = 17.62, b = 243.12, g = Math.log(rh / 100) + a * t / (b + t); return b * g / (a - g); }

  function skewT(host, tr) {
    var W = 520, H = 520, m = {l: 44, r: 14, t: 12, b: 34}, pw = W - m.l - m.r, ph = H - m.t - m.b, pTop = 100, pBot = 1050, skew = 0.9;
    host.innerHTML = "";
    var rows = [];
    tr.alt.forEach(function (a, i) { var p = tr.press[i] != null ? tr.press[i] : (a != null ? stdPress(a) : null); if (p != null && tr.temp[i] != null && p >= pTop && p <= pBot) rows.push({p: p, t: tr.temp[i], td: dew(tr.temp[i], tr.hum[i]), a: a}); });
    // only the climb (to the highest point) gives a clean profile
    var top = 0; rows.forEach(function (r, i) { if (r.p < rows[top].p) top = i; }); rows = rows.slice(0, top + 1);
    if (rows.length < 10) { host.innerHTML = '<div class="dim" style="padding:14px">Not enough temperature data on the way up for a Skew-T diagram.</div>'; return; }
    var svg = el("svg", {viewBox: "0 0 " + W + " " + H, width: "100%", style: "display:block;max-width:560px", role: "img", "aria-label": "Skew-T log-P diagram"}, host);
    var grid = cssVar("--line", "#25324a"), dim = cssVar("--dim", "#8a9bb3");
    function Yp(p) { return m.t + (Math.log(p) - Math.log(pTop)) / (Math.log(pBot) - Math.log(pTop)) * ph; }
    function Xt(t, p) { return m.l + ((t + 40) / 100) * pw * 0.6 + (Yp(pBot) - Yp(p)) * skew * 0.9 - 0; }
    var clip = el("clipPath", {id: "stclip"}, svg); el("rect", {x: m.l, y: m.t, width: pw, height: ph}, clip);
    var g = el("g", {"clip-path": "url(#stclip)"}, svg);
    for (var t = -100; t <= 50; t += 10) { var d = ""; [pBot, pTop].forEach(function (p, i) { d += (i ? "L" : "M") + Xt(t, p).toFixed(1) + "," + Yp(p).toFixed(1); }); el("path", {d: d, stroke: t === 0 ? "#4fb3ff" : grid, "stroke-width": t === 0 ? 1.2 : 1, fill: "none", opacity: .7}, g); }
    [1000, 850, 700, 500, 400, 300, 250, 200, 150, 100].forEach(function (p) { el("line", {x1: m.l, x2: W - m.r, y1: Yp(p), y2: Yp(p), stroke: grid}, svg); var tx = el("text", {x: m.l - 5, y: Yp(p) + 4, "text-anchor": "end", fill: dim, "font-size": 11}, svg); tx.textContent = p; });
    var yl = el("text", {x: 11, y: m.t + ph / 2, fill: dim, "font-size": 11, "text-anchor": "middle", transform: "rotate(-90 11 " + (m.t + ph / 2) + ")"}, svg); yl.textContent = "Pressure (hPa)";
    for (var tt = -40; tt <= 40; tt += 10) { var tx2 = el("text", {x: Xt(tt, pBot), y: H - m.b + 15, "text-anchor": "middle", fill: dim, "font-size": 11}, svg); tx2.textContent = tt; }
    var xl = el("text", {x: m.l + pw / 2, y: H - 3, fill: dim, "font-size": 11, "text-anchor": "middle"}, svg); xl.textContent = "Temperature at the surface line (°C), lines lean right with height";
    function path(key, color) {
      var dd = "", pen = false; rows.forEach(function (r) { var v = r[key]; if (v == null) { pen = false; return; } dd += (pen ? "L" : "M") + Xt(v, r.p).toFixed(1) + "," + Yp(r.p).toFixed(1); pen = true; });
      el("path", {d: dd, fill: "none", stroke: color, "stroke-width": 2.2, "stroke-linejoin": "round"}, g);
    }
    path("td", "#4fb3ff"); path("t", "#ff6b6b");
    var lg = el("text", {x: W - m.r - 4, y: m.t + 14, "text-anchor": "end", "font-size": 11, fill: "#ff6b6b"}, svg); lg.textContent = "Temperature";
    var lg2 = el("text", {x: W - m.r - 4, y: m.t + 28, "text-anchor": "end", "font-size": 11, fill: "#4fb3ff"}, svg); lg2.textContent = "Dew point";
  }
  window.SondeCharts = {line: line, bars: bars, skewT: skewT, dew: dew, stdPress: stdPress};
})();
