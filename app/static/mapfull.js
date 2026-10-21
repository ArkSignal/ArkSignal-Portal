/* Full screen for every map page: a button on the top right edge of the map (#lv) fills the whole screen with the map and its panels; Esc or the same button goes back. It uses the browser's
   full screen where there is one and a fixed, screen-sized map where there is not (an iPhone), so it works everywhere. The Leaflet map is told to measure itself again afterwards. */
(function () {
  var lv = document.getElementById("lv");
  if (!lv || document.getElementById("lv-fullbtn")) return;
  var b = document.createElement("button");
  b.type = "button"; b.id = "lv-fullbtn"; b.className = "btn small neutral";
  b.setAttribute("aria-label", "Full screen"); b.title = "Full screen (Esc leaves it)";
  var ICON_IN = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var ICON_OUT = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  b.innerHTML = ICON_IN;
  lv.appendChild(b);
  // the button sits straight above the location target (bottom right of the map, where the map controls are, never under the list); it follows that button's place
  function place() {
    var l = lv.querySelector(".lv-locate"), r = lv.getBoundingClientRect();
    if (l) { var q = l.getBoundingClientRect(); b.style.top = (q.top - r.top - q.width - 8) + "px"; b.style.right = Math.max(4, r.right - q.right) + "px"; b.style.bottom = "auto"; b.style.width = q.width + "px"; b.style.height = q.width + "px"; }
    else { b.style.top = "auto"; b.style.bottom = "110px"; b.style.right = "10px"; }
  }
  place(); setInterval(place, 1000); window.addEventListener("resize", place);
  function on() { return lv.classList.contains("lv-full") || document.fullscreenElement === lv || document.webkitFullscreenElement === lv; }
  function paint() {
    var v = on();
    b.innerHTML = v ? ICON_OUT : ICON_IN; b.setAttribute("aria-label", v ? "Leave full screen" : "Full screen"); b.title = v ? "Leave full screen (Esc)" : "Full screen (Esc leaves it)";
    document.body.classList.toggle("lv-isfull", v); place();
    [100, 350].forEach(function (ms) { setTimeout(function () { window.dispatchEvent(new Event("resize")); }, ms); });
  }
  function enter() {
    var rq = lv.requestFullscreen || lv.webkitRequestFullscreen;
    if (rq) { try { var p = rq.call(lv); if (p && p.catch) p.catch(function () { lv.classList.add("lv-full"); paint(); }); return; } catch (e) { } }
    lv.classList.add("lv-full"); paint();
  }
  function leave() {
    lv.classList.remove("lv-full");
    if (document.fullscreenElement || document.webkitFullscreenElement) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
    paint();
  }
  b.addEventListener("click", function () { if (on()) leave(); else enter(); });
  document.addEventListener("fullscreenchange", paint);
  document.addEventListener("webkitfullscreenchange", paint);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && lv.classList.contains("lv-full")) leave(); });
})();
