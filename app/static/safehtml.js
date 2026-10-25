/* Leaflet shows a string given to a popup or a tooltip as HTML. Names that come from outside (a mesh node, a vessel, a weather station, a repeater) end up in such strings, so every string is cleaned first:
   scripts, frames, forms and the like are dropped, every on... attribute is dropped, and a link or picture address that runs script (javascript:, vbscript:, data: other than a picture) is dropped.
   The page's Content-Security-Policy would stop such script from running anyway; this makes sure the markup is harmless too. Loaded right after Leaflet. */
(function () {
  if (!window.L || !L.DivOverlay || L.__safehtml) return;
  L.__safehtml = true;
  var GONE = /^(script|style|iframe|frame|frameset|object|embed|applet|link|meta|base|form|input|button|textarea|select)$/i;
  var BADURL = /^\s*(javascript|vbscript|data):/i, OKDATA = /^\s*data:image\/(png|jpeg|gif|webp);/i;
  function clean(html) {
    var t = document.createElement("template");
    t.innerHTML = html;
    (function walk(n) {
      Array.prototype.slice.call(n.childNodes).forEach(function (c) {
        if (c.nodeType !== 1) return;
        if (GONE.test(c.nodeName)) { n.removeChild(c); return; }
        Array.prototype.slice.call(c.attributes).forEach(function (a) {
          var nm = a.name.toLowerCase();
          if (nm.indexOf("on") === 0 || ((nm === "href" || nm === "src" || nm === "xlink:href" || nm === "action" || nm === "formaction") && BADURL.test(a.value) && !OKDATA.test(a.value))) c.removeAttribute(a.name);
        });
        walk(c);
      });
    })(t.content);
    return t.innerHTML;
  }
  var set = L.DivOverlay.prototype.setContent;
  L.DivOverlay.prototype.setContent = function (content) { return set.call(this, typeof content === "string" ? clean(content) : content); };
})();
