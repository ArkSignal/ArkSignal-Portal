/* The one label engine for the map pages: names next to icons that never sit on top of each other.
   var L2 = ArkLabels.create(map);          a layer on the map
   L2.begin();                              start a pass (every redraw and every zoom or pan)
   L2.add({key, lat, lon, html, color, priority, onClick, side: "right", gap, opacity, z, border: false})
                                            queue a label. html is what is drawn (its box is measured as drawn); a lower priority number wins a crowded spot; the label is above the icon by default,
                                            or beside it (side "right", gap pixels from the point), or centered on the point (side "center", for areas such as counties); key names the item so its label is kept from pass to pass instead of drawn again
   L2.flush() -> {shown, hidden}            place by priority: a label that would overlap one already placed is left off (its icon stays, it shows when zoomed in or panned clear),
                                            then bring the layer in line (labels no longer placed are removed, so a pass with nothing queued clears the layer)
   ArkLabels.merge(map, items, {w, h, zoom, alone: fn}) -> [[items]]   for readings that may be averaged: items whose boxes (w by h pixels at that zoom) would touch are gathered into one group, until no two boxes touch;
                                            alone(item) keeps an item out of every group. The caller draws one label per group (the weather map: the average and a count).
   The boxes are the real size of each label as drawn, in screen pixels, so the same rule holds at every zoom. Labels use the shared .lv-label look (size and background are the visitor's choices). */
(function () {
  "use strict";
  function create(map, opts) {
    opts = opts || {};
    var group = L.layerGroup().addTo(map), queue = [], pool = {}, padX = opts.padX == null ? 3 : opts.padX, padY = opts.padY == null ? 2 : opts.padY;
    var probe = null, sizes = {}, nsizes = 0;
    function lookKey() { var lv = document.getElementById("lv") || map.getContainer(); return (lv.getAttribute("class") || "") + "|" + (lv.style.getPropertyValue("--ls") || ""); }
    function sizeAll(items) {   // the real size of every label as drawn (font, scale and border included), measured together in one layout and remembered per text and look
      var look = lookKey(), need = [], seen = {}, lv = document.getElementById("lv") || map.getContainer(), i;
      items.forEach(function (it) { it._k = it.html + "|" + (it.border === false ? "n" : "b") + "|" + look; if (!sizes[it._k] && !seen[it._k]) { seen[it._k] = 1; need.push(it); } });
      if (!need.length) return;
      if (nsizes > 4000) { sizes = {}; nsizes = 0; }
      if (!probe) { probe = document.createElement("div"); probe.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;white-space:nowrap"; lv.appendChild(probe); }
      probe.innerHTML = need.map(function (it) { return '<div><span class="lv-label" style="' + (it.border === false ? "border:0" : "border:2px solid #888") + ';display:inline-block">' + it.html + "</span></div>"; }).join("");
      for (i = 0; i < need.length; i++) { var r = probe.children[i].firstChild.getBoundingClientRect(); sizes[need[i]._k] = [Math.max(10, r.width), Math.max(10, r.height)]; nsizes++; }
    }
    function begin() { queue = []; }
    function add(it) { queue.push(it); }
    function look(it, right, gap) {
      return '<span class="lv-label" style="' + (it.border === false ? "border:0;" : "border:2px solid " + (it.color || "#8a9bb3") + ";") +
        (right ? "position:absolute;left:" + gap + "px;top:0;transform:translateY(-50%);" : it.side === "center" ? "transform:translate(-50%,-50%);display:inline-block;" : "transform:translate(-50%,-165%);display:inline-block;") + (it.opacity ? "opacity:" + it.opacity + ";" : "") + '">' + it.html + "</span>";
    }
    function flush() {
      var placed = [], shown = 0, size = map.getSize(), i, keep = {}, near = [];
      queue.sort(function (a, b) { return (a.priority || 0) - (b.priority || 0); });
      for (i = 0; i < queue.length; i++) {
        var it = queue[i], p = map.latLngToContainerPoint([it.lat, it.lon]);
        if (p.x < -80 || p.y < -40 || p.x > size.x + 80 || p.y > size.y + 40) continue;   // off screen: nothing to place
        it._p = p; near.push(it);
      }
      sizeAll(near);
      near.forEach(function (it) {
        var p = it._p, wh = sizes[it._k], w = wh[0], h = wh[1], gap = it.gap || 0, right = it.side === "right", clash = false, j,
            mid = it.side === "center",
            b = right ? [p.x + gap, p.y - h / 2, p.x + gap + w, p.y + h / 2] : mid ? [p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2] : [p.x - w / 2, p.y - 1.65 * h, p.x + w / 2, p.y - 0.65 * h];
        for (j = 0; j < placed.length; j++) { var o = placed[j]; if (b[0] < o[2] + padX && o[0] < b[2] + padX && b[1] < o[3] + padY && o[1] < b[3] + padY) { clash = true; break; } }
        if (clash) return;
        placed.push(b); shown++;
        var key = it.key != null ? String(it.key) : it.lat + "|" + it.lon + "|" + it.html, sig = it.html + "|" + (it.color || "") + "|" + (it.side || "") + "|" + gap + "|" + (it.opacity || 0) + "|" + (it.border === false ? 0 : 1), e = pool[key];
        keep[key] = 1;
        if (e) {
          e.it = it;   // the click always goes to the newest item
          if (e.lat !== it.lat || e.lon !== it.lon) { e.m.setLatLng([it.lat, it.lon]); e.lat = it.lat; e.lon = it.lon; }
          if (e.sig !== sig) { e.m.setIcon(L.divIcon({className: "", html: look(it, right, gap), iconSize: [0, 0]})); e.sig = sig; }
          return;
        }
        var m = L.marker([it.lat, it.lon], {interactive: !!it.onClick, keyboard: false, icon: L.divIcon({className: "", html: look(it, right, gap), iconSize: [0, 0]}), zIndexOffset: it.z || 0});
        e = pool[key] = {m: m, it: it, sig: sig, lat: it.lat, lon: it.lon};
        if (it.onClick) m.on("click", function (ev) { if (e.it.onClick) e.it.onClick(ev); });
        m.addTo(group);
      });
      Object.keys(pool).forEach(function (k) { if (!keep[k]) { group.removeLayer(pool[k].m); delete pool[k]; } });
      var total = queue.length; queue = [];
      return {shown: shown, hidden: total - shown};
    }
    return {begin: begin, add: add, flush: flush, group: group};
  }
  function merge(map, list, o) {
    var zoom = o.zoom != null ? o.zoom : map.getZoom(), groups = [], singles = [];
    list = list.slice().sort(function (a, b) { return a.lat - b.lat || a.lon - b.lon || String(a.id).localeCompare(String(b.id)); });   // the same stations always make the same groups, whatever order the server listed them in: labels do not shuffle between refreshes
    list.forEach(function (it) {
      if (o.alone && o.alone(it)) { singles.push([it]); return; }
      var p = map.project([it.lat, it.lon], zoom); groups.push({pts: [it], x: p.x, y: p.y});
    });
    var merged = true;
    while (merged) {
      merged = false;
      for (var i = 0; i < groups.length && !merged; i++) for (var j = i + 1; j < groups.length; j++) {
        var a = groups[i], b = groups[j];
        if (Math.abs(a.x - b.x) < o.w && Math.abs(a.y - b.y) < o.h) {
          var na = a.pts.length, nb = b.pts.length;
          a.x = (a.x * na + b.x * nb) / (na + nb); a.y = (a.y * na + b.y * nb) / (na + nb); a.pts = a.pts.concat(b.pts); groups.splice(j, 1); merged = true; break;
        }
      }
    }
    return groups.map(function (g) { return g.pts; }).concat(singles);
  }
  window.ArkLabels = {create: create, merge: merge};
})();
