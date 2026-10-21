/* The Units box in a map's Layers and Filters (Display and View): the unit set for this map's service, one choice per service (the same one the Display menu has, saved the same way). A map page that has its
   own Units box (the aircraft and weather maps) keeps it and this one stays out of the way. The choice is "Default (...)" (the site's default for the service, else the standard unit) or one of the three sets.
   A page that can redraw by itself defines window.arkUnitsChanged; the others reload so every figure is written again in the new unit. */
(function () {
  var sel = document.getElementById("ark-units-sel"), head = document.getElementById("ark-units-h");
  if (!sel) return;
  if (document.getElementById("l-units")) { sel.style.display = "none"; if (head) head.style.display = "none"; return; }
  var area = window.ARK_AREA || "", NAMES = {imperial: "Feet, mph, miles, °F", aero: "Feet, knots, nautical miles, °C", metric: "Meters, km/h, kilometers, °C"};
  var site = (window.SITE_UNITS_BY || {})[area], std = ArkUnits.standard(), own = (window.PREFS || {}).units;
  sel.innerHTML = '<option value="">Default (' + NAMES[own || site || std] + ")</option>" + ["imperial", "aero", "metric"].map(function (k) { return '<option value="' + k + '">' + NAMES[k] + "</option>"; }).join("");
  var P = window.PREFS || {};
  sel.value = (area && P["u_" + area]) || "";
  sel.addEventListener("change", function () {
    ArkUnits.save(sel.value);
    setTimeout(function () { if (typeof window.arkUnitsChanged === "function") window.arkUnitsChanged(); else location.reload(); }, 150);
  });
})();
