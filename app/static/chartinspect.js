/* Hover read-out for the weather station charts (drawn on the server as SVG with their data attached): a vertical line follows the pointer and a small box shows the time and the value of
   every line at that moment, like Grafana. Touch: tap or drag across the chart. */
(function () {
  "use strict";
  function fmtTime(sec, tz, period) {
    try { return new Intl.DateTimeFormat([], {timeZone: tz, month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(sec * 1000)); }
    catch (e) { return new Date(sec * 1000).toLocaleString(); }
  }
  function attach(svg) {
    var m; try { m = JSON.parse(svg.getAttribute("data-chart")); } catch (e) { return; }
    var host = svg.parentNode; if (getComputedStyle(host).position === "static") host.style.position = "relative";
    var NS = "http://www.w3.org/2000/svg", line = document.createElementNS(NS, "line"), dots = [];
    line.setAttribute("y1", m.pt); line.setAttribute("y2", m.pt + m.h); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-opacity", ".55"); line.setAttribute("stroke-dasharray", "3 3"); line.style.display = "none"; svg.appendChild(line);
    m.series.forEach(function (s) { var c = document.createElementNS(NS, "circle"); c.setAttribute("r", 4); c.setAttribute("fill", s.color); c.setAttribute("stroke", "#fff"); c.setAttribute("stroke-width", 1.2); c.style.display = "none"; svg.appendChild(c); dots.push(c); });
    var tip = document.createElement("div");
    tip.style.cssText = "position:absolute;pointer-events:none;display:none;z-index:6;background:var(--card-bg,#111b2e);border:1px solid var(--line,#25324a);border-radius:10px;padding:6px 10px;font:600 .78rem 'JetBrains Mono',monospace;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.35)";
    host.appendChild(tip);
    var span = m.t1 - m.t0;
    function X(t) { return m.pl + m.w * (t - m.t0) / span; }
    function Y(v) { return m.pt + m.h * (1 - (v - m.lo) / (m.hi - m.lo)); }
    function move(clientX) {
      var r = svg.getBoundingClientRect(), px = (clientX - r.left) / r.width * m.width;
      if (px < m.pl || px > m.pl + m.w) return out();
      var t = m.t0 + (px - m.pl) / m.w * span, best = null, rows = [];
      m.series.forEach(function (s, i) {
        var bi = -1, bd = Infinity;
        s.pts.forEach(function (p, k) { var d = Math.abs(p[0] - t); if (d < bd) { bd = d; bi = k; } });
        if (bi >= 0 && bd <= Math.max(m.bucket * 2.5, 900)) { var p = s.pts[bi]; rows.push([s, p, i]); if (best === null || bd < Math.abs(best - t)) best = p[0]; }
      });
      if (!rows.length) return out();
      var x = X(best); line.setAttribute("x1", x); line.setAttribute("x2", x); line.style.display = "";
      dots.forEach(function (d) { d.style.display = "none"; });
      rows.forEach(function (q) { var d = dots[q[2]]; d.setAttribute("cx", X(q[1][0])); d.setAttribute("cy", Y(q[1][1])); d.style.display = ""; });
      tip.innerHTML = '<div style="color:var(--dim,#8a9bb3);margin-bottom:3px">' + fmtTime(best, m.tz) + "</div>" + rows.map(function (q) { return '<div><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + q[0].color + ';margin-right:6px"></span>' + q[0].label + ": " + q[1][1] + (m.unit ? " " + m.unit : "") + "</div>"; }).join("");
      tip.style.display = "block";
      var hx = x / m.width * r.width, w = tip.offsetWidth;
      tip.style.left = (hx + 14 + w > r.width ? Math.max(0, hx - 14 - w) : hx + 14) + "px"; tip.style.top = "6px";
    }
    function out() { line.style.display = "none"; tip.style.display = "none"; dots.forEach(function (d) { d.style.display = "none"; }); }
    svg.addEventListener("mousemove", function (e) { move(e.clientX); });
    svg.addEventListener("mouseleave", out);
    svg.addEventListener("touchstart", function (e) { if (e.touches[0]) move(e.touches[0].clientX); }, {passive: true});
    svg.addEventListener("touchmove", function (e) { if (e.touches[0]) move(e.touches[0].clientX); }, {passive: true});
    svg.addEventListener("touchend", function () { setTimeout(out, 1800); });
  }
  function init() { Array.prototype.forEach.call(document.querySelectorAll("svg.chart[data-chart]"), attach); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
