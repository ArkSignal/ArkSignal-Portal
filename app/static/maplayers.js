/* The map layers of every ArkSignal map page, from one place: the base maps and overlays chosen in Admin > Map Layers, the Weather panel (a tile per weather layer with a live
   preview, a switch, an opacity slider, radar looping and automatic refresh). The live (aircraft) map and the Weather Map both call ArkLayers.init, so they behave the same and
   what a visitor switched on is remembered for both. Page-specific tiles (winds measured by aircraft, the weather stations) come in through ctx.extraHtml / onChange / onSync.
   ctx: map, $, esc, LAYERS, M ({tile, attr, wxRefresh}), closeOthers(), foldSync(), extraHtml(), onChange(event), onSync() */
(function () {
  "use strict";
  window.ArkLayers = {init: function (ctx) {
    var map = ctx.map, $ = ctx.$, esc = ctx.esc, LAYERS = ctx.LAYERS, M = ctx.M;
    // ---- map layers: base maps are pick-one, overlays are checkboxes; the catalog (tar1090's sources) is edited in Admin > Map Layers
    var baseStd = L.tileLayer(M.tile, {maxZoom: 19, attribution: M.attr});
    function yesterday() { return new Date(Date.now() - 86400000).toISOString().slice(0, 10); }
    var made = {}, timers = {}, active = {};
    map.createPane("lvoverlay"); map.getPane("lvoverlay").style.zIndex = 250;   // above the tinted base tiles, below markers
    var bases = LAYERS.filter(function (c) { return c.is_base; }), overlays = LAYERS.filter(function (c) { return !c.is_base; });
    function parseOpts(str) {
      var o = {label: [], colorby: null};
      var m = /(?:^|;)label=([^;]*)/.exec(str || ""); if (m) o.label = m[1].split(",").filter(Boolean);
      m = /colorby=([A-Za-z0-9_]+):(.*)$/.exec(str || "");
      if (m) o.colorby = {prop: m[1], rules: m[2].split(";").map(function (r) { var p = r.split("="); return {vals: p[0].split("|"), color: p[1]}; }).filter(function (r) { return r.color; })};
      return o;
    }
    var wxOp = {}; try { wxOp = JSON.parse(localStorage.getItem("lv.wxop") || "{}"); } catch (e) {}
    function wxOpacity(c) { return wxOp[c.slug] != null ? wxOp[c.slug] : c.opacity; }
    LAYERS.forEach(function (c) { if (wxOp[c.slug] != null) c.opacity = wxOp[c.slug]; });
    function makeLayer(c) {
      var common = {opacity: wxOpacity(c), minZoom: c.min_zoom, maxZoom: c.max_zoom, attribution: c.attribution};
      // a tile layer never disappears when the map zooms past the last zoom its server has: the tiles of that zoom are stretched (maxNativeZoom), up to the map's own limit of 19
      if (c.kind === "tile") return L.tileLayer(c.url.replace("{date}", yesterday()), Object.assign({subdomains: "abc", maxNativeZoom: c.max_zoom, pane: c.is_base ? "tilePane" : "lvoverlay"}, common, {maxZoom: Math.max(c.max_zoom, 19)}));
      if (c.kind === "wms") return L.tileLayer.wms(c.url, Object.assign({layers: c.options, format: "image/png", transparent: true, pane: "lvoverlay"}, common));
      return L.layerGroup();
    }
    // RainViewer: always shows the newest radar frame; Play loops the last two hours and the forecast
    var rain = {frames: [], layers: [], idx: -1, timer: null, host: "", c: null, group: null};
    function rainTile(path) { return L.tileLayer(rain.host + path + "/256/{z}/{x}/{y}/6/1_1.png", {opacity: 0, attribution: rain.c.attribution, maxNativeZoom: 7, maxZoom: rain.c.max_zoom, pane: "lvoverlay"}); }
    function rainShow(i) {
      rain.layers.forEach(function (ly, k) { if (ly) ly.setOpacity(k === i ? rain.c.opacity : 0); });
      rain.idx = i;
      var f = rain.frames[i];
      if (f) rainUi(null, new Date(f.time * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}) + (f.future ? " (forecast)" : ""));
    }
    function rainUi(play, time) {
      var pb = $("wx-play-rainviewer") || $("rain-play"), tm = $("wx-time-rainviewer") || $("rain-time");
      if (pb && play) pb.textContent = play;
      if (tm && time != null) tm.textContent = time;
    }
    function rainStop() { clearInterval(rain.timer); rain.timer = null; rainUi("Play", null); }
    function rainLive() {
      rainStop();
      var last = rain.frames.reduce(function (a, f, k) { return f.future ? a : k; }, rain.frames.length - 1);
      if (rain.layers.length && rain.layers[last]) rainShow(last);
    }
    async function loadRain(c, group) {
      try {
        var d = await (await fetch(c.url, {cache: "no-store"})).json(), r = d.radar || {};
        var frames = (r.past || []).map(function (f) { return {time: f.time, path: f.path, future: false}; }).concat((r.nowcast || []).map(function (f) { return {time: f.time, path: f.path, future: true}; }));
        if (!frames.length) return;
        var wasPlaying = !!rain.timer;
        rainStop();
        group.clearLayers();
        rain.c = c; rain.group = group; rain.host = d.host; rain.frames = frames;
        // only the newest real frame is loaded until someone presses Play
        var latest = frames.reduce(function (a, f, k) { return f.future ? a : k; }, frames.length - 1);
        rain.layers = frames.map(function () { return null; });
        rain.layers[latest] = rainTile(frames[latest].path).addTo(group);
        rainShow(latest);
        if (wasPlaying) rainPlay();
      } catch (e) { /* the radar simply stays empty */ }
    }
    function rainPlay() {
      if (!rain.frames.length) return;
      rain.frames.forEach(function (f, k) { if (!rain.layers[k]) rain.layers[k] = rainTile(f.path).addTo(rain.group); });
      rainUi("Pause", null);
      clearInterval(rain.timer);
      rain.timer = setInterval(function () { rainShow((rain.idx + 1) % rain.frames.length); }, 700);
    }
    // a feature of a data layer (alert, earthquake, river gauge) opens the page's own details card, like every other map item; a page without a card gets a popup
    function openLayerCard(p, ly, at) {
      var card = document.getElementById("lv-card"), lv = document.getElementById("lv");
      function row(k, v) { return v === "" || v == null ? "" : "<div>" + esc(k) + "</div><div>" + esc(v) + "</div>"; }
      function when(t) { return t ? new Date(t).toLocaleString() : ""; }
      var rows = (p.rows || []).map(function (r) { return row(r[0], r[2] === "t" ? when(r[1]) : r[1]); }).join("") + row("Severity", p.severity) + (p.stage != null ? row("Stage", p.stage + " " + (p.unit || "")) : "") + (p.mag != null ? row("Magnitude", Number(p.mag).toFixed(1)) : "") + (p.depth_km != null ? row("Depth", window.ArkUnits && ArkUnits.text && ArkUnits.text.dist ? ArkUnits.text.dist(p.depth_km) : p.depth_km + " km") : "") +
        row(p.ends ? "Until" : "Reported", when(p.ends || p.time)) + row("Area", p.area) + row("State", p.state) + row("Issued by", p.sender);
      var body = '<div class="k">' + rows + "</div>" + (p.description ? "<div class='lv-alertdesc'>" + esc(p.description) + "</div>" : "") + (p.instruction ? "<div class='lv-alertdesc'><b>What to do:</b> " + esc(p.instruction) + "</div>" : "");
      var linkText = p.link ? (p.linkText || (/usgs/.test(p.link) ? "USGS Page" : "NOAA Page")) : "";
      if (!card || !lv) {
        ly.bindPopup("<div class='lv-alertpop'><b>" + esc(p.event) + "</b><br>" + esc(p.headline) + body + (p.link ? "<br><a href='" + esc(p.link) + "' target='_blank' rel='noopener'>" + linkText + "</a>" : "") + "</div>", {maxWidth: 320}).openPopup(at);
        return;
      }
      document.getElementById("c-name").textContent = p.event;
      document.getElementById("c-sub").textContent = p.headline;
      var pg = document.getElementById("c-page");
      if (pg) { if (p.link) { pg.href = p.link; pg.target = "_blank"; pg.rel = "noopener"; pg.textContent = linkText; pg.style.display = ""; } else pg.style.display = "none"; }
      document.getElementById("c-body").innerHTML = body;
      card.classList.remove("min"); var cm = document.getElementById("c-min"); if (cm) { cm.textContent = "Collapse"; cm.setAttribute("aria-expanded", "true"); }
      card.style.display = "block"; lv.classList.add("has-card");
      var fol = document.getElementById("c-follow");
      if (fol) { fol.style.display = ""; fol.textContent = "Zoom To"; fol.onclick = function () { if (ly.getBounds) map.fitBounds(ly.getBounds(), {maxZoom: 10}); else map.setView(ly.getLatLng(), Math.max(map.getZoom(), 10)); }; }
      document.getElementById("c-close").onclick = function () { card.style.display = "none"; lv.classList.remove("has-card"); };
    }
    function gaugeIcon(col, size) {
      var w = Math.round(size * 2.6), h = Math.round(w * 1.25);
      return L.divIcon({className: "", iconSize: [w, h], iconAnchor: [w / 2, h - 2], html: '<svg width="' + w + '" height="' + h + '" viewBox="0 0 24 30" style="filter:drop-shadow(0 1px 2px rgba(0,0,0,.7))"><path d="M12 1 C12 1 3 12 3 19 a9 9 0 0 0 18 0 C21 12 12 1 12 1 Z" fill="' + col + '" stroke="#fff" stroke-width="2"/><path d="M7 19 q2.5 -2.5 5 0 t5 0" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>'});
    }
    // the legend of the data layers that are switched on: the colors come from the layer's own settings, the words from the kind
    var LEGEND_TEXT = {
      "wx-alerts": ["Weather alerts", {TOR: "Tornado", SVR: "Severe storm", FLD: "Flood", WIN: "Winter weather", HOT: "Heat", FIR: "Fire weather", WND: "Wind, fog or dust", OTH: "Other"}, "area"],
      quakes: ["Earthquakes", {M1: "Under magnitude 2", M2: "Magnitude 2 to 3", M3: "Magnitude 3 to 4", M4: "Magnitude 4 and up"}, "dot"],
      rivers: ["River gauges", {OK: "No flooding", ACT: "Action stage", MIN: "Minor flooding", MOD: "Moderate flooding", MAJ: "Major flooding"}, "drop"]
    };
    function dataLegend() {
      var h = "";
      LAYERS.forEach(function (c) {
        var spec = LEGEND_TEXT[c.slug]; if (!spec || !active[c.slug]) return;
        var o = parseOpts(c.options); if (!o.colorby) return;
        h += "<h6>" + spec[0] + "</h6>";
        o.colorby.rules.forEach(function (r) {
          var col = r.color, sym = spec[2] === "drop" ? '<svg width="14" height="17" viewBox="0 0 24 30"><path d="M12 1 C12 1 3 12 3 19 a9 9 0 0 0 18 0 C21 12 12 1 12 1 Z" fill="' + col + '" stroke="#fff" stroke-width="2"/></svg>'
            : spec[2] === "dot" ? '<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:' + col + ';border:2px solid #fff;box-sizing:border-box"></span>'
            : '<span style="display:inline-block;width:14px;height:10px;border-radius:2px;background:' + col + ';opacity:.8;border:1px solid ' + col + '"></span>';
          h += '<div class="lg">' + sym + "<span>" + esc(spec[1][r.vals[0]] || r.vals[0]) + "</span></div>";
        });
        if (spec[2] === "dot") h += '<div class="dim" style="font-size:.74rem">A bigger circle is a stronger earthquake.</div>';
        if (spec[2] === "drop") h += '<div class="dim" style="font-size:.74rem">A river or lake gauge: click one for its stage and flow.</div>';
      });
      return h;
    }
    async function loadShapes(c, group) {
      var o = parseOpts(c.options);
      try {
        var gj = await (await fetch(c.url)).json();
        if (!active[c.slug]) return;
        group.clearLayers();
        L.geoJSON(gj, {
          pointToLayer: function (f, ll) {
            var p = f.properties || {}, col = c.color;
            if (o.colorby) { var rule = o.colorby.rules.filter(function (r) { return r.vals.indexOf(String(p[o.colorby.prop])) >= 0; })[0]; if (rule) col = rule.color; }
            if (p.icon === "gauge") return L.marker(ll, {icon: gaugeIcon(col, p.size || 8), keyboard: false});   // a water drop with a wave: a river gauge is a thing, not just a dot
            return L.circleMarker(ll, {radius: p.size ? p.size : p.mag != null ? Math.max(6, 4 + p.mag * 2.6) : 7, color: "#ffffff", weight: 2, fillColor: col, fillOpacity: Math.max(.8, c.opacity), opacity: 1});   // a white ring so a dot stands out on any map
          },
          style: function (f) {
            var col = c.color, p = f.properties || {};
            if (o.colorby) {
              var rule = o.colorby.rules.filter(function (r) { return r.vals.indexOf(String(p[o.colorby.prop])) >= 0; })[0];
              if (!rule) return {stroke: false, fill: false};
              col = rule.color;
            }
            return {color: col, weight: p.weight || 2, fillColor: col, fillOpacity: p.fillOpacity != null ? p.fillOpacity : c.opacity, opacity: p.lineOpacity || .9, dashArray: p.dash || null, interactive: !p.passive};
          },
          onEachFeature: function (f, ly) {
            var p = f.properties || {}, t = o.label.map(function (k) { return p[k]; }).filter(Boolean).join(" - ") || p.NAME || p.name || "";
            if (t) ly.bindTooltip(esc(t), {className: "lv-tip", sticky: true});
            if (p.headline && p.event) {
              ly.on("click", function (e) { L.DomEvent.stopPropagation(e); openLayerCard(p, ly, e.latlng); });   // the click is the card's: the map's own click (Drive Times picks a place) must not open a second popup over it
            }
          }
        }).addTo(group);
      } catch (e) { /* the shapes simply stay empty */ }
    }
    function refreshLayer(c) {
      var ly = made[c.slug];
      if (c.kind === "rainviewer") loadRain(c, ly);
      else if (c.kind === "geojson") loadShapes(c, ly);
    }
    function saveLayerState() { try { localStorage.setItem("lv.layers", JSON.stringify({base: curBase, on: Object.keys(active)})); } catch (e) {} }
    var saved = null; try { saved = JSON.parse(localStorage.getItem("lv.layers") || "null"); } catch (e) {}
    var curBase = saved && (saved.base === "std" || bases.some(function (c) { return c.slug === saved.base; })) ? saved.base : "std";

    function setBase(slug) {
      if (map._lvBase) map.removeLayer(map._lvBase);
      var c = bases.filter(function (x) { return x.slug === slug; })[0];
      var ly = c ? (made[slug] || (made[slug] = makeLayer(c))) : baseStd;
      ly.addTo(map); if (ly.bringToBack) ly.bringToBack();
      map._lvBase = ly; curBase = c ? slug : "std";
      // imagery whose server stops before the closest zooms (the USGS stops at 16) hands over to the worldwide imagery for the zooms beyond, so zooming in further keeps a picture
      if (map._lvHi) { map.removeLayer(map._lvHi); map._lvHi = null; }
      var hi = c && c.max_zoom < 19 && /imagery|sat/i.test(slug + " " + c.name) ? bases.filter(function (x) { return x.slug === "esri-satellite" && x.slug !== slug; })[0] : null;
      if (c && c.kind === "tile") ly.options.maxZoom = hi ? c.max_zoom : Math.max(c.max_zoom, 19);   // with a hand-over the imagery stops at its last zoom and the worldwide imagery takes over
      if (hi) { map._lvHi = L.tileLayer(hi.url, {minZoom: c.max_zoom + 1, maxZoom: 19, maxNativeZoom: Math.min(hi.max_zoom, 18), attribution: hi.attribution, pane: "tilePane"}).addTo(map); if (map._lvHi.bringToBack) map._lvHi.bringToBack(); }
      // the dark tint is for the standard street map; charts and imagery show as published
      document.querySelector(".leaflet-tile-pane").style.filter = c ? "none" : "";
      var st = document.getElementById("l-style"); if (st && !(st.value === "hyb" && c && slug === st.dataset.sat)) st.value = (c && slug === st.dataset.sat) ? "sat" : "std";
    }
    var hybOn = [];   // the overlays the Hybrid choice switched on (and so switches off again)
    function hybridOverlays(on) {
      var st = document.getElementById("l-style"), slugs = (st && st.dataset.hyb || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
      if (on) slugs.forEach(function (s) { var c = overlays.filter(function (x) { return x.slug === s; })[0]; if (c && !active[c.slug]) { setOverlay(c, true); hybOn.push(c.slug); } });
      else { hybOn.forEach(function (s) { var c = overlays.filter(function (x) { return x.slug === s; })[0]; if (c && active[c.slug]) setOverlay(c, false); }); hybOn = []; }
      var boxes = document.querySelectorAll("#lv-layers input.lv-ov"); Array.prototype.forEach.call(boxes, function (b) { b.checked = !!active[b.value]; });
    }
    var styleSel = document.getElementById("l-style");   // the quick Street / Satellite switch in Display and View
    if (styleSel) styleSel.addEventListener("change", function () {
      var want = styleSel.value, hasSat = bases.some(function (x) { return x.slug === styleSel.dataset.sat; });
      hybridOverlays(false);
      setBase((want === "sat" || want === "hyb") && hasSat ? styleSel.dataset.sat : "std");
      if (want === "hyb" && hasSat) { styleSel.value = "hyb"; hybridOverlays(true); }
      saveLayerState(); var r = document.querySelector('input[name=lv-base][value="' + curBase + '"]'); if (r) r.checked = true; });
    function setOverlay(c, on) {
      var ly = made[c.slug] || (made[c.slug] = makeLayer(c));
      if (on) {
        active[c.slug] = true; ly.addTo(map);
        if (c.kind === "rainviewer" || c.kind === "geojson") {
          refreshLayer(c);
          if (c.refresh_minutes > 0 && !c.wx_group) timers[c.slug] = setInterval(function () { refreshLayer(c); }, c.refresh_minutes * 60000);
        } else if (c.refresh_minutes > 0 && !c.wx_group) {
          timers[c.slug] = setInterval(function () {
            var stamp = Date.now();
            if (c.kind === "wms") ly.setParams({_: stamp});
            else ly.setUrl(c.url.replace("{date}", yesterday()) + (c.url.indexOf("?") < 0 ? "?" : "&") + "_=" + stamp);
          }, c.refresh_minutes * 60000);
        }
      } else {
        delete active[c.slug]; map.removeLayer(ly);
        clearInterval(timers[c.slug]);
        if (c.kind === "rainviewer") rainStop();
      }
    }
    function buildLayerPanel() {
      var groups = [];
      LAYERS.forEach(function (c) { if (groups.indexOf(c.grp) < 0) groups.push(c.grp); });
      var h = '<details><summary>Base Map</summary><div class="items"><label><input type="radio" name="lv-base" value="std"> Standard Map</label>';
      groups.forEach(function (g) {
        var bs = bases.filter(function (c) { return c.grp === g; });
        if (!bs.length) return;
        h += "<h5>" + esc(g) + "</h5>";
        bs.forEach(function (c) { h += '<label><input type="radio" name="lv-base" value="' + c.slug + '"> ' + esc(c.name) + "</label>"; });
      });
      h += "</div></details>";
      groups.forEach(function (g) {
        var os = overlays.filter(function (c) { return c.grp === g && !c.wx_group; });   // layers with a Weather heading live in the Weather panel
        if (!os.length) return;
        h += "<details><summary>" + esc(g) + " Overlays</summary><div class=\"items\">";
        os.forEach(function (c) {
          h += '<label><input type="checkbox" class="lv-ov" value="' + c.slug + '"> ' + esc(c.name) + "</label>";
          if (c.kind === "rainviewer") h += '<div id="rain-ctl" style="display:none;margin:2px 0 6px 24px"><button class="btn small neutral" type="button" id="rain-play" style="margin:0">Play</button> ' +
            '<button class="btn small neutral" type="button" id="rain-live" style="margin:0">Latest</button> <span id="rain-time" class="dim" style="font-size:.75rem"></span></div>';
        });
        h += "</div></details>";
      });
      $("lv-layers").innerHTML = h;
      var radio = document.querySelector('input[name=lv-base][value="' + curBase + '"]'); if (radio) radio.checked = true;
      document.querySelectorAll(".lv-ov").forEach(function (cb) { cb.checked = !!active[cb.value]; });
      rainControls();
    }
    function rainControls() {
      var ctl = $("rain-ctl"); if (!ctl) return;
      var rc = LAYERS.filter(function (c) { return c.kind === "rainviewer"; })[0];
      ctl.style.display = rc && active[rc.slug] ? "block" : "none";
    }
    document.addEventListener("click", function (e) {
      if (e.target.id === "rain-play") { rain.timer ? rainStop() : rainPlay(); }
      else if (e.target.id === "rain-live") rainLive();
    });
    function layerChange(e) {
      if (e.target.name === "lv-base") setBase(e.target.value);
      else if (e.target.classList.contains("lv-ov")) setOverlay(overlays.filter(function (c) { return c.slug === e.target.value; })[0], e.target.checked);
      saveLayerState(); rainControls();
      if (wxBuilt) wxSync();
      if (ctx.onLegend) ctx.onLegend();
    }
    $("lv-layers").addEventListener("change", layerChange);
    setBase(curBase);
    var startOn = saved ? saved.on : overlays.filter(function (c) { return c.default_on; }).map(function (c) { return c.slug; });
    overlays.forEach(function (c) { if (startOn.indexOf(c.slug) >= 0) setOverlay(c, true); });
    if (styleSel) { var hs = (styleSel.dataset.hyb || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean); if (hs.length && curBase === styleSel.dataset.sat && hs.every(function (s) { return active[s]; })) styleSel.value = "hyb"; }   // satellite with the Hybrid overlays already on
    buildLayerPanel();

    // ---- Weather panel: a tile per weather layer (preview, description, switch, opacity), radar looping for NEXRAD,
    // and winds measured by the aircraft themselves (our own take: no outside wind service needed)
    var wxBuilt = false, wxThumbs = {}, wxLoops = {};
    var WXLAYERS = overlays.filter(function (c) { return c.wx_group; });
    // keeping the weather current: while the switch is on, every active weather layer is refreshed on its own schedule
    // (the layer's refresh time, else the site's Weather Update Interval) and says when it was last updated
    var wxAuto = localStorage.getItem("lv.wxauto") !== "0", wxLast = {};
    function wxStamp(c) { wxLast[c.slug] = Date.now(); var el = document.getElementById("wx-upd-" + c.slug); if (el) el.textContent = "Updated " + window.fmtTime(new Date(wxLast[c.slug]), "ts") + (wxAuto ? " · updates itself" : ""); }
    function wxRefresh(c) {
      var ly = made[c.slug]; if (!ly || !active[c.slug]) return;
      var L2 = wxLoops[c.slug]; if (L2 && L2.timer) return;   // a loop is playing: leave it alone
      var stamp = Date.now();
      if (c.kind === "wms") ly.setParams({_: stamp});
      else if (c.kind === "rainviewer") { if (rain.timer) return; loadRain(c, ly); }
      else if (c.kind === "tile") ly.setUrl(c.url.replace("{date}", yesterday()) + (c.url.indexOf("?") < 0 ? "?" : "&") + "_=" + stamp);
      else if (c.kind === "geojson") refreshLayer(c);   // alerts, earthquakes and gauges: ask for the data again
      wxStamp(c);
    }
    setInterval(function () {
      if (!wxAuto) return;
      WXLAYERS.forEach(function (c) {
        if (!active[c.slug]) return;
        var every = (c.refresh_minutes > 0 ? c.refresh_minutes : M.wxRefresh) * 60000;
        if (Date.now() - (wxLast[c.slug] || 0) >= every) wxRefresh(c);
      });
    }, 15000);
    var IEM = "nexrad-n0q-900913";
    function isLoopable(c) { return c.kind === "tile" && c.url.indexOf(IEM) >= 0; }
    function loopFrames(c) {   // IEM keeps the last 50 minutes in 5 minute steps
      var f = []; for (var m = 50; m >= 5; m -= 5) f.push({url: c.url.replace(IEM, IEM + "-m" + (m < 10 ? "0" : "") + m + "m"), ago: m}); f.push({url: c.url, ago: 0}); return f;
    }
    function loopShow(c, i) {
      var L2 = wxLoops[c.slug]; if (!L2) return;
      L2.layers.forEach(function (ly, k) { ly.setOpacity(k === i ? wxOpacity(c) : 0); });
      L2.idx = i;
      var el = document.getElementById("wx-time-" + c.slug); if (el) el.textContent = L2.frames[i].ago ? L2.frames[i].ago + " min ago" : "Now";
    }
    function loopStop(c, keepFrame) {
      var L2 = wxLoops[c.slug]; if (!L2) return;
      clearInterval(L2.timer); L2.timer = null;
      var b = document.getElementById("wx-play-" + c.slug); if (b) b.textContent = "Play";
      L2.layers.forEach(function (ly) { map.removeLayer(ly); }); L2.layers = [];
      if (made[c.slug] && active[c.slug]) made[c.slug].setOpacity(wxOpacity(c));
      var el = document.getElementById("wx-time-" + c.slug); if (el) el.textContent = "";
    }
    function loopPlay(c) {
      var L2 = wxLoops[c.slug] = wxLoops[c.slug] || {layers: [], idx: 0, timer: null, frames: loopFrames(c)};
      if (L2.timer) { loopStop(c); return; }
      L2.frames = loopFrames(c);
      L2.layers = L2.frames.map(function (fr) { return L.tileLayer(fr.url, {opacity: 0, maxNativeZoom: c.max_zoom, maxZoom: 19, attribution: c.attribution, pane: "lvoverlay"}).addTo(map); });
      if (made[c.slug]) made[c.slug].setOpacity(0);
      document.getElementById("wx-play-" + c.slug).textContent = "Pause";
      loopShow(c, 0);
      L2.timer = setInterval(function () { loopShow(c, (L2.idx + 1) % L2.frames.length); }, 700);
    }
    function wxThumb(c, el) {
      var m = L.map(el, {zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false, zoomSnap: 0.25});
      m.setView([34.8, -92.3], 6);
      L.tileLayer(M.tile, {maxZoom: 19}).addTo(m);
      var op = Math.max(0.6, c.opacity);
      if (c.kind === "rainviewer") {
        fetch(c.url).then(function (r) { return r.json(); }).then(function (d) {
          var past = (d.radar && d.radar.past) || [], f = past[past.length - 1]; if (!f) return;
          L.tileLayer(d.host + f.path + "/256/{z}/{x}/{y}/6/1_1.png", {opacity: op, maxNativeZoom: 7}).addTo(m);
        }).catch(function () {});
      } else if (c.kind === "wms") L.tileLayer.wms(c.url, {layers: c.options, format: "image/png", transparent: true, opacity: op}).addTo(m);
      else if (c.kind === "tile") L.tileLayer(c.url.replace("{date}", yesterday()), {subdomains: "abc", opacity: op, maxNativeZoom: Math.min(c.max_zoom, 7)}).addTo(m);
      wxThumbs[c.slug] = m;
    }
    function wxSync() {
      document.querySelectorAll("#lv-wx .wx-tile[data-slug]").forEach(function (t) {
        var on = !!active[t.dataset.slug]; t.classList.toggle("on", on);
        if (on && !wxLast[t.dataset.slug]) { var c0 = WXLAYERS.filter(function (x) { return x.slug === t.dataset.slug; })[0]; if (c0) wxStamp(c0); }
        var cb = t.querySelector(".lv-ov"); if (cb) cb.checked = on;
        if (!on) { var c = WXLAYERS.filter(function (x) { return x.slug === t.dataset.slug; })[0]; if (c && wxLoops[c.slug] && wxLoops[c.slug].timer) loopStop(c); }
      });
      if (ctx.onSync) ctx.onSync();
    }
    function wxBuild() {
      var groups = []; WXLAYERS.forEach(function (c) { if (groups.indexOf(c.wx_group) < 0) groups.push(c.wx_group); });
      var h = '<div class="wx-head"><label class="wx-chk"><input type="checkbox" id="wx-auto"' + (wxAuto ? " checked" : "") + '> Update automatically</label><button class="btn small neutral" type="button" id="wx-now">Update Now</button></div>';
      groups.forEach(function (g) {
        h += "<h4>" + esc(g) + "</h4>";
        WXLAYERS.filter(function (c) { return c.wx_group === g; }).forEach(function (c) {
          h += '<div class="wx-tile" data-slug="' + c.slug + '"><div class="wx-thumb" id="wx-th-' + c.slug + '"></div><div class="wx-info"><b>' + esc(c.name) + "</b><span>" + esc(c.description || "") + '</span><span class="wx-upd" id="wx-upd-' + c.slug + '"></span></div>' +
            '<label class="wx-sw"><input type="checkbox" class="lv-ov" value="' + c.slug + '" aria-label="' + esc(c.name) + '"><i></i></label>' +
            '<div class="wx-ctl">' + (c.kind === "geojson" ? "" : '<input type="range" class="wx-op" data-slug="' + c.slug + '" min="10" max="100" value="' + Math.round(wxOpacity(c) * 100) + '" aria-label="Opacity">') +
            (isLoopable(c) || c.kind === "rainviewer" ? '<button class="btn small neutral wx-play" type="button" data-slug="' + c.slug + '" id="wx-play-' + c.slug + '">Play</button><span class="wx-time" id="wx-time-' + c.slug + '"></span>' : "") + "</div></div>";
        });
      });
      if (ctx.extraHtml) h += ctx.extraHtml();
      $("lv-wx").innerHTML = h;
      wxBuilt = true;
    if (ctx.afterBuild) ctx.afterBuild();
      WXLAYERS.forEach(function (c) { var el = $("wx-th-" + c.slug); if (el) wxThumb(c, el); });
      wxSync();
    }
    function wxOpen(open) {
      if (open && ctx.closeOthers) ctx.closeOthers();   // the panels share the left column: one at a time
      $("lv-wx").style.display = open ? "block" : "none";
      $("lv-wxbtn").textContent = open ? "Hide Weather" : "Weather"; if (ctx.foldSync) ctx.foldSync();
      if (open) { if (!wxBuilt) wxBuild(); else { wxSync(); Object.keys(wxThumbs).forEach(function (k) { wxThumbs[k].invalidateSize(); }); } }
    }
    $("lv-wx").addEventListener("change", function (e) {
      if (e.target.id !== "wx-auto") return;
      wxAuto = e.target.checked; try { localStorage.setItem("lv.wxauto", wxAuto ? "1" : "0"); } catch (err) {}
      WXLAYERS.forEach(function (c) { if (active[c.slug]) wxStamp(c); });
      if (wxAuto) WXLAYERS.forEach(function (c) { wxLast[c.slug] = 0; });   // catch up at the next tick
    });
    $("lv-wx").addEventListener("click", function (e) { if (e.target.id === "wx-now") WXLAYERS.forEach(function (c) { if (active[c.slug]) wxRefresh(c); }); });
    $("lv-wxbtn").addEventListener("click", function () { wxOpen($("lv-wx").style.display !== "block"); });
    $("lv-wx").addEventListener("change", function (e) {
      if (ctx.onChange && ctx.onChange(e)) return;
      layerChange(e);
    });
    $("lv-wx").addEventListener("input", function (e) {
      if (!e.target.classList.contains("wx-op")) return;
      var c = WXLAYERS.filter(function (x) { return x.slug === e.target.dataset.slug; })[0]; if (!c) return;
      var v = Math.max(0.1, Math.min(1, e.target.value / 100)); c.opacity = v; wxOp[c.slug] = v;
      try { localStorage.setItem("lv.wxop", JSON.stringify(wxOp)); } catch (err) {}
      var ly = made[c.slug], L2 = wxLoops[c.slug];
      if (L2 && L2.timer) loopShow(c, L2.idx); else if (c.kind === "rainviewer") rainShow(rain.idx < 0 ? 0 : rain.idx); else if (ly && ly.setOpacity) ly.setOpacity(v);
    });
    $("lv-wx").addEventListener("click", function (e) {
      var b = e.target.closest(".wx-play"); if (!b) return;
      var c = WXLAYERS.filter(function (x) { return x.slug === b.dataset.slug; })[0]; if (!c) return;
      if (c.kind === "rainviewer") { rain.timer ? rainStop() : rainPlay(); b.textContent = rain.timer ? "Pause" : "Play"; } else loopPlay(c);
    });


    return {openCard: openLayerCard, dataLegend: dataLegend, wxOpen: wxOpen, built: function () { return wxBuilt; }, sync: function () { if (wxBuilt) wxSync(); }, active: active, layerChange: layerChange, thumbs: wxThumbs};
  }};
})();
