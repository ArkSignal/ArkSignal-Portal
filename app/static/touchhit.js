/* Bigger touch targets on phones and tablets: a small round marker keeps its look but is clickable from a finger's distance (an invisible wide stroke around it), and the hospital
   markers get a larger hit area. Does nothing for a mouse. Loaded after Leaflet on every page that draws markers. */
(function () {
  "use strict";
  var coarse = false; try { coarse = window.matchMedia("(pointer: coarse)").matches; } catch (e) {}
  if (!coarse || !window.L || !L.CircleMarker) return;
  var st = document.createElement("style");
  st.textContent = "path.leaflet-interactive.ark-hit{stroke-width:26px!important;stroke-opacity:.01!important;stroke-linecap:round}.hico::before{content:'';position:absolute;inset:-12px;border-radius:50%}";
  document.head.appendChild(st);
  L.CircleMarker.addInitHook(function () {
    if (this.options.interactive !== false && (this.options.radius || 10) < 10 && !this.options.className) this.options.className = "ark-hit";
  });
})();
