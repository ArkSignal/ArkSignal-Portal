/* The small arrow centred on the bottom edge of whichever panel is open on a map (Filters, Weather or Legend): one tap folds it away so the map shows, and the buttons bring it back.
   Every map page that has the button #lv-fold calls ArkFold.init with the three functions that close its panels and gets back a sync function to call whenever a panel opens or closes. */
window.ArkFold = {
  init: function (o) {
    var f = document.getElementById("lv-fold");
    if (!f) return function () {};
    function shown(id) { var e = document.getElementById(id); return !!e && e.style.display === "block"; }
    function sync() {
      var tb = document.getElementById("lv-toolsbody");
      f.style.display = ((tb && tb.style.display !== "none") || shown("lv-wx") || shown("lv-legend")) ? "block" : "none";
    }
    f.addEventListener("click", function () {
      if (shown("lv-legend")) o.legOpen(false); else if (shown("lv-wx")) o.wxOpen(false); else o.filtersShow(false);
      sync();
    });
    window.arkFoldSync = sync;
    return sync;
  }
};
