/* On a phone the Layers and Filters panel opens over the whole screen, so it is easy to read and use, with a bar at the top and a button at the bottom that put it away again (Escape too). Wider windows keep the
   panel where it is. The panel is the one the map page already opens and closes (#lv-toolsbody shown or not): this script only dresses it. */
(function () {
  var lv = document.getElementById("lv"), tools = document.getElementById("lv-tools"), body = document.getElementById("lv-toolsbody"), btn = document.getElementById("lv-filtbtn");
  if (!lv || !tools || !body || !btn || lv.getAttribute("data-fs")) return;
  lv.setAttribute("data-fs", "1");
  var bar = document.createElement("div"); bar.id = "fs-bar";
  bar.innerHTML = '<b>Layers &amp; Filters</b><button type="button" class="btn" id="fs-close" aria-label="Close Layers and Filters and go back to the map">&#10005; Close</button>';
  tools.insertBefore(bar, tools.firstChild);
  var done = document.createElement("button"); done.type = "button"; done.id = "fs-done"; done.className = "btn"; done.textContent = "Show the Map";
  lv.appendChild(done);
  function sync() {
    var open = body.style.display !== "none" && window.innerWidth <= 700;
    lv.classList.toggle("fs-open", open);
    document.documentElement.classList.toggle("fs-lock", open);
  }
  bar.querySelector("#fs-close").addEventListener("click", function () { if (body.style.display !== "none") btn.click(); });
  done.addEventListener("click", function () { if (body.style.display !== "none") btn.click(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && lv.classList.contains("fs-open") && body.style.display !== "none") btn.click(); });
  new MutationObserver(sync).observe(body, {attributes: true, attributeFilter: ["style"]});
  window.addEventListener("resize", sync);
  sync();
})();
