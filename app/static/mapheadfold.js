/* On a phone the top panel (map type, search box and three buttons) is about a third of the map. Once the visitor has moved or zoomed the map, the map type and the search box fold away
   and only the three buttons stay, in one row, with a Search button that brings them back. Wider windows are not touched. Pure CSS class (head-min) on #lv-tools; see mapshell.css. */
(function () {
  var lv = document.getElementById("lv"), tools = document.getElementById("lv-tools"), map = document.getElementById("lvmap");
  var head = tools && tools.querySelector(".lv-head");
  if (!lv || !tools || !head || !map || lv.getAttribute("data-hf")) return;
  lv.setAttribute("data-hf", "1");
  var q = document.getElementById("q"), sb = document.createElement("button");
  sb.type = "button"; sb.id = "lv-searchbtn"; sb.className = "btn small neutral"; sb.textContent = "Search"; sb.style.margin = "0";
  sb.setAttribute("aria-label", "Show the map type and the search box again");
  head.insertBefore(sb, head.firstChild);
  var phone = function () { return window.innerWidth <= 700; };
  function fold(on) { tools.classList.toggle("head-min", on && phone()); }
  sb.addEventListener("click", function () { fold(false); if (q) q.focus(); });
  var sx = 0, sy = 0, down = false;
  map.addEventListener("pointerdown", function (e) { down = true; sx = e.clientX; sy = e.clientY; });
  map.addEventListener("pointerup", function (e) { if (down && Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 12) fold(true); down = false; });
  map.addEventListener("pointercancel", function () { down = false; });
  map.addEventListener("wheel", function () { fold(true); }, {passive: true});
  map.addEventListener("touchend", function (e) { if (e.touches && e.touches.length > 0) fold(true); }, {passive: true});   // a pinch: a finger still down when the other lifts
  window.addEventListener("resize", function () { if (!phone()) fold(false); });
})();

/* A popup opened on a phone must not slide under the top panel or the details card: Leaflet pans the map so the popup clears them, measured when the popup opens. */
(function () {
  if (!window.L || !L.Popup || L.Popup.prototype._arkPad) return;
  var adj = L.Popup.prototype._adjustPan;
  L.Popup.prototype._arkPad = true;
  L.Popup.prototype._adjustPan = function () {
    var lv = document.getElementById("lv"), t = document.getElementById("lv-tools"), c = document.getElementById("lv-card");
    if (lv && t && window.innerWidth <= 700) {
      var r = lv.getBoundingClientRect(), tr = t.getBoundingClientRect(), cr = c && c.style.display !== "none" ? c.getBoundingClientRect() : null;
      this.options.autoPanPaddingTopLeft = L.point(10, Math.max(10, Math.round(tr.bottom - r.top) + 10));
      this.options.autoPanPaddingBottomRight = L.point(10, cr && cr.height ? Math.round(r.bottom - cr.top) + 10 : 10);
    }
    return adj.apply(this, arguments);
  };
})();

/* While the details card is open on a phone, the round buttons that float over the bottom of the map (Normal look, locate, full screen) would sit on top of the card's text: they are put away until the card is
   closed or folded (class card-open on #lv, see mapshell.css). */
(function () {
  var lv = document.getElementById("lv"), card = document.getElementById("lv-card");
  if (!lv || !card || card.getAttribute("data-co")) return;
  card.setAttribute("data-co", "1");
  function sync() { var shown = card.style.display !== "none" && card.style.display !== ""; lv.classList.toggle("card-open", shown && !card.classList.contains("min")); }
  new MutationObserver(sync).observe(card, {attributes: true, attributeFilter: ["style", "class"]});
  sync();
})();

/* The Replay bar on a phone starts folded to the time, Play, Back to Live and the slider (about a fifth of the map instead of two fifths); More shows the step buttons, speed, date and time. The bar's own
   height drives the room the panels leave (static/pasttraffic.js watches it). */
(function () {
  var bar = document.getElementById("lv-replay"), more = document.getElementById("rp-more");
  if (!bar || !more || more.getAttribute("data-rm")) return;
  more.setAttribute("data-rm", "1");
  function set(open) { bar.classList.toggle("rp-open", open); more.textContent = open ? "Less" : "More"; more.setAttribute("aria-expanded", open ? "true" : "false"); }
  more.addEventListener("click", function () { set(!bar.classList.contains("rp-open")); });
  set(false);
})();

/* While the Replay bar is up on a phone, the round buttons along the bottom of the map are put away too (class replay-on on #lv, see mapshell.css). */
(function () {
  var lv = document.getElementById("lv"), bar = document.getElementById("lv-replay");
  if (!lv || !bar || bar.getAttribute("data-ro")) return;
  bar.setAttribute("data-ro", "1");
  function sync() { lv.classList.toggle("replay-on", bar.style.display !== "none"); }
  new MutationObserver(sync).observe(bar, {attributes: true, attributeFilter: ["style"]});
  sync();
})();

/* THE RULE (Marty, 2026-10-16): opening a details card, or choosing another item while it is open, folds the Weather, Layers and Filters and Legend panels away on every map, so the card is what shows.
   Each map has its own way of opening a card, so this watches the card itself and presses the panels' own buttons. */
(function () {
  var card = document.getElementById("lv-card");
  if (!card || card.getAttribute("data-fa")) return;
  card.setAttribute("data-fa", "1");
  var wasShown = false, lastName = "";
  function fold() {
    var tb = document.getElementById("lv-toolsbody"), wx = document.getElementById("lv-wx"), lg = document.getElementById("lv-legend");
    function press(id) { var b = document.getElementById(id); if (b) b.click(); }
    if (wx && wx.style.display === "block") press("lv-wxbtn");
    if (lg && lg.style.display === "block") press("lv-legbtn");
    if (tb && tb.style.display !== "none") press("lv-filtbtn");
  }
  function changed() {
    var shown = card.style.display !== "none" && card.style.display !== "";
    var nm = (document.getElementById("c-name") || {}).textContent || "";
    if (shown && (!wasShown || nm !== lastName)) { fold(); card.scrollTop = 0; }
    wasShown = shown; lastName = nm;
  }
  new MutationObserver(changed).observe(card, {attributes: true, attributeFilter: ["style"], childList: true, subtree: true, characterData: true});
  changed();
})();
