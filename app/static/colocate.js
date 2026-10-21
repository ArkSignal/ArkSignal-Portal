/* Stations at the same place on a map: an ADS-B feeder, a weather station and a balloon receiving station often stand at one address, and drawn on top of each other only the last one is
   visible. Colocate fans the markers of different kinds that share a position out around it (a few pixels, the same at every zoom), with a small anchor dot and thin lines at the true spot,
   so every one can be seen and clicked.
   Colocate.track(map, kind, marker, lat, lon): call it for each point marker (a Leaflet marker or circleMarker) when it is made; kind is any short name ("adsb", "weather", "sonde"...).
   Markers that are removed are forgotten by themselves; groups are worked out again when the map zooms or layers are added or removed. */
(function () {
  "use strict";
  var NEAR_M = 60, RADIUS_PX = 17, ORDER = ["adsb", "weather", "sonde"], SPREAD_SAME = {mesh: 1};   // kinds whose own markers also fan out when they share a spot (mesh nodes set up side by side)
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
        for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) (buckets[(cx + dx) + "," + (cy + dy)] || []).forEach(function (j) {
          if (j > i && metres(e, list[j]) <= NEAR_M) parent[find(j)] = find(i);
        });
      });
      var groups = {};
      list.forEach(function (e, i) { var r = find(i); (groups[r] = groups[r] || []).push(e); });
      Object.keys(groups).forEach(function (r) {
        var g = groups[r], kinds = [];
        g.forEach(function (e) { if (kinds.indexOf(e.kind) < 0) kinds.push(e.kind); });
        var same = kinds.length < 2;
        if (same && !(SPREAD_SAME[kinds[0]] && g.length >= 2)) return;
        kinds.sort(function (a, b) { var x = ORDER.indexOf(a), y = ORDER.indexOf(b); return (x < 0 ? 99 : x) - (y < 0 ? 99 : y) || (a < b ? -1 : 1); });
        var cLat = g.reduce(function (s, e) { return s + e.lat; }, 0) / g.length, cLon = g.reduce(function (s, e) { return s + e.lon; }, 0) / g.length;
        var c = map.project([cLat, cLon], zoom);
        L.circleMarker([cLat, cLon], {radius: 2.5, color: "#0b1422", weight: 1, fillColor: "#e8eef7", fillOpacity: 1, interactive: false, pane: "shadowPane"}).addTo(deco);
        g.forEach(function (e) {
          var gi = g.indexOf(e), n = same ? g.length : kinds.length, i = same ? gi : kinds.indexOf(e.kind), a = -Math.PI / 2 + i * 2 * Math.PI / n, R = same ? Math.max(RADIUS_PX, g.length * 5) : RADIUS_PX;
          var ll = map.unproject(c.add([R * Math.cos(a), R * Math.sin(a)]), zoom);
          e.layer.setLatLng(ll);
          L.polyline([[cLat, cLon], ll], {color: "#e8eef7", weight: 1.2, opacity: .7, interactive: false, pane: "shadowPane"}).addTo(deco);
        });
      });
    }
    deco.addTo(map);
    map.on("zoomend layeradd layerremove", function (e) { if (e && e.layer && e.layer.__colocateDeco) return; schedule(); });
    // our own decoration layers must not set off another refresh
    var addTo = deco.addLayer;
    deco.addLayer = function (l) { l.__colocateDeco = true; return addTo.call(this, l); };
    return {
      track: function (kind, layer, lat, lon) {
        if (!layer || typeof layer.setLatLng !== "function" || lat == null || lon == null) return layer;
        entries.push({kind: kind, ref: new WeakRef(layer), lat: +lat, lon: +lon});
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
    track: function (map, kind, layer, lat, lon) {
      if (!map || !window.WeakRef) return layer;
      if (!map._colocate) map._colocate = manager(map);
      return map._colocate.track(kind, layer, lat, lon);
    },
    of: function (map) { return map && map._colocate; }
  };
})();
