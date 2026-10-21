/* "Limit to an area": pick one or more counties, cities or ZIP codes (the same places the map search knows) and anything with a position is kept or left out by whether it lies inside. A county is its real outline
   (US Census); a city or ZIP is a circle of the chosen radius around its center. The choice is remembered per key in this browser. Public:
   ArkArea.attach(container, {key, onChange, label}) -> {test(lat, lon) -> true when nothing is picked or the place is inside, active() -> bool, areas()}  */
(function () {
  "use strict";
  var shapes = null, shapesLoading = null;
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  function load(key) { try { return JSON.parse(localStorage.getItem("lv.area." + key) || "[]") || []; } catch (e) { return []; } }
  function save(key, v) { try { localStorage.setItem("lv.area." + key, JSON.stringify(v)); } catch (e) {} }
  function loadShapes() {
    if (shapes) return Promise.resolve(shapes);
    return shapesLoading || (shapesLoading = fetch("/api/counties/shapes.json").then(function (r) { return r.json(); }).then(function (d) { shapes = d.counties || []; return shapes; }).catch(function () { shapes = []; return shapes; }));
  }
  function inRing(lat, lon, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function inGeom(lat, lon, g) {
    var polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    return polys.some(function (p) { return inRing(lat, lon, p[0]) && !p.slice(1).some(function (h) { return inRing(lat, lon, h); }); });
  }
  function km(a, b, c, d) { var p = Math.PI / 180, h = Math.sin((c - a) * p / 2) ** 2 + Math.cos(a * p) * Math.cos(c * p) * Math.sin((d - b) * p / 2) ** 2; return 12742 * Math.asin(Math.sqrt(h)); }

  function attach(box, o) {
    var areas = load(o.key), radius = parseInt(localStorage.getItem("lv.area.r") || "25", 10) || 25, timer = null, seq = 0;
    var el = document.createElement("div"); el.className = "areaf";
    el.innerHTML = '<div class="lv-sub" style="margin-top:6px">' + esc(o.label || "Limit to an area") + '</div><div class="areaf-chips"></div>' +
      '<div class="lv-row" style="margin-top:4px"><input type="search" class="areaf-q" placeholder="Add a county, city or ZIP" aria-label="Add an area" autocomplete="off" style="flex:1 1 140px;min-width:0"><select class="areaf-r" aria-label="Radius around a city or ZIP"><option value="10">10 mi</option><option value="25">25 mi</option><option value="50">50 mi</option></select></div>' +
      '<div class="areaf-res"></div>';
    box.appendChild(el);
    var chips = el.querySelector(".areaf-chips"), q = el.querySelector(".areaf-q"), res = el.querySelector(".areaf-res"), sel = el.querySelector(".areaf-r");
    sel.value = String(radius);
    function draw() {
      chips.innerHTML = areas.map(function (a, i) { return '<span class="chip" data-i="' + i + '">' + esc(a.label) + (a.kind === "county" ? "" : " · " + sel.value + " mi") + "<b>×</b></span>"; }).join("") + (areas.length ? "" : '<span class="dim" style="font-size:.78rem">Everywhere</span>');
    }
    function changed() { save(o.key, areas); draw(); if (o.onChange) o.onChange(); }
    chips.addEventListener("click", function (e) { var c = e.target.closest(".chip"); if (!c) return; areas.splice(parseInt(c.getAttribute("data-i"), 10), 1); changed(); });
    sel.addEventListener("change", function () { radius = parseInt(sel.value, 10); try { localStorage.setItem("lv.area.r", sel.value); } catch (e) {} draw(); if (areas.some(function (a) { return a.kind !== "county"; }) && o.onChange) o.onChange(); });
    q.addEventListener("input", function () {
      clearTimeout(timer); var t = q.value.trim(), me = ++seq; if (t.length < 2) { res.innerHTML = ""; return; }
      timer = setTimeout(function () {
        fetch("/api/places/search.json?q=" + encodeURIComponent(t)).then(function (r) { return r.json(); }).then(function (d) {
          if (me !== seq) return;
          res.innerHTML = (d.results || []).slice(0, 6).map(function (p, i) { return '<a href="#" class="areaf-pick" data-i="' + i + '" style="display:block;padding:4px 6px;font-size:.84rem">' + esc(p.label) + ' <span class="dim">' + (p.kind === "county" ? "county" : p.kind === "zip" ? "ZIP code" : "city or town") + "</span></a>"; }).join("") || '<span class="dim" style="font-size:.8rem">Nothing found.</span>';
          res._items = d.results || [];
        }).catch(function () {});
      }, 250);
    });
    res.addEventListener("click", function (e) {
      var a = e.target.closest(".areaf-pick"); if (!a) return; e.preventDefault();
      var p = res._items[parseInt(a.getAttribute("data-i"), 10)]; if (!p) return;
      if (!areas.some(function (x) { return x.label === p.label; })) areas.push({kind: p.kind, label: p.label, lat: p.lat, lon: p.lon});
      q.value = ""; res.innerHTML = ""; if (p.kind === "county") loadShapes().then(changed); else changed();
    });
    draw();
    if (areas.some(function (a) { return a.kind === "county"; })) loadShapes().then(function () { if (o.onChange) o.onChange(); });
    function test(lat, lon) {
      if (!areas.length) return true;
      return areas.some(function (a) {
        if (a.kind === "county") {
          var s = (shapes || []).filter(function (c) { return (c.name + ", " + c.state) === a.label; })[0];
          return s ? inGeom(lat, lon, s.geometry) : km(lat, lon, a.lat, a.lon) <= 30;   // until the outline has loaded, near the county's center
        }
        return km(lat, lon, a.lat, a.lon) <= radius * 1.609344;
      });
    }
    return {test: test, active: function () { return areas.length > 0; }, areas: function () { return areas.slice(); }};
  }
  window.ArkArea = {attach: attach, inGeom: inGeom};
})();
