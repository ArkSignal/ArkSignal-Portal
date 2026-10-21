/* Picture and animation viewer: <div class="looper" data-src="/some/frames.json"></div> becomes a picture that zooms and pans, with (when there is more than one picture) play/pause, step, speed,
   scrub and the time of each frame. The address answers {frames: [{t: ISO time, url}], more: bool, ms: milliseconds per frame, wanted}. While "more" is true the player asks again every few seconds
   until the loop is complete, then now and then for newer pictures.
   With data-fit="1" the viewer is as tall as the visible window (the whole picture is in view without scrolling the page) and the controls sit over the picture; otherwise it is as tall as the picture.
   Zoom: the + and - buttons, Fit, the mouse wheel, a double click or double tap, a two-finger pinch; drag to pan.
   From a script: box.looper.load(newAddress, keepZoom) loads another series in place; box.looper.zoomTo(1) fits; box.looper.addOverlay(element) puts an element over the picture. */
(function () {
  var css = ".looper-stage{position:relative;width:100%;margin:0 auto;background:#0008;border-radius:10px;overflow:hidden;touch-action:pan-y;user-select:none;-webkit-user-select:none;scroll-margin-top:68px}"
    + ".looper-stage.zoomed{touch-action:none;cursor:grab}.looper-stage.dragging{cursor:grabbing}"
    + ".looper-img{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform;-webkit-user-drag:none;pointer-events:none;max-width:none}"
    + ".looper-zoom{position:absolute;top:8px;right:8px;display:flex;flex-direction:column;gap:6px;z-index:3}"
    + ".looper-zoom button{width:38px;height:38px;border-radius:50%;border:1px solid rgba(255,255,255,.35);background:rgba(0,0,0,.55);color:#fff;font-size:1.15rem;line-height:1;cursor:pointer;padding:0}"
    + ".looper-zoom button.fit{font-size:.7rem;font-weight:700}"
    + ".looper-empty{position:absolute;left:0;right:0;top:40%;padding:0 12px;text-align:center;opacity:.8}.looper-when{margin:8px 0 4px;font-weight:600}.looper-scrub{width:100%;margin:2px 0 8px}"
    + ".looper-bar{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.looper-bar .btn{margin:0}.looper-bar select{width:auto}.looper-loop{margin:0 0 0 4px;white-space:nowrap}.looper-status{margin-top:6px}"
    + ".looper-hint{margin-top:4px;font-size:.8rem}"
    + ".looper-over{position:absolute;left:0;right:0;bottom:0;padding:6px 10px 8px;background:linear-gradient(to top,rgba(0,0,0,.82),rgba(0,0,0,.55) 70%,transparent);z-index:2;color:#fff}"
    + ".looper-over .looper-when{margin:0 0 2px;font-size:.9rem;text-shadow:0 1px 2px #000}.looper-over .looper-scrub{margin:0 0 4px}";
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text) e.textContent = text;
    return e;
  }
  function fmt(t) { var d = new Date(t); return window.fmtTime ? window.fmtTime(d, "dt") : d.toLocaleString(); }

  function start(box) {
    var src = box.dataset.src, fit = box.dataset.fit === "1", frames = [], cache = {}, idx = 0, timer = null, playing = false, speed = 1, base = 400, askTimer = null, userMoved = false, gen = 0;
    var scrolls = false, scale = 1, px = 0, py = 0, SW = 0, SH = 0, W = 0, H = 0;
    box.innerHTML = "";
    var stage = el("div", { "class": "looper-stage" });
    var img = el("img", { "class": "looper-img", alt: box.dataset.alt || "Picture", draggable: "false" });
    var empty = el("div", { "class": "looper-empty" }, "Loading the pictures...");
    var zoomBox = el("div", { "class": "looper-zoom" });
    var zin = el("button", { type: "button", "aria-label": "Zoom in", title: "Zoom in" }, "+");
    var zout = el("button", { type: "button", "aria-label": "Zoom out", title: "Zoom out" }, "−");
    var zfit = el("button", { type: "button", "class": "fit", "aria-label": "Fit the picture to the window", title: "Fit" }, "Fit");
    [zin, zout, zfit].forEach(function (b) { zoomBox.appendChild(b); });
    stage.appendChild(img); stage.appendChild(empty); stage.appendChild(zoomBox);
    var when = el("div", { "class": "looper-when", "aria-live": "off" });
    var scrub = el("input", { type: "range", min: "0", max: "0", value: "0", "aria-label": "Picture in the loop", "class": "looper-scrub" });
    var bar = el("div", { "class": "looper-bar" });
    var prev = el("button", { type: "button", "class": "btn small neutral", "aria-label": "Previous picture" }, "◀");
    var play = el("button", { type: "button", "class": "btn small", "aria-label": "Play or pause" }, "Pause");
    var next = el("button", { type: "button", "class": "btn small neutral", "aria-label": "Next picture" }, "▶");
    var sp = el("select", { "aria-label": "Speed" });
    [["0.5", "Slow"], ["1", "Normal"], ["2", "Fast"], ["4", "Faster"]].forEach(function (o) { var op = el("option", { value: o[0] }, o[1]); if (o[0] === "1") op.selected = true; sp.appendChild(op); });
    var loopCb = el("input", { type: "checkbox", id: "lp-" + Math.random().toString(36).slice(2), checked: "checked" });
    var loopLb = el("label", { "for": loopCb.id, "class": "looper-loop" }, " Loop");
    loopLb.insertBefore(loopCb, loopLb.firstChild);
    var status = el("div", { "class": "dim looper-status" });
    var hint = el("div", { "class": "dim looper-hint" }, "Zoom with the mouse wheel, the + and - buttons, a double click or a two-finger pinch; drag to move around.");
    [prev, play, next, sp, loopLb].forEach(function (x) { bar.appendChild(x); });
    if (fit) {
      var over = el("div", { "class": "looper-over" });
      [when, scrub, bar].forEach(function (x) { over.appendChild(x); });
      stage.appendChild(over);
      box.appendChild(stage); box.appendChild(status); box.appendChild(hint);
    } else {
      [stage, when, scrub, bar, status, hint].forEach(function (x) { box.appendChild(x); });
    }

    // ---- sizing, zoom and pan. The picture is drawn at its fitted size (W x H, inside the stage SW x SH) and moved with translate and scale.
    function stickyTop() {   // how much of the top of the window the site's menu bar covers
      var n = document.querySelector("nav"), c = n ? getComputedStyle(n).position : "";
      return n && (c === "sticky" || c === "fixed") ? Math.round(n.getBoundingClientRect().height) : 0;
    }
    function layout() {
      SW = stage.clientWidth || 300;
      var nw = img.naturalWidth, nh = img.naturalHeight;
      if (fit) {
        // as tall as the room below the page heading when that is most of the window (no scrolling at all); otherwise as tall as the window and scrolled up under the menu bar
        var nv = stickyTop(), foot = 48, full = Math.max(240, window.innerHeight - nv - 16 - foot);
        var below = window.innerHeight - (stage.getBoundingClientRect().top + window.pageYOffset) - foot;
        scrolls = below < full * 0.8;
        SH = scrolls ? full : Math.max(240, below);
        stage.style.scrollMarginTop = Math.max(0, nv + 8 - (parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0)) + "px";
      }
      else SH = Math.round(Math.min(SW * (nw ? nh / nw : 0.75), window.innerHeight * 0.85));
      stage.style.height = SH + "px";
      if (nw && nh) { W = Math.min(SW, SH * nw / nh); H = W * nh / nw; } else { W = SW; H = SH; }
      img.style.width = W + "px"; img.style.height = H + "px";
      apply();
    }
    function maxScale() { return Math.max(4, img.naturalWidth && W ? (img.naturalWidth / W) * 2 : 4); }
    function apply() {
      scale = Math.max(1, Math.min(maxScale(), scale));
      var dw = W * scale, dh = H * scale;
      px = dw <= SW ? (SW - dw) / 2 : Math.min(0, Math.max(SW - dw, px));
      py = dh <= SH ? (SH - dh) / 2 : Math.min(0, Math.max(SH - dh, py));
      img.style.transform = "translate(" + px + "px," + py + "px) scale(" + scale + ")";
      stage.classList.toggle("zoomed", scale > 1.001);
    }
    function zoomAt(f, cx, cy) {
      var ns = Math.max(1, Math.min(maxScale(), scale * f));
      if (ns === scale) return;
      px = cx - (cx - px) * ns / scale; py = cy - (cy - py) * ns / scale; scale = ns; apply();
    }
    function zoomTo(s) { scale = s; px = 0; py = 0; apply(); }
    zin.addEventListener("click", function () { zoomAt(1.6, SW / 2, SH / 2); });
    zout.addEventListener("click", function () { zoomAt(1 / 1.6, SW / 2, SH / 2); });
    zfit.addEventListener("click", function () { zoomTo(1); });
    img.addEventListener("load", layout);
    window.addEventListener("resize", layout);
    stage.addEventListener("wheel", function (e) {
      if (e.target.closest && e.target.closest(".looper-over, .looper-panel, .looper-ui")) return;   // scroll the controls, not the picture
      e.preventDefault();
      var r = stage.getBoundingClientRect(), d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      zoomAt(Math.exp(-Math.max(-300, Math.min(300, d)) * 0.0022), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    stage.addEventListener("dblclick", function (e) {
      if (e.target.closest && e.target.closest(".looper-over, .looper-zoom, .looper-panel, .looper-ui")) return;
      var r = stage.getBoundingClientRect();
      if (scale > 1.001) zoomTo(1); else zoomAt(2.5, e.clientX - r.left, e.clientY - r.top);
    });
    var ptrs = {}, last = null, pinch = 0;
    function dist() { var k = Object.keys(ptrs); if (k.length < 2) return 0; var a = ptrs[k[0]], b = ptrs[k[1]]; return Math.hypot(a.x - b.x, a.y - b.y); }
    stage.addEventListener("pointerdown", function (e) {
      if (e.target.closest && e.target.closest(".looper-over, .looper-zoom, .looper-panel, .looper-ui")) return;
      ptrs[e.pointerId] = { x: e.clientX, y: e.clientY }; last = { x: e.clientX, y: e.clientY }; pinch = dist();
      try { stage.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ }
      if (scale > 1.001) stage.classList.add("dragging");
    });
    stage.addEventListener("pointermove", function (e) {
      if (!ptrs[e.pointerId]) return;
      ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (Object.keys(ptrs).length >= 2) {
        var d = dist(), k = Object.keys(ptrs), a = ptrs[k[0]], b = ptrs[k[1]], r = stage.getBoundingClientRect();
        if (pinch && d) zoomAt(d / pinch, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
        pinch = d;
      } else if (scale > 1.001 && last) {
        px += e.clientX - last.x; py += e.clientY - last.y; apply();
      }
      last = { x: e.clientX, y: e.clientY };
    });
    function up(e) { delete ptrs[e.pointerId]; pinch = 0; last = null; stage.classList.remove("dragging"); }
    stage.addEventListener("pointerup", up); stage.addEventListener("pointercancel", up);

    // ---- the pictures
    function show(i) {
      if (!frames.length) return;
      idx = Math.max(0, Math.min(frames.length - 1, i));
      var f = frames[idx];
      if (img.getAttribute("src") !== f.url) img.src = f.url;
      when.textContent = fmt(f.t) + (frames.length > 1 ? "  (" + (idx + 1) + " of " + frames.length + ")" : "");
      scrub.value = String(idx);
    }
    function tick() {
      if (!playing) return;
      if (idx >= frames.length - 1) {
        if (!loopCb.checked) { stop(); return; }
        show(0);
        timer = setTimeout(tick, (base / speed) * 3);   // a short rest before the loop starts over
        return;
      }
      show(idx + 1);
      timer = setTimeout(tick, base / speed + (idx >= frames.length - 1 ? base * 2 : 0));
    }
    function go() { playing = true; play.textContent = "Pause"; clearTimeout(timer); timer = setTimeout(tick, base / speed); }
    function stop() { playing = false; play.textContent = "Play"; clearTimeout(timer); }
    play.addEventListener("click", function () { if (playing) stop(); else { if (idx >= frames.length - 1) show(0); go(); } });
    prev.addEventListener("click", function () { stop(); userMoved = true; show(idx - 1); });
    next.addEventListener("click", function () { stop(); userMoved = true; show(idx + 1); });
    scrub.addEventListener("input", function () { stop(); userMoved = true; show(parseInt(scrub.value, 10) || 0); });
    sp.addEventListener("change", function () { speed = parseFloat(sp.value) || 1; });

    function transport(on) { [scrub, bar].forEach(function (x) { x.style.display = on ? "" : "none"; }); }
    function preload(list) { list.forEach(function (f) { if (!cache[f.url]) { var im = new Image(); im.src = f.url; cache[f.url] = im; } }); }
    function ask(g) {
      fetch(src, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); }).then(function (j) {
        if (g !== gen) return;   // another series was chosen meanwhile
        var wasLast = frames.length && idx >= frames.length - 1;
        var first = !frames.length;
        frames = j.frames || [];
        base = j.ms || base;
        scrub.max = String(Math.max(0, frames.length - 1));
        transport(frames.length > 1);
        preload(frames);
        empty.style.display = frames.length ? "none" : "";
        if (!frames.length) empty.textContent = j.more ? "Fetching the first pictures from the source..." : "There are no pictures for this view just now. Try again in a few minutes.";
        if (first && frames.length) { show(0); if (frames.length > 1) go(); }
        else if (wasLast && !userMoved) show(frames.length - 1);
        else if (frames.length) show(idx);
        if (frames.length === 1 && !first) show(0);
        status.textContent = j.more ? "Getting more pictures (" + frames.length + (j.wanted ? " of " + j.wanted : "") + ")..." : (frames.length > 1 ? frames.length + " pictures." : "");
        if (box.onloopdata) box.onloopdata(j);
        clearTimeout(askTimer);
        askTimer = setTimeout(function () { ask(g); }, j.more ? 2500 : 5 * 60000);   // keep asking until complete, then now and then for newer pictures
      }).catch(function () {
        if (g !== gen) return;
        empty.style.display = frames.length ? "none" : ""; empty.textContent = frames.length ? "" : "The pictures could not be loaded just now.";
        clearTimeout(askTimer); askTimer = setTimeout(function () { ask(g); }, 30000);
      });
    }
    function load(newSrc, keepZoom) {
      gen++; src = newSrc; frames = []; idx = 0; userMoved = false; cache = {};
      stop(); clearTimeout(askTimer);
      if (!keepZoom) zoomTo(1);
      empty.style.display = ""; empty.textContent = "Loading the pictures..."; status.textContent = ""; when.textContent = "";
      img.removeAttribute("src");
      ask(gen);
    }
    box.looper = { load: load, zoomTo: zoomTo, addOverlay: function (e) { stage.appendChild(e); }, stage: stage };
    transport(false);
    layout();
    ask(gen);
    if (fit) {   // the whole picture in view without scrolling the page: once the page has settled and the first picture has arrived
      var pad = el("div", { "aria-hidden": "true" });
      box.parentNode.insertBefore(pad, box.nextSibling);
      var aligned = 0, align = function () {
        if (aligned++ >= 2) return;
        layout();
        if (!scrolls) { pad.style.height = "0px"; return; }
        var nv = stickyTop(), top = stage.getBoundingClientRect().top + window.pageYOffset, docH = document.documentElement.scrollHeight - pad.offsetHeight;
        pad.style.height = Math.max(0, (top - nv - 8) + window.innerHeight - docH) + "px";   // room enough to scroll the picture up under the menu bar
        stage.scrollIntoView({ block: "start" });
      };
      setTimeout(align, 250); img.addEventListener("load", function once() { img.removeEventListener("load", once); setTimeout(align, 50); });
    }
  }
  function init() { document.querySelectorAll(".looper[data-src]").forEach(start); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
