/* The map host: switching the kind of map (Aircraft, Weather Stations, Radiosondes, Counties, Coverage, Repeaters, APRS Stations) without leaving the page.
   Every map type is one page made from the shared shell (partials/_mapshell.html); this script loads the chosen type's page in the background, takes its map area, runs its code and puts the old one away:
   its timers, its document and window listeners, its requests in flight and its Leaflet map. The address bar, the page title and the menu follow, so Back, Reload and shared links work as before.
   ArkMapHost.go(url) opens a map page of another type in place (or falls back to a normal visit); ArkMapHost.current() is the number of swaps so far.
   Scripts written for the page that carry data-ark-rerun (the search and the saved views) run again for every map type; library scripts run once. */
(function () {
  "use strict";
  if (window.ArkMapHost) return;
  var live = {timers: [], intervals: [], listeners: [], abort: window.AbortController ? new AbortController() : null}, swaps = 0, busy = false;
  var oRO = window.ResizeObserver, observers = [];
  if (oRO) window.ResizeObserver = function (cb) { var o = new oRO(cb); observers.push(o); return o; };
  var oSI = window.setInterval, oST = window.setTimeout, oF = window.fetch, oDA = document.addEventListener, oWA = window.addEventListener;
  // what a map page starts is recorded from here on, so that it can be stopped when the page is put away
  window.setInterval = function () { var id = oSI.apply(window, arguments); live.intervals.push(id); return id; };
  window.setTimeout = function () { var id = oST.apply(window, arguments); live.timers.push(id); if (live.timers.length > 5000) live.timers.splice(0, 2500); return id; };
  document.addEventListener = function (t, fn, o) { live.listeners.push([document, t, fn, o]); return oDA.call(document, t, fn, o); };
  window.addEventListener = function (t, fn, o) { live.listeners.push([window, t, fn, o]); return oWA.call(window, t, fn, o); };
  if (oF && live.abort) window.fetch = function (input, init) {
    if (init && init.signal) return oF.call(window, input, init);
    if (live.abort) init = Object.assign({}, init || {}, {signal: live.abort.signal});
    return oF.call(window, input, init);
  };
  function teardown() {
    live.intervals.forEach(function (i) { clearInterval(i); }); live.timers.forEach(function (t) { clearTimeout(t); });
    live.listeners.forEach(function (l) { try { l[0].removeEventListener(l[1], l[2], l[3]); } catch (e) {} });
    if (live.abort) { try { live.abort.abort(); } catch (e) {} }
    observers.forEach(function (o) { try { o.disconnect(); } catch (e) {} }); observers = [];
    try { if (window.arkMap && window.arkMap.remove) window.arkMap.remove(); } catch (e) {}
    window.arkMap = null; window.arkViewURL = null;
    live = {timers: [], intervals: [], listeners: [], abort: window.AbortController ? new AbortController() : null};
  }
  function between(doc) {   // the page's own content: everything between the action message line and the back-to-top button
    var a = doc.getElementById("action-feedback"), b = doc.getElementById("to-top"), out = [], n = a ? a.nextSibling : null;
    while (n && n !== b) { out.push(n); n = n.nextSibling; }
    return {a: a, b: b, nodes: out};
  }
  var have = {};   // the library scripts that have run on this page, by address without the version
  function key(src) { return new URL(src, location.href).href.split("?")[0]; }
  function noteScripts() { Array.prototype.forEach.call(document.scripts, function (s) { if (s.src) have[key(s.src)] = 1; }); }   // called before the new page's copies are put in
  function loaded(src) { return !!have[key(src)]; }
  function runScripts(nodes) {   // inline scripts and new library scripts run in order; library scripts that are already here are not loaded again
    var scripts = [];
    nodes.forEach(function (n) { if (n.nodeType !== 1) return; if (n.tagName === "SCRIPT") scripts.push(n); else Array.prototype.forEach.call(n.querySelectorAll("script"), function (s) { scripts.push(s); }); });
    return scripts.reduce(function (p, old) {
      return p.then(function () {
        return new Promise(function (resolve) {
          var src = old.getAttribute("src");
          if (src && loaded(src) && !old.hasAttribute("data-ark-rerun")) { resolve(); return; }
          var s = document.createElement("script");
          if (old.crossOrigin) s.crossOrigin = old.crossOrigin;
          if (src) { s.src = src; have[key(src)] = 1; s.onload = s.onerror = function () { resolve(); }; document.body.appendChild(s); }
          else { s.textContent = old.textContent; document.body.appendChild(s); s.remove(); resolve(); }
        });
      });
    }, Promise.resolve());
  }
  function syncChrome(doc) {   // the menu: which group is current, and the small bar under it
    var cur = document.querySelectorAll("#mainnav details.navmenu:not(#prefs-menu)"), neu = doc.querySelectorAll("#mainnav details.navmenu:not(#prefs-menu)");
    cur.forEach(function (d, i) { if (neu[i]) { d.classList.toggle("active", neu[i].classList.contains("active")); var a = d.querySelectorAll(".navdrop a"), b = neu[i].querySelectorAll(".navdrop a"); a.forEach(function (x, k) { if (b[k]) x.className = b[k].className; }); } });
    var mb = document.querySelector("#mainnav .mapbtn"), nb = doc.querySelector("#mainnav .mapbtn"); if (mb && nb) mb.className = nb.className;
    var sb = document.querySelector("#mainnav .subbar"), nsb = doc.querySelector("#mainnav .subbar"), main = document.querySelector("main");
    if (sb && nsb) { sb.querySelector("b").textContent = nsb.querySelector("b").textContent; sb.setAttribute("aria-label", nsb.getAttribute("aria-label") || ""); sb.querySelector(".subdrop").innerHTML = nsb.querySelector(".subdrop").innerHTML; sb.style.display = ""; }
    else if (sb) sb.style.display = "none";
    if (main) main.classList.toggle("has-sub", !!nsb);
  }
  async function go(url, opts) {
    opts = opts || {};
    if (busy) return false;
    busy = true;
    try {
      var r = await oF.call(window, url, {credentials: "same-origin", headers: {"X-Ark-Map-Host": "1"}});
      var text = r.ok ? await r.text() : "";
      var doc = text ? new DOMParser().parseFromString(text, "text/html") : null;
      if (!doc || !doc.getElementById("lv") || !doc.getElementById("action-feedback")) { busy = false; location.href = url; return false; }
      var oldC = between(document), newC = between(doc);
      noteScripts(); teardown();
      oldC.nodes.forEach(function (n) { n.remove(); });
      newC.nodes.forEach(function (n) {
        if (n.nodeType === 1 && n.tagName === "LINK" && n.rel === "stylesheet" && document.querySelector('link[href="' + n.getAttribute("href") + '"]')) return;
        oldC.b.parentNode.insertBefore(document.importNode(n, true), oldC.b);
      });
      document.title = doc.title; syncChrome(doc);
      if (!opts.pop) history.pushState({arkMap: true}, "", url);
      swaps++;
      await runScripts(newC.nodes);
      document.dispatchEvent(new CustomEvent("ark-map-swapped", {detail: {url: url}}));
      window.scrollTo(0, 0);
      busy = false; return true;
    } catch (e) { busy = false; location.href = url; return false; }
  }
  oWA.call(window, "popstate", function () { if (document.getElementById("lv")) go(location.href, {pop: true}); });
  window.ArkMapHost = {go: go, swaps: function () { return swaps; }};
})();
