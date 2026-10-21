/* Site themes in the browser: picks the theme (the person's choice, else the site's, which follows the seasons on their days), puts its colors on the page before it is drawn, fills the theme list of the
   Display menu, shows a chip anyone can press to go back to the normal look when a seasonal theme was put on by the site, and adds the light, optional decorations (emoji drifting down the page).
   The data (ARK_THEMES) comes from app/themes.py: every theme's color variables and symbols, which theme the site shows today, and which is the normal one. Map code asks ArkThemes.balloon(...) and
   ArkThemes.plane(...) for the symbols of a seasonal theme (Santa and bats on some aircraft, pumpkins and skeletons on balloons...). */
window.ArkThemes = (function () {
  var C = window.ARK_THEMES || {themes: {}, site: "dark", normal: "dark", order: []}, applied = [], lastId = "dark", lastSource = "site";

  function hash(s) { var h = 5381; s = String(s); for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return Math.abs(h); }

  // which theme a set of preferences gives: {id, source} where source says who chose it: "me" (a pick of the person), "site" (the site's own theme for the day), "normal"
  function resolve(prefs) {
    var t = (prefs && prefs.theme) || "site";
    if (prefs && prefs.themeV !== "2" && t !== "site") t = "site";   // a look saved before themes existed (Dark, Light, Match my device) counts as 'follow the site' once; the next pick is kept
    if (!C.enabled && C.enabled !== undefined) return {id: t === "light" ? "light" : (t === "auto" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : "dark"), source: "me"};
    if (t === "site") return {id: C.themes[C.site] ? C.site : "dark", source: C.themes[C.site] && C.themes[C.site].kind === "seasonal" ? "site" : "normal"};
    if (t === "normal") return {id: C.themes[C.normal] ? C.normal : "dark", source: "normal"};
    if (t === "auto") return {id: matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark", source: "me"};
    return {id: C.themes[t] ? t : "dark", source: "me"};
  }

  function apply(prefs) {
    var r = resolve(prefs), th = C.themes[r.id] || {mode: "dark", vars: {}}, root = document.documentElement;
    applied.forEach(function (k) { root.style.removeProperty(k); });
    applied = [];
    Object.keys(th.vars || {}).forEach(function (k) { root.style.setProperty(k, th.vars[k]); applied.push(k); });
    root.setAttribute("data-theme", th.mode === "light" ? "light" : "dark");
    root.setAttribute("data-ark-theme", r.id);
    lastId = r.id; lastSource = r.source;
    if (document.body) { chrome(prefs); }
    return r;
  }

  function decorOn(prefs) { return C.decor !== false && (prefs && prefs.deco) !== "0"; }

  // the theme list of the Display menu
  function fillSelect(sel, prefs) {
    if (!sel) return;
    var o = '<option value="site">Site default' + ((C.seasonal_on && C.themes[C.seasonal_on]) ? " (" + C.themes[C.seasonal_on].name + " today)" : "") + '</option><option value="normal">Normal look (no seasonal themes)</option><option value="auto">Match my device</option>';
    var g = {base: "", scheme: "", seasonal: ""};
    (C.order || []).forEach(function (id) { var t = C.themes[id]; if (t) g[t.kind] += '<option value="' + id + '">' + t.name + "</option>"; });
    o += '<optgroup label="Basic">' + g.base + '</optgroup><optgroup label="Color schemes">' + g.scheme + '</optgroup><optgroup label="Seasons and holidays">' + g.seasonal + "</optgroup>";
    var mine = prefs && prefs.theme;
    if (mine && C.themes[mine] && (C.order || []).indexOf(mine) < 0) {   // a theme the person chose that is out of season stays in their list, so the menu shows what they have
      o += '<optgroup label="Your choice"><option value="' + mine + '">' + C.themes[mine].name + " (out of season)</option></optgroup>";
    }
    sel.innerHTML = o;
  }

  // the way out of a seasonal theme the site put on by itself, which anyone can take at any time: on an ordinary page a banner under the menu that says so (with a Normal Look button), on a map a solid pill at the top middle
  function chip() {
    var el = document.getElementById("ark-normal"), th = C.themes[lastId], show = lastSource === "site" && th && th.kind === "seasonal";
    if (!show) { if (el) el.remove(); return; }
    var lv = document.getElementById("lv"), main = document.querySelector("main");
    if (!el) {
      el = document.createElement("div"); el.id = "ark-normal"; el.setAttribute("role", "region"); el.setAttribute("aria-label", "Seasonal theme");
      var btn = '<button type="button" id="ark-normal-btn">Normal look</button>';
      el.innerHTML = '<span aria-hidden="true" class="ark-normal-ic">' + ((th.deco || [""])[0]) + '</span><span class="ark-normal-tx"><span class="ark-normal-pre">The site is wearing its </span><b>' + th.name + "</b> theme.</span> " + btn;
      el.querySelector("button").addEventListener("click", function () { if (window.adsbSavePrefs) window.adsbSavePrefs({theme: "normal"}); else { try { var p = JSON.parse(localStorage.getItem("adsb.prefs") || "{}"); p.theme = "normal"; p.themeV = "2"; localStorage.setItem("adsb.prefs", JSON.stringify(p)); } catch (e) {} apply({theme: "normal", themeV: "2"}); } });
    }
    var cls = lv ? "ark-normal-map" : "ark-normal-bar";
    if (el.className !== cls) el.className = cls;
    if (lv) { if (el.parentNode !== lv) lv.appendChild(el); }
    else if (main && main.parentNode) { if (el.parentNode !== main.parentNode || el.nextElementSibling !== main) main.parentNode.insertBefore(el, main); }
    else if (!el.parentNode) document.body.appendChild(el);
    if (!document.getElementById("ark-chip-css")) {
      var st = document.createElement("style"); st.id = "ark-chip-css";
      st.textContent = "#ark-normal button{font:inherit;font-weight:700;cursor:pointer;border:0;border-radius:999px;padding:6px 16px;background:#fff;color:#111;margin-left:8px}#ark-normal button:hover{background:#ffe9b0}" +
        "#ark-normal.ark-normal-bar{display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;padding:9px 16px;background:var(--acc);color:#111;font-size:.95rem;text-align:center}" +
        "#ark-normal.ark-normal-bar .ark-normal-ic{font-size:1.2em}" +
        "#ark-normal.ark-normal-map{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:1300;display:flex;align-items:center;gap:6px;padding:6px 8px 6px 14px;border-radius:999px;background:var(--acc);color:#111;font-weight:600;font-size:.88rem;box-shadow:0 2px 12px rgba(0,0,0,.55);white-space:nowrap}" +
        "@media (max-width:700px){#ark-normal.ark-normal-map{top:auto;bottom:62px;left:10px;transform:none}#ark-normal.ark-normal-map .ark-normal-tx{display:none}#ark-normal.ark-normal-bar{flex-wrap:nowrap;justify-content:center;padding:5px 10px;font-size:.85rem}#ark-normal.ark-normal-bar .ark-normal-pre{display:none}#ark-normal.ark-normal-bar button{padding:4px 12px;margin-left:4px;white-space:nowrap}}";
      document.head.appendChild(st);
    }
  }
  function chrome(prefs) { chip(); dress(prefs); }
  // a map page swapped in place loses the chip with the old map: put it back (only the chip, so this never loops)
  if (window.MutationObserver) { var mt = null; new MutationObserver(function () { clearTimeout(mt); mt = setTimeout(function () { if (document.body && lastSource === "site") chip(); }, 300); }).observe(document.documentElement, {childList: true, subtree: true}); }

  // the dressing of a seasonal theme: still things only. A ribbon of the theme's colors along the top, a small symbol beside the site name, a corner picture (cobweb, string of lights...), a greeting in the footer
  var ART = {
    cobweb: '<svg viewBox="0 0 120 120" width="120" height="120" fill="none" stroke="#e8e0f4" stroke-opacity=".5" stroke-width="1"><path d="M120 0L0 0M120 0L0 40M120 0L0 80M120 0L20 120M120 0L60 120M120 0L100 120"/><path d="M100 0Q96 20 120 24M78 0Q70 40 120 50M55 0Q40 62 120 80M30 0Q12 84 120 108" /></svg>',
    stars: '<svg viewBox="0 0 120 40" width="160" height="54"><g fill="#fff" fill-opacity=".55"><path d="M10 6l2 6h6l-5 4 2 6-5-4-5 4 2-6-5-4h6z"/><path d="M55 14l1.500 4.500h4.500l-3.600 2.800 1.400 4.500-3.800-2.800-3.800 2.800 1.400-4.500-3.600-2.800h4.500z"/><path d="M100 4l1.800 5.400h5.400l-4.400 3.300 1.700 5.400-4.500-3.300-4.500 3.300 1.700-5.400-4.400-3.300h5.400z"/></g></svg>',
    leaves: '<svg viewBox="0 0 120 60" width="150" height="75" fill-opacity=".55"><path d="M15 40c0-18 14-30 28-32 2 18-8 32-28 32z" fill="#ff9a3c"/><path d="M60 50c-8-14-2-30 14-38 8 16 4 32-14 38z" fill="#c9663a"/><path d="M92 36c4-14 16-22 28-20-2 14-12 24-28 20z" fill="#e8a23a"/></svg>',
    snowcap: '<svg viewBox="0 0 400 16" width="100%" height="16" preserveAspectRatio="none" fill="#fff" fill-opacity=".5"><path d="M0 0h400v6c-14 0-14 8-28 8s-14-6-28-6-14 8-28 8-14-8-28-8-14 6-28 6-14-8-28-8-14 8-28 8-14-6-28-6-14 8-28 8-14-8-28-8z"/></svg>',
    hearts: '<svg viewBox="0 0 120 40" width="150" height="50" fill="#ff6fa5" fill-opacity=".5"><path d="M20 30c-14-9-14-20-6-22 4-1 6 2 6 4 0-2 2-5 6-4 8 2 8 13-6 22z"/><path d="M62 24c-9-6-9-14-4-15 3-.5 4 1.500 4 3 0-1.500 1-3.500 4-3 5 1 5 9-4 15z"/><path d="M96 32c-11-8-11-17-5-19 3-1 5 1.500 5 3 0-1.500 2-4 5-3 6 2 6 11-5 19z"/></svg>',
    sparkle: '<svg viewBox="0 0 120 40" width="150" height="50" fill="#f4cf5c" fill-opacity=".6"><path d="M20 6l2 8 8 2-8 2-2 8-2-8-8-2 8-2z"/><path d="M70 18l1.500 5 5 1.500-5 1.500-1.500 5-1.500-5-5-1.500 5-1.500z"/><path d="M104 4l1.200 4 4 1.200-4 1.200-1.200 4-1.200-4-4-1.200 4-1.200z"/></svg>'
  };
  function dress(prefs) {
    var th = C.themes[lastId], on = th && th.stripe && decorOn(prefs) && lastSource !== "normal";
    ["ark-stripe", "ark-art", "ark-badge", "ark-greet", "ark-lights"].forEach(function (id) { var e = document.getElementById(id); if (e) e.remove(); });
    if (!on) return;
    var st = document.getElementById("ark-dress-css");
    if (!st) { st = document.createElement("style"); st.id = "ark-dress-css";
      st.textContent = "#ark-stripe{position:fixed;left:0;right:0;top:0;height:4px;z-index:3000;pointer-events:none}#ark-art{position:fixed;top:0;right:0;z-index:2;pointer-events:none;line-height:0}" +
        "#ark-badge{margin-left:6px;font-size:1.05em}#ark-greet{display:block;margin-bottom:4px;font-weight:600;color:var(--acc)}" +
        "#ark-lights{position:fixed;left:0;right:0;top:4px;height:14px;z-index:3000;pointer-events:none;background:radial-gradient(circle at 10px 7px,var(--c1) 0 3.5px,transparent 4px) 0 0/48px 14px repeat-x,radial-gradient(circle at 24px 9px,var(--c2) 0 3.5px,transparent 4px) 0 0/48px 14px repeat-x,radial-gradient(circle at 38px 7px,var(--c3) 0 3.5px,transparent 4px) 0 0/48px 14px repeat-x;opacity:.85}";
      document.head.appendChild(st); }
    var s = document.createElement("div"); s.id = "ark-stripe"; s.setAttribute("aria-hidden", "true");
    var cols = th.stripe, n = cols.length; s.style.background = "linear-gradient(90deg," + cols.map(function (c, i) { return c + " " + Math.round(i * 100 / n) + "% " + Math.round((i + 1) * 100 / n) + "%"; }).join(",") + ")";
    document.body.appendChild(s);
    if (th.art === "lights") { var l = document.createElement("div"); l.id = "ark-lights"; l.setAttribute("aria-hidden", "true"); l.style.setProperty("--c1", "#ff5252"); l.style.setProperty("--c2", "#4cd37a"); l.style.setProperty("--c3", "#ffd447"); document.body.appendChild(l); }
    else if (th.art && ART[th.art]) { var a = document.createElement("div"); a.id = "ark-art"; a.setAttribute("aria-hidden", "true"); a.innerHTML = ART[th.art]; if (th.art === "snowcap") { a.style.left = "0"; a.style.top = "4px"; } document.body.appendChild(a); }
    var brand = document.querySelector("a.brand");
    if (brand && th.deco && th.deco.length) { var b = document.createElement("span"); b.id = "ark-badge"; b.setAttribute("aria-hidden", "true"); b.innerHTML = th.deco[0]; brand.appendChild(b); }
    var foot = document.querySelector("footer");
    if (foot && th.banner) { var g = document.createElement("span"); g.id = "ark-greet"; g.textContent = th.banner; foot.insertBefore(g, foot.firstChild); }
  }

  // symbols for the maps: a seasonal theme may swap some balloons and aircraft for its own (always the same ones for the same flight, so they do not flicker)
  function cur() { return C.themes[lastId]; }
  function symbols() { return C.decor !== false && (window.PREFS || {}).deco !== "0" && lastSource !== "normal"; }   // the Seasonal decorations box of the Display menu covers these too
  function balloon(serial) {
    var t = cur(); if (!t || !t.balloons || !t.balloons.length || !symbols()) return null;
    return t.balloons[hash(serial) % t.balloons.length];
  }
  function plane(hex) {
    var t = cur(); if (!t || !t.planes || !t.planes.length || !t.plane_share || !symbols()) return null;
    return hash(hex) % t.plane_share === 0 ? t.planes[hash(hex + "x") % t.planes.length] : null;
  }
  return {resolve: resolve, apply: apply, fillSelect: fillSelect, balloon: balloon, plane: plane, id: function () { return lastId; }, source: function () { return lastSource; }, config: function () { return C; },
          ready: function (prefs) { chrome(prefs || window.PREFS || {}); }};
})();
