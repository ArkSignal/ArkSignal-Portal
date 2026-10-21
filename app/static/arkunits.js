/* Units for every page, one set per service (Aircraft, Weather, Radiosondes, APRS, ACARS, Mesh, Repeaters, Places, Home): a person's own choice for the service (Display menu, or the Units box in a map's
   Layers and Filters), else the site's default for that service (Settings > Units: each service starts in its industry's own units: aviation in feet and knots, radiosondes in meters as on SondeHub...), else the site default
   (Settings > Home Page and Live Map > Units). A person's own overall choice (Display menu > Units) comes before the site's default for a service. The page's service is window.ARK_AREA. Three sets, as on the aircraft map:
     imperial   feet, mph, ft/min, miles, deg F        (the default)
     aero       feet, knots, ft/min, nautical miles, deg C
     metric     meters, km/h, m/s, kilometers, deg C
   ArkUnits.system()                     the set in use
   ArkUnits.alt(m) / .spd(ms) / .vspd(ms) / .dist(km) / .temp(c)   -> {v, u}: the number and its unit, from metric inputs
   ArkUnits.text.alt(m, digits) ...      -> "12,345 ft"                                                                          */
(function () {
  "use strict";
  var SETS = {
    imperial: {alt: [3.28084, "ft"], spd: [2.236936, "mph"], vspd: [196.85, "ft/min"], dist: [0.621371, "mi"], nm: [1.150779, "mi"], press: [0.02953, "inHg"], rain: [0.03937, "in"], temp: "F"},
    aero: {alt: [3.28084, "ft"], spd: [1.943844, "kt"], vspd: [196.85, "ft/min"], dist: [0.539957, "nm"], nm: [1, "nm"], press: [1, "hPa"], rain: [0.03937, "in"], temp: "C"},
    metric: {alt: [1, "m"], spd: [3.6, "km/h"], vspd: [1, "m/s"], dist: [1, "km"], nm: [1.852, "km"], press: [1, "hPa"], rain: [1, "mm"], temp: "C"}
  };
  function standard() { var u = (window.PREFS && window.PREFS.units) || window.SITE_UNITS || "imperial"; return SETS[u] ? u : "imperial"; }
  function system(area) {
    var a = area || window.ARK_AREA || "", P = window.PREFS || {};
    if (a && SETS[P["u_" + a]]) return P["u_" + a];      // their own choice for the service
    if (SETS[P.units]) return P.units;                    // their overall choice
    var d = a ? (window.SITE_UNITS_BY || {})[a] : "";     // the site's default for the service: its industry's own units
    if (SETS[d]) return d;
    return standard();
  }
  function conv(kind, x, area) { var s = SETS[system(area)][kind]; return x == null ? {v: null, u: s[1]} : {v: x * s[0], u: s[1]}; }
  function temp(c, area) { var f = SETS[system(area)].temp === "F"; return {v: c == null ? null : (f ? c * 9 / 5 + 32 : c), u: f ? "°F" : "°C"}; }
  function fmt(o, d) { if (o.v == null) return "-"; return (d ? o.v.toFixed(d) : Math.round(o.v).toLocaleString()) + " " + o.u; }
  window.ArkUnits = {
    system: system, sets: SETS, alt: function (m) { return conv("alt", m); }, spd: function (ms) { return conv("spd", ms); }, vspd: function (ms) { return conv("vspd", ms); },
    dist: function (km) { return conv("dist", km); }, nm: function (n) { return conv("nm", n); }, temp: temp,
    text: {alt: function (m, d) { return fmt(conv("alt", m), d); }, spd: function (ms, d) { return fmt(conv("spd", ms), d); }, vspd: function (ms, d) { return fmt(conv("vspd", ms), d); },
           dist: function (km, d) { return fmt(conv("dist", km), d === undefined ? (km != null && km < 10 ? 1 : 0) : d); }, temp: function (c, d) { return fmt(temp(c), d); }, nm: function (n, d) { return fmt(conv("nm", n), d === undefined ? 0 : d); },
           press: function (hpa, d) { return fmt(conv("press", hpa), d === undefined ? (system() === "imperial" ? 2 : 0) : d); }, rain: function (mm, d) { return fmt(conv("rain", mm), d === undefined ? (system() === "metric" ? 1 : 2) : d); }},
    // values as the aircraft feed gives them (feet, knots, feet per minute, feet again for airports)
    avia: {alt: function (ft, d) { return fmt(conv("alt", ft == null ? null : ft * 0.3048), d); }, spd: function (kt, d) { return fmt(conv("spd", kt == null ? null : kt * 0.514444), d); },
           vspd: function (fpm, d) { return fmt(conv("vspd", fpm == null ? null : fpm * 0.00508), d); }},
    // the unit set for one service: a function set like ArkUnits itself but for that service, whatever page this is (the home page's radiosonde card shows balloon figures in the balloon units)
    forArea: function (area) {
      return {system: function () { return system(area); }, text: {alt: function (m, d) { return fmt(conv("alt", m, area), d); }, spd: function (ms, d) { return fmt(conv("spd", ms, area), d); }, vspd: function (ms, d) { return fmt(conv("vspd", ms, area), d); },
              dist: function (km, d) { return fmt(conv("dist", km, area), d === undefined ? (km != null && km < 10 ? 1 : 0) : d); }, temp: function (c, d) { return fmt(temp(c, area), d); }}};
    },
    area: function () { return window.ARK_AREA || ""; }, standard: standard,
    // saving: this page's service when it has one, else the overall (standard) choice
    save: function (u) { var a = window.ARK_AREA || "", c = {}; c[a ? "u_" + a : "units"] = u; if (window.adsbSavePrefs) window.adsbSavePrefs(c); else { window.PREFS = Object.assign({}, window.PREFS || {}, c); } },
    saveFor: function (area, u) { var c = {}; c[area ? "u_" + area : "units"] = u; if (window.adsbSavePrefs) window.adsbSavePrefs(c); else { window.PREFS = Object.assign({}, window.PREFS || {}, c); } }
  };
})();
