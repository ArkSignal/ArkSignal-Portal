/* Stations at the same place on a map: an ADS-B feeder, a weather station and a balloon receiving station often stand at one address, and drawn on top of each other only the last one is
   visible. Colocate fans the markers of different kinds that share a position out around it (a few pixels, the same at every zoom), with a small anchor dot and thin lines at the true spot,
   so every one can be seen and clicked.
   Colocate.track(map, kind, marker, lat, lon): call it for each point marker (a Leaflet marker or circleMarker) when it is made; kind is any short name ("adsb", "weather", "sonde"...).
   Markers that are removed are forgotten by themselves; groups are worked out again when the map zooms or layers are added or removed. */
(function () {
  "use strict";
  var NEAR_M = 60, RADIUS_PX = 17, SPREAD_ZOOM = 12, OVERLAP_PX = 24, ORDER = ["adsb", "weather", "sonde"], SPREAD_SAME = {mesh: 1};   // kinds whose own markers also fan out when they share a spot (mesh nodes set up side by side)
  function metres(a, b) {
    var dl = (b.lat - a.lat) * 111320, dn = (b.lon - a.lon) * 111320 * Math.cos(a.lat * Math.PI / 180);
    return Math.sqrt(dl * dl + dn * dn);
  }
  function manager(map) {
    var entries = [], timer = null, deco = L.layerGroup(), on = true;
    function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 30); }
    function live() {
      var out = [];
      entries = entries.filter(function (e) { var l = e.ref.deref(); if (!l) return false; e.layer = l; return true; });
      entries.forEach(function (e) { if (e.layer._map === map) out.push(e); });
      return out;
    }
    function refresh() {
      deco.clearLayers();
      var list = live(), zoom = map.getZoom();
      // restore everything to its true place first
      list.forEach(function (e) { e.layer.setLatLng([e.lat, e.lon]); });
      if (!on) return;
      // groups of markers within NEAR_M of each other (a bucket grid keeps this quick with many stations)
      var cell = 0.002, buckets = {}, parent = list.map(function (_e, i) { return i; });
      function find(i) { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; }
      list.forEach(function (e, i) { var k = Math.floor(e.lat / cell) + "," + Math.floor(e.lon / cell); (buckets[k] = buckets[k] || []).push(i); });
      list.forEach(function (e, i) {
        var cx = Math.floor(e.lat / cell), cy = Math.floor(e.lon / cell);
        var near = Math.min(NEAR_M, OVERLAP_PX * 156543.03 * Math.cos(e.lat * Math.PI / 180) / Math.pow(2, zoom));   // only markers that really overlap on the screen: zoomed far in, nodes a few meters apart are separate on their own
        for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) (buckets[(cx + dx) + "," + (cy + dy)] || []).forEach(function (j) {
          if (j > i && metres(e, list[j]) <= near) parent[find(j)] = find(i);
        });
      });
      var groups = {};
      list.forEach(function (e, i) { var r = find(i); (groups[r] = groups[r] || []).push(e); });
      Object.keys(groups).forEach(function (r) {
        var g = groups[r], kinds = [];
        g.forEach(function (e) { if (kinds.indexOf(e.kind) < 0) kinds.push(e.kind); });
        var same = kinds.length < 2;
        if (same && !(SPREAD_SAME[kinds[0]] && g.length >= 2)) return;
        if (same && zoom < SPREAD_ZOOM) {   // zoomed out, a crowd at one spot is one dot with a count (a ring of them would cover the map); zooming in or clicking the count spreads them out
          var bLat = g.reduce(function (t, e) { return t + e.lat; }, 0) / g.length, bLon = g.reduce(function (t, e) { return t + e.lon; }, 0) / g.length;
          var badge = L.marker([bLat, bLon], {icon: L.divIcon({className: "", html: '<div style="min-width:20px;height:20px;padding:0 5px;border-radius:10px;background:#0b1422;color:#e8eef7;border:2px solid #e8eef7;font:700 11px/16px Inter,sans-serif;text-align:center;box-sizing:border-box;cursor:pointer" title="' + g.length + ' nodes at this spot: click to spread them out">' + g.length + "</div>", iconSize: [0, 0]}), zIndexOffset: 400, pane: "markerPane"});
          badge.on("click", function () { map.setView([bLat, bLon], Math.max(SPREAD_ZOOM + 1, map.getZoom())); });
          badge.addTo(deco);
          return;
        }
        kinds.sort(function (a, b) { var x = ORDER.indexOf(a), y = ORDER.indexOf(b); return (x < 0 ? 99 : x) - (y < 0 ? 99 : y) || (a < b ? -1 : 1); });
        var cLat = g.reduce(function (s, e) { return s + e.lat; }, 0) / g.length, cLon = g.reduce(function (s, e) { return s + e.lon; }, 0) / g.length;
        var c = map.project([cLat, cLon], zoom);
        L.circleMarker([cLat, cLon], {radius: 2.5, color: "#0b1422", weight: 1, fillColor: "#e8eef7", fillOpacity: 1, interactive: false, pane: "shadowPane"}).addTo(deco);
        var placed = [];
        g.forEach(function (e) {
          var gi = g.indexOf(e), n = same ? g.length : kinds.length, i = same ? gi : kinds.indexOf(e.kind), a = -Math.PI / 2 + i * 2 * Math.PI / n, R = same ? RADIUS_PX : RADIUS_PX;
          if (same) {   // rings: 7 on the first, 14 on the next, 21 on the third and so on, so a big crowd is a tidy disc and not one huge circle
            var ring = 1, left = gi, cap = 7;
            while (left >= cap) { left -= cap; ring++; cap += 7; }
            a = -Math.PI / 2 + left * 2 * Math.PI / cap + (ring % 2 ? 0 : Math.PI / cap);
            R = RADIUS_PX + (ring - 1) * 15;
          }
          var ll = map.unproject(c.add([R * Math.cos(a), R * Math.sin(a)]), zoom);
          e.layer.setLatLng(ll);
          placed.push({e: e, ll: ll});
          L.polyline([[cLat, cLon], ll], {color: "#e8eef7", weight: 1, opacity: same ? .3 : .7, interactive: false, pane: "shadowPane"}).addTo(deco);
        });
        if (same && placed.some(function (x) { return x.e.label; })) {   // the names go in a column beside the spread-out nodes, each on its own line with a thin leader, so they never overlap
          var ROW = 19, MAXL = 24, rings = Math.max.apply(null, placed.map(function (x) { return map.project(x.ll, zoom).distanceTo(c); })), colX = c.x + rings + 30;
          var byY = placed.slice().sort(function (p, q) { return map.project(p.ll, zoom).y - map.project(q.ll, zoom).y; }), shown = byY.slice(0, MAXL), top = c.y - (shown.length - 1) * ROW / 2;
          var esc = function (t) { return String(t).replace(/[&<>"]/g, function (ch) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[ch]; }); };
          shown.forEach(function (x, n) {
            var at = map.unproject(L.point(colX, top + n * ROW), zoom);
            L.polyline([x.ll, at], {color: "#e8eef7", weight: 1, opacity: .35, interactive: false, pane: "shadowPane"}).addTo(deco);
            L.marker(at, {icon: L.divIcon({className: "", html: '<span style="position:absolute;left:2px;top:-9px;white-space:nowrap;padding:1px 6px;border-radius:8px;background:rgba(11,20,34,.92);color:#e8eef7;font:600 11px/16px Inter,sans-serif;border:1px solid rgba(232,238,247,.35)">' + esc(x.e.label) + "</span>", iconSize: [0, 0]}), interactive: false, keyboard: false, zIndexOffset: 300}).addTo(deco);
          });
          if (byY.length > MAXL) L.marker(map.unproject(L.point(colX, top + shown.length * ROW), zoom), {icon: L.divIcon({className: "", html: '<span style="position:absolute;left:2px;top:-9px;white-space:nowrap;color:#e8eef7;font:600 11px Inter,sans-serif">+' + (byY.length - MAXL) + " more (click a dot)</span>", iconSize: [0, 0]}), interactive: false, keyboard: false}).addTo(deco);
        }
      });
    }
    deco.addTo(map);
    map.on("zoomend layeradd layerremove", function (e) { if (e && e.layer && e.layer.__colocateDeco) return; schedule(); });
    // our own decoration layers must not set off another refresh
    var addTo = deco.addLayer;
    deco.addLayer = function (l) { l.__colocateDeco = true; return addTo.call(this, l); };
    return {
      track: function (kind, layer, lat, lon, label) {
        if (!layer || typeof layer.setLatLng !== "function" || lat == null || lon == null) return layer;
        entries.push({kind: kind, ref: new WeakRef(layer), lat: +lat, lon: +lon, label: label || ""});
        schedule();
        return layer;
      },
      refresh: refresh,
      enable: function (v) { on = !!v; refresh(); },
      groups: function () {   // for tests: how many places have more than one kind, and what moved
        var list = live(); return list.filter(function (e) { var p = e.layer.getLatLng(); return Math.abs(p.lat - e.lat) > 1e-9 || Math.abs(p.lng - e.lon) > 1e-9; }).length;
      }
    };
  }
  window.Colocate = {
    track: function (map, kind, layer, lat, lon, label) {
      if (!map || !window.WeakRef) return layer;
      if (!map._colocate) map._colocate = manager(map);
      return map._colocate.track(kind, layer, lat, lon, label);
    },
    of: function (map) { return map && map._colocate; }
  };
})();
