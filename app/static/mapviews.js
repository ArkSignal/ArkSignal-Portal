/* Saved views for every map: a name for how the map looks right now (where it is looking, zoom, filters, the selected item and which layers are on), to come back to in one tap.
   Signed-in people keep their views with the account (so they follow them to every device); everyone else keeps them in this browser. Each map lists only its own views.
   Needs window.arkViewURL() from the map page (the same address "Copy Link to This View" gives). Adds its controls above that button. */
(function () {
  "use strict";
  var tries = 0;
  (function wait() {   // the page defines window.arkViewURL when its map starts, which is after this script is read
    if (window.arkViewURL) { start(); return; }
    if (tries++ < 60) setTimeout(wait, 200);
  })();
  function start() {
  var anchor = document.getElementById("lv-sharelink");
  if (!anchor) return;
  var row = anchor.parentNode, path = location.pathname.replace(/\/$/, "") || "/";
  var signed = !!window.ARK_SIGNED_IN, views = [];
  var wrap = document.createElement("div");
  wrap.innerHTML = '<div class="lv-sub">Saved Views</div><div class="lv-row"><select id="lv-views" aria-label="Saved views"><option value="">Choose a saved view</option></select></div>'
    + '<div class="lv-row"><button class="btn small neutral" type="button" id="lv-vsave" style="margin:0">Save This View</button><button class="btn small neutral" type="button" id="lv-vdel" style="margin:0" disabled>Delete</button><span id="lv-vnote" class="dim" style="font-size:.78rem"></span></div>';
  row.parentNode.insertBefore(wrap, row);
  var sel = document.getElementById("lv-views"), del = document.getElementById("lv-vdel"), note = document.getElementById("lv-vnote");
  function say(t) { note.textContent = t; setTimeout(function () { note.textContent = ""; }, 3500); }
  function mine() { return views.filter(function (v) { return (v.url.split("?")[0].replace(/\/$/, "") || "/") === path; }); }
  function draw() {
    sel.innerHTML = '<option value="">' + (mine().length ? "Choose a saved view" : "No saved views yet") + "</option>" + mine().map(function (v, i) { return '<option value="' + i + '">' + v.name.replace(/[&<>"]/g, "") + "</option>"; }).join("");
    del.disabled = true;
  }
  function localGet() { try { return JSON.parse(localStorage.getItem("lv.views") || "[]"); } catch (e) { return []; } }
  function localSet(v) { try { localStorage.setItem("lv.views", JSON.stringify(v)); } catch (e) {} }
  function load() {
    if (signed) fetch("/my/map-views", {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) { views = d.views || []; draw(); }).catch(function () { views = localGet(); draw(); });
    else { views = localGet(); draw(); }
  }
  function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : (window.CSRF_TOKEN || ""); }
  document.getElementById("lv-vsave").addEventListener("click", function () {
    var name = (window.prompt("A name for this view (for example Little Rock aircraft):") || "").trim().slice(0, 40);
    if (!name) return;
    var u = window.arkViewURL(), rel = u.replace(location.origin, ""), layers = ""; try { layers = localStorage.getItem("lv.layers") || ""; } catch (e) {}
    var v = {name: name, url: rel, layers: layers};
    if (signed) fetch("/my/map-views", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify(v)}).then(function (r) { return r.json(); }).then(function (d) { if (d.error) say(d.error); else { views = d.views; draw(); say("Saved."); } });
    else { var all = localGet().filter(function (x) { return !(x.name === name && x.url.split("?")[0] === rel.split("?")[0]); }); all.push(v); localSet(all.slice(-25)); views = all.slice(-25); draw(); say("Saved in this browser."); }
  });
  sel.addEventListener("change", function () {
    del.disabled = sel.value === "";
    if (sel.value === "") return;
    var v = mine()[parseInt(sel.value, 10)]; if (!v) return;
    try { if (v.layers) localStorage.setItem("lv.layers", v.layers); } catch (e) {}
    location.href = v.url;
  });
  del.addEventListener("click", function () {
    var v = mine()[parseInt(sel.value, 10)]; if (!v || !window.confirm("Delete the view \"" + v.name + "\"?")) return;
    if (signed) fetch("/my/map-views/delete", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify({name: v.name, url: v.url.split("?")[0]})}).then(function (r) { return r.json(); }).then(function (d) { views = d.views || []; draw(); say("Deleted."); });
    else { var all = localGet().filter(function (x) { return !(x.name === v.name && x.url.split("?")[0] === v.url.split("?")[0]); }); localSet(all); views = all; draw(); say("Deleted."); }
  });
  load();
  }
})();
