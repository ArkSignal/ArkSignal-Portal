/* ArkSignal weather stations on a Leaflet map. One component, used by the live (aircraft) map, the Weather page and the full Weather Map, so a station looks and
   behaves the same everywhere: pick what the markers show (temperature, wind, gust, humidity, pressure, rain, UV...), wind arrows that point where the wind blows
   to, filters, a legend, and the visitor's choices remembered. Readings arrive in imperial units (the way stations send them) and are converted here. */
(function () {
  "use strict";
  var SCALE_T = [[32, "#7db7ff"], [50, "#7fe0d0"], [70, "#8be28a"], [85, "#ffd24f"], [95, "#ff9f43"], [1e9, "#ff5d5d"]];
  var SCALE_W = [[5, "#9fc5d9"], [10, "#7fe0d0"], [20, "#ffd24f"], [30, "#ff9f43"], [1e9, "#ff5d5d"]];
  var SCALE_R = [[0.005, "#8a9bb3"], [0.25, "#8fd3ff"], [1, "#4fb3ff"], [2, "#2f7cff"], [1e9, "#9b6dff"]];
  var METRICS = {
    temp: {label: "Temperature", f: "temp_f", kind: "temp", scale: SCALE_T},
    feels: {label: "Feels Like", f: "feels_f", kind: "temp", scale: SCALE_T},
    dew: {label: "Dew Point", f: "dew_f", kind: "temp", scale: [[32, "#c9b8ff"], [50, "#8fd3ff"], [60, "#7fe0d0"], [65, "#8be28a"], [70, "#ffd24f"], [1e9, "#ff9f43"]]},
    wind: {label: "Wind", f: "wind_mph", kind: "speed", scale: SCALE_W},
    gust: {label: "Gust", f: "gust_mph", kind: "speed", scale: SCALE_W},
    hum: {label: "Humidity", f: "hum", kind: "pct", scale: [[30, "#ffd24f"], [50, "#c8e06a"], [70, "#7fe0d0"], [90, "#6fb3ff"], [1e9, "#9b8cff"]]},
    press: {label: "Pressure", f: "press_in", kind: "press", scale: [[29.6, "#c79bff"], [29.9, "#8fb4ff"], [30.1, "#7fe0d0"], [1e9, "#ffd24f"]]},
    rain: {label: "Rain Today", f: "rain_day_in", kind: "rain", scale: SCALE_R},
    rate: {label: "Rain Rate", f: "rain_rate_in", kind: "rate", scale: [[0.005, "#8a9bb3"], [0.1, "#8fd3ff"], [0.3, "#4fb3ff"], [1, "#2f7cff"], [1e9, "#9b6dff"]]},
    solar: {label: "Solar Radiation", f: "solar", kind: "solar", scale: [[100, "#8a9bb3"], [400, "#ffe08a"], [800, "#ffc04f"], [1e9, "#ff9f43"]]},
    uv: {label: "UV Index", f: "uv", kind: "uv", scale: [[3, "#8be28a"], [6, "#ffd24f"], [8, "#ff9f43"], [11, "#ff5d5d"], [1e9, "#c79bff"]]},
    cloud: {label: "Cloud Cover", f: "cloud_pct", kind: "pct", scale: [[10, "#7db7ff"], [30, "#a9c3dd"], [60, "#b8bec7"], [85, "#9aa1ad"], [1e9, "#7b828f"]]}   // from airport weather (METAR); the sky is not measured by the other stations
  };
  var ORDER = ["temp", "feels", "dew", "wind", "gust", "hum", "press", "rain", "rate", "solar", "uv", "cloud"];
  var COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

  function compass(d) { return d == null ? "" : COMPASS[Math.floor(((d % 360) / 22.5) + 0.5) % 16]; }
  var ICONS = {temp: 1, drop: 1, dew: 1, press: 1, wind: 1, compass: 1, rain: 1, rate: 1, sun: 1, uv: 1, bolt: 1, cloud: 1, partly: 1, sunny: 1, moon: 1, storm: 1, fog: 1, windy: 1};
  // an icon from the sprite in partials/_wxicons.html (the page includes it); only known names are drawn
  function svgIcon(name, cls) { return ICONS[name] ? '<svg class="wxi ' + (cls || "") + '"><use href="#wxi-' + name + '"/></svg>' : ""; }
  function esc(t) { var d = document.createElement("div"); d.textContent = t == null ? "" : t; return d.innerHTML; }

  // [text, unit] for a stored (imperial) value
  function conv(kind, v, units) {
    if (v == null || isNaN(v)) return ["-", ""];
    var imp = units !== "metric";
    switch (kind) {
      case "temp": return imp ? [v.toFixed(0), "°F"] : [((v - 32) * 5 / 9).toFixed(0), "°C"];
      case "speed": return imp ? [v.toFixed(0), "mph"] : [(v * 1.609344).toFixed(0), "km/h"];
      case "pct": return [v.toFixed(0), "%"];
      case "press": return imp ? [v.toFixed(2), "inHg"] : [(v / 0.02953).toFixed(0), "hPa"];
      case "rain": return imp ? [v.toFixed(2), "in"] : [(v * 25.4).toFixed(1), "mm"];
      case "rate": return imp ? [v.toFixed(2), "in/h"] : [(v * 25.4).toFixed(1), "mm/h"];
      case "solar": return [v.toFixed(0), "W/m²"];
      case "uv": return [v.toFixed(1), ""];
    }
    return [String(v), ""];
  }
  // a number typed in the visitor's units back to the stored ones
  function toBase(kind, x, units) {
    if (units === "metric") {
      if (kind === "temp") return x * 9 / 5 + 32;
      if (kind === "speed") return x / 1.609344;
      if (kind === "press") return x * 0.02953;
      if (kind === "rain" || kind === "rate") return x / 25.4;
    }
    return x;
  }
  function colorFor(m, v) {
    if (v == null) return "#8a9bb3";
    for (var i = 0; i < m.scale.length; i++) if (v < m.scale[i][0]) return m.scale[i][1];
    return m.scale[m.scale.length - 1][1];
  }

  function injectStyle() {
    if (document.getElementById("wxmap-style")) return;
    var s = document.createElement("style"); s.id = "wxmap-style";
    s.textContent = ".wxl{position:absolute}.wxl .wxdot{position:absolute;left:-4px;top:-4px;width:8px;height:8px;border-radius:50%;background:var(--wc,#8a9bb3);border:1.5px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.5);pointer-events:none}.wxl .lv-label{position:absolute;left:0;top:0;transform:translate(7px,calc(-100% - 7px));display:block;text-align:left;border-left:calc(5px * var(--ls,1)) solid var(--wc,#8a9bb3);cursor:pointer;pointer-events:auto;background:rgba(10,18,32,.92);color:#fff}" +
      "#lv.lb-dark .wxl .lv-label{background:rgba(10,18,32,.92);color:#fff}#lv.lb-light .wxl .lv-label{background:rgba(255,255,255,.95);color:#10203a}#lv.lb-yellow .wxl .lv-label{background:#ffd54a;color:#14110a}#lv.lb-none .wxl .lv-label{background:transparent}" +
      ".wxl.off .lv-label{opacity:.55}.wxl.sel .lv-label{outline:2px solid #fff;z-index:2}.wxl .lv-label small{font-weight:600;opacity:.75;font-size:.78em;margin-left:1px}.wxl .lv-label .wxi{width:1.15em;height:1.15em;vertical-align:middle}" +
      ".wxl svg.wxr{width:calc(1.5em);height:calc(1.5em);flex:none;margin:-3px 0}.wxl .wxl1{display:flex;gap:calc(7px * var(--ls,1));align-items:center}.wxl .lv-label small{display:block;margin:0;font-size:1em;font-weight:700;opacity:1}.wxl .lv-label .wxid{font-size:.74em;font-weight:600;opacity:.8;letter-spacing:.03em}" +
      ".wxp{display:inline-block;font:700 .74rem/1 'JetBrains Mono',monospace;padding:3px 6px;border-radius:9px;border:1px solid rgba(0,0,0,.4);white-space:nowrap;color:#071018;box-shadow:0 1px 4px rgba(0,0,0,.5)}" +
      ".wxopts{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center}.wxopts label{display:flex;gap:6px;align-items:center;margin:0;font-size:.85rem}.wxfields{display:grid;grid-template-columns:1fr 1fr;gap:2px 10px;margin-top:4px}.wxfields label{margin:0;display:flex;gap:6px;align-items:center;font-size:.82rem;cursor:pointer}" +
      ".wxopts select,.wxopts input[type=number],.wxopts input[type=text]{width:auto;min-width:0;max-width:100%}.wxleg{display:flex;flex-direction:column;gap:3px;margin-top:6px}.wxleg div{display:flex;gap:8px;align-items:center;font-size:.8rem}";
    document.head.appendChild(s);
  }

  var PREF = "ark.wx.prefs";
  function loadPrefs() { try { return JSON.parse(localStorage.getItem(PREF) || "{}") || {}; } catch (e) { return {}; } }
  function savePrefs(st) { try { localStorage.setItem(PREF, JSON.stringify({metric: st.metric, arrows: st.arrows, online: st.online, extra: st.extra, hover: st.hover, combine: st.combine})); } catch (e) {} }

  function create(map, opts) {
    injectStyle();
    opts = opts || {};
    var units = opts.units === "metric" ? "metric" : "imperial";
    var st = {metric: "temp", arrows: true, online: false, min: "", max: "", q: "", extra: [], hover: true, combine: true};
    var d = opts.defaults || {}, p = loadPrefs();
    if (METRICS[d.metric]) st.metric = d.metric;
    if (d.arrows != null) st.arrows = !!d.arrows;
    if (METRICS[p.metric]) st.metric = p.metric;
    if (p.arrows != null) st.arrows = !!p.arrows;
    if (p.online != null) st.online = !!p.online;
    if (Array.isArray(p.extra)) st.extra = p.extra.filter(function (k) { return k === "id" || k === "cond" || METRICS[k]; });
    if (p.hover != null) st.hover = !!p.hover;
    if (p.combine != null) st.combine = !!p.combine;
    if (opts.initial) for (var k in opts.initial) if (st.hasOwnProperty(k) && opts.initial[k] != null) st[k] = opts.initial[k];
    var group = L.layerGroup(), points = [], markers = {}, selected = null, cl = {px: 90, max_zoom: 15}, loadedBox = null, moveTimer = 0, retries = 0, retryTimer = 0;   // cl: Settings > Weather Stations (combining overlapping stations when zoomed out)
    var api = {group: group, state: st, METRICS: METRICS, ORDER: ORDER, conv: conv, compass: compass, colorFor: colorFor};

    function shown(pt) {
      if (st.online && !pt.online) return false;
      var m = METRICS[st.metric], v = pt.r ? pt.r[m.f] : null;
      if (st.min !== "" && !isNaN(parseFloat(st.min)) && (v == null || v < toBase(m.kind, parseFloat(st.min), units))) return false;
      if (st.max !== "" && !isNaN(parseFloat(st.max)) && (v == null || v > toBase(m.kind, parseFloat(st.max), units))) return false;
      if (st.q) { var t = (pt.id + " " + pt.name + " " + (pt.owner || "")).toLowerCase(); if (t.indexOf(st.q.toLowerCase()) < 0) return false; }
      return true;
    }
    // a label like the aircraft labels (same pill, same size and background choices): a small wind rose with the arrow inside, then the value shown (colored edge) and anything else chosen
    function itemText(k, r) {
      var m = METRICS[k], v = r[m.f];
      if (v == null) return "--";
      var c = conv(m.kind, v, units);
      if (k === "wind") return c[0] + " " + c[1] + (r.wind_dir != null ? " " + compass(r.wind_dir) : "");
      if (m.kind === "temp") return c[0] + "°" + (k === "feels" ? " feels" : k === "dew" ? " dew" : "");
      if (k === "uv") return "UV " + c[0];
      if (k === "gust") return "gust " + c[0];
      return c[0] + (c[1] ? " " + c[1] : "");
    }
    function icon(pt) {
      var m = METRICS[st.metric], r = pt.r || {}, c = colorFor(m, r[m.f]), l1 = [];
      var arrow = (st.arrows || st.metric === "wind" || st.metric === "gust") && r.wind_dir != null && r.wind_mph != null;
      if (arrow) {
        var col = colorFor(METRICS.wind, r.wind_mph);
        l1.push('<svg class="wxr" viewBox="-12 -12 24 24"><circle r="10.4" fill="rgba(0,0,0,.28)" stroke="#8fa6c2" stroke-width="1.3"/><path d="M0,-10.4 L0,-7.6" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><g transform="rotate(' + ((r.wind_dir + 180) % 360) + ')"><path d="M0,-7.6 L3.6,5.6 L0,3.4 L-3.6,5.6 Z" fill="' + col + '" stroke="#071018" stroke-width=".9" stroke-linejoin="round"/></g></svg>');
      }
      if (st.extra.indexOf("cond") >= 0 && pt.cond && ICONS[pt.cond[0]]) l1.push(svgIcon(pt.cond[0], "cond-" + pt.cond[0]));
      var keys = [st.metric].concat(st.extra.filter(function (k) { return METRICS[k] && k !== st.metric; })), rest = keys.map(function (k) { return itemText(k, r); });
      // like an aircraft label: the first line names it (the station ID when chosen, else the value shown), the lines under it are small and hold the rest, three to a line so a label never gets long
      if (st.extra.indexOf("id") >= 0) l1.push('<b class="wxid">' + esc(pt.id) + "</b>"); else l1.push("<b>" + esc(rest.shift()) + "</b>");
      var rows = []; while (rest.length) rows.push("<small>" + rest.splice(0, 3).map(esc).join(" · ") + "</small>");
      if (pt.n) rows.unshift("<small>" + pt.n + (window.innerWidth < 700 ? " stations" : " stations averaged") + "</small>");   // a combined label (zoomed out)
      return L.divIcon({className: "wxl" + (pt.online ? "" : " off") + (selected === pt.id ? " sel" : ""), html: '<span class="wxdot" style="--wc:' + c + '"></span><span class="lv-label" style="--wc:' + c + '"><span class="wxl1">' + l1.join("") + "</span>" + rows.join("") + "</span>", iconSize: [0, 0], iconAnchor: [0, 0]});
    }
    function popup(pt) {
      var r = pt.r || {}, el = document.createElement("div"), b = document.createElement("b");
      if (pt.cond && ICONS[pt.cond[0]]) { var ci = document.createElement("span"); ci.innerHTML = svgIcon(pt.cond[0], "cond-" + pt.cond[0]) + " "; ci.title = pt.cond[1]; el.appendChild(ci); }
      b.textContent = pt.name; el.appendChild(b); if (pt.src === "aprs" && window.ArkCall) ArkCall.tag(b, pt.id);
      if (pt.approx) el.appendChild(document.createTextNode(" (approximate position)"));
      var lines = [];
      function add(label, key, kind, extra) { if (r[key] != null) { var c = conv(kind, r[key], units); lines.push(label + " " + c[0] + " " + c[1] + (extra || "")); } }
      add("Temperature", "temp_f", "temp"); if (r.feels_f != null && r.temp_f != null && Math.abs(r.feels_f - r.temp_f) >= 2) add("Feels like", "feels_f", "temp");
      add("Humidity", "hum", "pct"); add("Dew point", "dew_f", "temp");
      if (r.wind_mph != null) { var w = conv("speed", r.wind_mph, units); lines.push("Wind " + w[0] + " " + w[1] + " " + compass(r.wind_dir) + (r.gust_mph != null ? ", gusting " + conv("speed", r.gust_mph, units)[0] : "")); }
      add("Pressure", "press_in", "press"); add("Rain today", "rain_day_in", "rain"); if (r.rain_rate_in) add("Rain rate", "rain_rate_in", "rate");
      add("UV index", "uv", "uv"); add("Solar", "solar", "solar");
      lines.push(pt.online ? "Reporting now" : "Not reporting right now");
      lines.forEach(function (t) { el.appendChild(document.createElement("br")); el.appendChild(document.createTextNode(t)); });
      el.appendChild(document.createElement("br"));
      var a = document.createElement("a"); a.href = pt.href; a.textContent = "Weather page"; el.appendChild(a);
      if (opts.onDetails) { el.appendChild(document.createTextNode(" · ")); var b2 = document.createElement("a"); b2.href = "#"; b2.textContent = "Details"; b2.addEventListener("click", function (e) { e.preventDefault(); opts.onDetails(pt); }); el.appendChild(b2); }
      return el;
    }
    // Zoomed out, stations whose labels would sit on top of each other are drawn as one label: the average of their readings and a count. Clicking it zooms in until they separate.
    function avgPoint(list) {
      var r = {}, sums = {}, cnt = {}, sx = 0, sy = 0, wn = 0;
      list.forEach(function (pt) {
        var q = pt.r || {};
        Object.keys(q).forEach(function (k) { if (typeof q[k] === "number" && k !== "wind_dir") { sums[k] = (sums[k] || 0) + q[k]; cnt[k] = (cnt[k] || 0) + 1; } });
        if (q.wind_dir != null && q.wind_mph != null) { var a = q.wind_dir * Math.PI / 180, w = Math.max(q.wind_mph, 0.01); sx += Math.sin(a) * w; sy += Math.cos(a) * w; wn++; }
      });
      Object.keys(sums).forEach(function (k) { r[k] = sums[k] / cnt[k]; });
      if (wn) r.wind_dir = (Math.atan2(sx, sy) * 180 / Math.PI + 360) % 360;
      var lat = 0, lon = 0; list.forEach(function (pt) { lat += pt.lat; lon += pt.lon; });
      return {n: list.length, id: list.length + " stations", name: list.length + " stations", lat: lat / list.length, lon: lon / list.length, r: r, online: list.some(function (pt) { return pt.online; }), cond: null};
    }
    function clusters(list) {
      // only what is in view (and a margin) is drawn at all: a map of the whole region opens fast and fills in as it is moved
      var bounds = map.getBounds().pad(0.35);
      list = list.filter(function (pt) { return pt.id === selected || bounds.contains([pt.lat, pt.lon]); });
      var zoom = map.getZoom();
      if (!st.combine || !(cl.px > 0) || zoom >= cl.max_zoom) return list.map(function (pt) { return [pt]; });
      // a label is a box: stations whose boxes would overlap are drawn as one, until no two boxes touch
      var lv = document.getElementById("lv"), ls = lv ? (parseFloat(getComputedStyle(lv).getPropertyValue("--ls")) || 1) : 1;
      var rows = 1 + Math.ceil(st.extra.filter(function (k) { return k !== "id" && k !== "cond"; }).length / 3) * 0.45;
      return ArkLabels.merge(map, list, {w: cl.px * 1.9 * ls, h: cl.px * 0.5 * ls * rows, zoom: zoom, alone: function (pt) { return pt.id === selected; }});   // the shared label engine (static/labelfit.js)
    }
    function clusterMarker(list) {
      var pt = avgPoint(list), m = METRICS[st.metric], vals = list.map(function (x) { return (x.r || {})[m.f]; }).filter(function (v) { return v != null; });
      var ic = icon(pt), lo = vals.length ? Math.min.apply(null, vals) : null, hi = vals.length ? Math.max.apply(null, vals) : null;
      var mk = L.marker([pt.lat, pt.lon], {icon: ic, riseOnHover: true});
      var hv = conv(m.kind, (pt.r || {})[m.f], units), l1 = vals.length ? conv(m.kind, lo, units)[0] + " to " + conv(m.kind, hi, units)[0] + " " + conv(m.kind, hi, units)[1] : "";
      mk.bindTooltip("<b>" + list.length + " stations</b> averaging " + esc(hv[0] + " " + hv[1]) + (l1 ? "<br>" + esc(l1) : "") + "<br>Click to zoom in", {className: "lv-tip", direction: "top", offset: [0, -16]});
      mk.on("click", function () {
        var z = map.getZoom(); map.fitBounds(L.latLngBounds(list.map(function (x) { return [x.lat, x.lon]; })).pad(0.3), {maxZoom: Math.max(cl.max_zoom, z + 1)});
        if (map.getZoom() <= z) map.setZoom(z + 1);
      });
      return mk;
    }
    function render() {
      group.clearLayers(); markers = {};
      var n = 0;
      clusters(points.filter(shown)).forEach(function (list) {
        n += list.length;
        if (list.length > 1) { clusterMarker(list).addTo(group); return; }
        var pt = list[0];
        var mk = L.marker([pt.lat, pt.lon], {icon: icon(pt), riseOnHover: true}).bindPopup(popup(pt));
        mk.on("click", function () { api.select(pt.id, true); });
        if (st.hover) { var mm = METRICS[st.metric], hv = conv(mm.kind, (pt.r || {})[mm.f], units); mk.bindTooltip("<b>" + esc(pt.id) + "</b> " + esc(hv[0] + " " + hv[1]) + (pt.online ? "" : " (not reporting)"), {className: "lv-tip", direction: "top", offset: [0, -16]}); }
        mk.addTo(group); markers[pt.id] = mk;
        if (window.Colocate) Colocate.track(map, "weather", mk, pt.lat, pt.lon);   // fanned out when a feeder or balloon station stands at the same place
      });
      api.count = n;
      if (opts.onRender) opts.onRender(points, n);
    }
    map.on("zoomend moveend", function () {
      if (!group._map) return;
      render();
      if (opts.viewport && loadedBox && !loadedBox.contains(map.getBounds())) { clearTimeout(moveTimer); moveTimer = setTimeout(function () { api.load(); }, 400); }   // moved out of what was loaded: ask for the new view
    });
    api.render = render;
    api.points = function () { return points; };
    api.visible = function () { return points.filter(shown); };
    api.setUnits = function (u) { units = u === "metric" ? "metric" : "imperial"; render(); };
    api.units = function () { return units; };
    api.set = function (k, v, quiet) { st[k] = v; if (k === "metric" || k === "arrows" || k === "online" || k === "extra" || k === "hover" || k === "combine") savePrefs(st); render(); if (!quiet && opts.onChange) opts.onChange(st); };
    api.select = function (id, fromMap) {
      var old = selected; selected = id;
      if (id && !markers[id] && points.some(function (x) { return x.id === id; })) render();   // the chosen station stands out of any combined label
      [old, id].forEach(function (i) { var pt = points.filter(function (x) { return x.id === i; })[0]; if (pt && markers[i]) markers[i].setIcon(icon(pt)); });
      if (opts.onSelect) opts.onSelect(id, fromMap);
    };
    api.selected = function () { return selected; };
    api.open = function (id) { var mk = markers[id]; if (!mk && selected === id) { render(); mk = markers[id]; } if (mk) { map.setView(mk.getLatLng(), Math.max(map.getZoom(), 10)); mk.openPopup(); } };
    api.load = function () {
      var q = [];
      if (opts.sources) q.push("sources=" + encodeURIComponent(opts.sources()));
      if (opts.viewport) { loadedBox = map.getBounds().pad(0.5); q.push("bbox=" + [loadedBox.getSouth(), loadedBox.getWest(), loadedBox.getNorth(), loadedBox.getEast()].map(function (x) { return x.toFixed(2); }).join(",")); }   // only what is in view, and a margin
      return fetch("/api/weather.json" + (q.length ? "?" + q.join("&") : "")).then(function (r) { return r.json(); }).then(function (d) { points = d.stations || []; if (d.cluster) cl = d.cluster; if (!api._drawn || !(d.pending > 0) || retries >= 11) { render(); api._drawn = true; }   /* while airport weather is still arriving the first answer is drawn and the rest wait for the complete one: the labels do not merge and re-merge with every tile */ if (d.pending > 0 && ++retries < 12) { clearTimeout(retryTimer); retryTimer = setTimeout(function () { api.load(); }, 3000); } else if (!d.pending) retries = 0; return points; }).catch(function () {});
    };
    api.legend = function (key) {
      var m = METRICS[key || st.metric], rows = [], prev = null;
      m.scale.forEach(function (s, i) {
        var hi = s[0], label;
        if (i === 0) label = "below " + conv(m.kind, hi, units).join(" ");
        else if (hi >= 1e8) label = conv(m.kind, prev, units).join(" ") + " and up";
        else label = conv(m.kind, prev, units)[0] + " to " + conv(m.kind, hi, units).join(" ");
        rows.push('<div><span class="wxp" style="background:' + s[1] + ';padding:3px 14px">&nbsp;</span>' + esc(label) + "</div>");
        prev = hi;
      });
      var head = "<b>" + esc(m.label) + "</b>";
      if (key) return head + '<div class="wxleg">' + rows.join("") + "</div>";
      return head + '<div class="wxleg">' + rows.join("") + "</div>" + (st.arrows || st.metric === "wind" || st.metric === "gust" ? '<div class="dim" style="font-size:.74rem;margin-top:4px">Arrows point the way the wind is blowing; longer is faster.</div>' : "") + '<div class="dim" style="font-size:.74rem;margin-top:2px">Faint: not reporting right now.</div>';
    };
    // the options a visitor can change: what is shown, wind arrows, only stations reporting now; the full map adds a value range and a search
    api.controlsHtml = function (extra) {
      var o = ORDER.map(function (k) { return '<option value="' + k + '"' + (k === st.metric ? " selected" : "") + ">" + METRICS[k].label + "</option>"; }).join("");
      var h = '<div class="wxopts"><label>Show <select data-wx="metric">' + o + "</select></label>" +
        '<label><input type="checkbox" data-wx="arrows"' + (st.arrows ? " checked" : "") + '> Wind rose and arrow in the label</label>' +
        '<label><input type="checkbox" data-wx="online"' + (st.online ? " checked" : "") + "> Reporting now only</label></div>";
      if (extra) h += '<div class="wxopts" style="margin-top:8px"><label>Only from <input type="number" data-wx="min" value="' + esc(st.min) + '" style="width:80px"></label><label>to <input type="number" data-wx="max" value="' + esc(st.max) + '" style="width:80px"></label>' +
        '<span class="dim" data-wx-unit style="font-size:.8rem"></span></div><div style="margin-top:8px"><input type="text" data-wx="q" placeholder="Search stations" value="' + esc(st.q) + '" style="width:100%"></div>';
      return h;
    };
    api.displayHtml = function () {
      var items = [["id", "Station ID"], ["cond", "Sky and weather icon"]].concat(ORDER.map(function (k) { return [k, METRICS[k].label]; }));
      return '<div class="lv-sub">Label Also Shows</div><div class="wxfields" data-wx-fields>' + items.map(function (x) { return '<label><input type="checkbox" data-wx-extra="' + x[0] + '"' + (st.extra.indexOf(x[0]) >= 0 ? " checked" : "") + "> " + esc(x[1]) + "</label>"; }).join("") + "</div>" +
        '<div class="dim" style="font-size:.74rem;margin-top:4px">The label always shows what is chosen under Show.</div><div class="lv-row"><label><input type="checkbox" data-wx="hover"' + (st.hover ? " checked" : "") + "> Hover details</label></div>" +
        '<div class="lv-row"><label title="Zoomed out, stations that would overlap are drawn as one label with the average of their readings"><input type="checkbox" data-wx="combine"' + (st.combine ? " checked" : "") + "> Combine overlapping stations when zoomed out</label></div>";
    };
    api.bind = function (root) {
      function unitNote() { var el = root.querySelector("[data-wx-unit]"); if (el) el.textContent = conv(METRICS[st.metric].kind, 1, units)[1] || ""; }
      root.addEventListener("change", function (e) {
        var x = e.target.getAttribute && e.target.getAttribute("data-wx-extra"); if (!x) return;
        var l = st.extra.filter(function (k) { return k !== x; }); if (e.target.checked) l.push(x);
        api.set("extra", l);
      });
      root.addEventListener("input", function (e) {
        var k = e.target.getAttribute && e.target.getAttribute("data-wx"); if (!k) return;
        if (k === "arrows" || k === "online" || k === "hover" || k === "combine") api.set(k, e.target.checked);
        else if (k === "metric") { api.set("metric", e.target.value); unitNote(); }
        else api.set(k, e.target.value);
      });
      unitNote();
    };
    return api;
  }
  window.ArkWx = {icon: svgIcon, create: create, METRICS: METRICS, ORDER: ORDER, conv: conv, compass: compass, colorFor: colorFor, esc: esc};
})();
