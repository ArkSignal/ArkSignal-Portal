/* County outlines on any map: a thin line around every county, switched with the "County outlines" box under Display and View (remembered in this browser). The outlines are the same file the
   County Map uses (/api/counties/shapes.json, US Census Bureau), fetched only when the box is first ticked and drawn so that nothing under them stops working (they do not catch clicks).
   Public: ArkCounties.init({map, box}). */
(function () {
  "use strict";
  var KEY = "lv.countyoutline";
  function init(o) {
    var map = o.map, box = o.box; if (!box) return;
    var group = null, loading = false, bad = false;
    function say(t) { var n = document.getElementById("l-county-note"); if (n) n.textContent = t || ""; }
    function draw(rows) {
      group = L.layerGroup();
      rows.forEach(function (c) { L.geoJSON({type: "Feature", geometry: c.geometry}, {interactive: false, style: {color: "#d8e6ff", weight: 1.1, opacity: .7, fill: false}}).addTo(group); });
      if (box.checked) group.addTo(map);
    }
    function apply() {
      try { localStorage.setItem(KEY, box.checked ? "1" : "0"); } catch (e) {}
      if (!box.checked) { if (group) map.removeLayer(group); say(""); return; }
      if (group) { group.addTo(map); say(""); return; }
      if (loading || bad) return;
      loading = true; say("Loading the county lines...");
      fetch("/api/counties/shapes.json").then(function (r) { return r.json(); }).then(function (d) {
        loading = false; var rows = d.counties || [];
        if (!rows.length) { bad = true; say("No county outlines are loaded yet. An administrator loads them under Data Updates."); return; }
        say(""); draw(rows);
      }).catch(function () { loading = false; say("The county lines could not be loaded."); });
    }
    box.addEventListener("change", apply);
    try { if (localStorage.getItem(KEY) === "1") { box.checked = true; apply(); } } catch (e) {}
    window.arkCountyOutline = {on: function () { return box.checked; }, loaded: function () { return !!group; }};
  }
  window.ArkCounties = {init: init};
})();
