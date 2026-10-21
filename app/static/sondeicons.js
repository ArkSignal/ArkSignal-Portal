/* Icons for balloon flights, drawn for ArkSignal (the same ideas SondeHub and auto_rx use: a balloon while it climbs, a parachute once it has burst, the sonde box on the ground).
   SondeIcons.svg(kind, color, size) returns an inline SVG string; kind is climbing, falling, landed, lost, burst, launch, recovered, planned, notfound, station, site, chase, me.
   SondeIcons.marker(kind, color, label, opts) returns the HTML for a Leaflet divIcon: the icon centered on the point (a 34 px target, 44 px on touch screens) and an optional label beside it. */
(function () {
  "use strict";
  function box(inner, size, vb) { return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + (vb || "0 0 32 32") + '" width="' + size + '" height="' + size + '" aria-hidden="true" style="display:block;overflow:visible">' + inner + "</svg>"; }
  var OUT = 'stroke="#0b1422" stroke-width="1.4" stroke-linejoin="round"';
  var PAYLOAD = function (c, y) { return '<rect x="11" y="' + y + '" width="10" height="7" rx="1.6" fill="#e8eef7" ' + OUT + '/><rect x="13" y="' + (y + 2) + '" width="6" height="3" rx="1" fill="' + c + '"/>'; };
  var ICONS = {
    climbing: function (c) {   // a balloon on its string with the sonde below
      return '<ellipse cx="16" cy="11" rx="8.5" ry="10" fill="' + c + '" ' + OUT + '/><path d="M11 6.5c1.2-2.4 3.4-3.3 5.6-3.2" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width="1.6" stroke-linecap="round"/>' +
        '<path d="M14 21l2 2.4 2-2.4z" fill="' + c + '" ' + OUT + '/><path d="M16 23.4V25" stroke="#0b1422" stroke-width="1.2"/>' + PAYLOAD(c, 25);
    },
    falling: function (c) {   // a parachute over the sonde
      return '<path d="M3.5 13C3.5 6 9 2.5 16 2.5S28.5 6 28.5 13c-2-1.6-4-1.6-6.2 0-2.2-1.6-4.2-1.6-6.3 0-2.100-1.6-4.200-1.6-6.300 0-2.200-1.600-4.200-1.600-6.200 0z" fill="' + c + '" ' + OUT + '/>' +
        '<path d="M10 3.200C8 6 7.600 10 8.200 13M16 2.500V13M22 3.200c2 2.800 2.400 6.800 1.800 9.800" fill="none" stroke="#0b1422" stroke-opacity=".5" stroke-width="1"/>' +
        '<path d="M5 13.500L15.200 25M16 13.500V25M27 13.500L16.800 25" stroke="#0b1422" stroke-width="1" fill="none"/>' + PAYLOAD(c, 24);
    },
    landed: function (c) {   // the sonde box on the ground with a check
      return '<ellipse cx="16" cy="25" rx="11" ry="3" fill="#0b1422" fill-opacity=".35"/>' + PAYLOAD(c, 14) + '<path d="M12 12.500L16 17l9-12" transform="translate(0 -2.500) scale(.9) translate(1.5 4)" fill="none" stroke="#3ee6a8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
    },
    lost: function (c) {   // the sonde with a question mark: the signal ended in the air
      return PAYLOAD(c, 17) + '<circle cx="16" cy="9" r="8" fill="#0b1422" fill-opacity=".85" stroke="' + c + '" stroke-width="1.6"/><text x="16" y="13.200" text-anchor="middle" font-size="12" font-weight="800" font-family="Inter,sans-serif" fill="' + c + '">?</text>';
    },
    burst: function (c) {   // a spiky star where the balloon burst
      var p = [], n = 10; for (var i = 0; i < n * 2; i++) { var a = Math.PI * i / n - Math.PI / 2, r = i % 2 ? 6 : 13; p.push((16 + r * Math.cos(a)).toFixed(1) + "," + (16 + r * Math.sin(a)).toFixed(1)); }
      return '<polygon points="' + p.join(" ") + '" fill="' + (c || "#ffb454") + '" ' + OUT + '/><circle cx="16" cy="16" r="3.200" fill="#fff" fill-opacity=".85"/>';
    },
    launch: function (c) {   // a flag on a pole at the launch site
      return '<path d="M9 28V4" stroke="#0b1422" stroke-width="2.600" stroke-linecap="round"/><path d="M9 28V4" stroke="#e8eef7" stroke-width="1.200" stroke-linecap="round"/><path d="M10 5h14l-4 5.500 4 5.500H10z" fill="' + (c || "#4fb3ff") + '" ' + OUT + '/><ellipse cx="9" cy="28.500" rx="5" ry="1.800" fill="#0b1422" fill-opacity=".4"/>';
    },
    recovered: function (c) {   // a check in a green disc
      return '<circle cx="16" cy="16" r="12" fill="#27c48f" ' + OUT + '/><path d="M9.500 16.500l4.200 4.200 8.800-9.200" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
    },
    notfound: function (c) {   // a cross in an orange disc
      return '<circle cx="16" cy="16" r="12" fill="#ff9f43" ' + OUT + '/><path d="M11 11l10 10M21 11L11 21" stroke="#fff" stroke-width="3" stroke-linecap="round"/>';
    },
    planned: function (c) {   // a recovery is planned: a blue disc with a car, so a chaser knows someone is going for it
      return '<circle cx="16" cy="16" r="12" fill="#3b82f6" ' + OUT + '/><path d="M8.500 18.500l1.700-4.600a2 2 0 0 1 1.900-1.300h7.800a2 2 0 0 1 1.900 1.300l1.700 4.600v3.200h-15z" fill="#fff"/><circle cx="12" cy="21.800" r="2" fill="#0b1422" stroke="#fff" stroke-width="1"/><circle cx="20" cy="21.800" r="2" fill="#0b1422" stroke="#fff" stroke-width="1"/><path d="M11.500 14.200h9l1 3h-11z" fill="#3b82f6"/>';
    },
    chase: function (c) {   // another chaser's car
      return '<rect x="3.500" y="12" width="25" height="12" rx="4" fill="' + (c || "#ff8c42") + '" ' + OUT + '/><path d="M9 12l2.200-4.200a2 2 0 0 1 1.800-1.100h6a2 2 0 0 1 1.800 1.100L23 12z" fill="#e8eef7" ' + OUT + '/><circle cx="10" cy="24" r="3" fill="#0b1422" stroke="#e8eef7" stroke-width="1.200"/><circle cx="22" cy="24" r="3" fill="#0b1422" stroke="#e8eef7" stroke-width="1.200"/>';
    },
    me: function (c) {   // where this member is (chase mode): a blue dot with a ring
      return '<circle cx="16" cy="16" r="13" fill="#3b82f6" fill-opacity=".25" stroke="#3b82f6" stroke-width="1.500"/><circle cx="16" cy="16" r="7" fill="#3b82f6" stroke="#fff" stroke-width="2.500"/>';
    },
    feeder: function (c) {   // an ADS-B feeder: a round badge with an airplane, big enough to see at state level
      return '<circle cx="16" cy="16" r="14" fill="' + (c || "#3ee6a8") + '" ' + OUT + '/><path d="M16 5.5l2.2 8.6 8.3 4.4v2.4l-8.3-2.4-.9 5.4 2.9 2.1v1.7L16 26.2l-4.2 1.5V26l2.9-2.1-.9-5.4-8.3 2.4v-2.4l8.3-4.4z" fill="#fff" stroke="#0b1422" stroke-width=".9" stroke-linejoin="round"/>';
    },
    station: function (c) {   // an antenna with its waves
      return '<path d="M16 14v13M11 27h10" stroke="#0b1422" stroke-width="2.800" stroke-linecap="round"/><path d="M16 14v13M11 27h10" stroke="#e8eef7" stroke-width="1.200" stroke-linecap="round"/><circle cx="16" cy="11" r="2.800" fill="' + (c || "#ffb454") + '" ' + OUT + '/>' +
        '<path d="M10 6.500a8.500 8.500 0 0 0 0 9M22 6.500a8.500 8.500 0 0 1 0 9M6 3.500a13 13 0 0 0 0 15M26 3.500a13 13 0 0 1 0 15" fill="none" stroke="' + (c || "#ffb454") + '" stroke-width="1.800" stroke-linecap="round"/>';
    },
    site: function (c) {   // a diamond for a launch site
      return '<path d="M16 3l12 13-12 13L4 16z" fill="' + (c || "#4fb3ff") + '" ' + OUT + '/><circle cx="16" cy="16" r="3.600" fill="#fff"/>';
    }
  };
  function svg(kind, color, size) { var f = ICONS[kind] || ICONS.climbing; return box(f(color || "#3ee6a8"), size || 30); }
  function touch() { try { return window.matchMedia("(pointer: coarse)").matches; } catch (e) { return false; } }
  function marker(kind, color, label, o) {
    o = o || {};
    var size = o.size || (touch() ? 40 : 32), hit = Math.max(size, touch() ? 44 : 34), tip = label ? '<span class="lv-label sm-lab" style="border:2px solid ' + (o.border || color) + ';left:calc(50% + ' + (Math.round(size / 2) + 4) + "px);" + (o.dim ? "opacity:.7;" : "") + '">' + label + "</span>" : "";
    // a seasonal theme (Halloween pumpkins and skeletons, Christmas ornaments...) swaps the balloons that are in the air for its own symbol, the same one for the same flight; the ring keeps the altitude color
    var glyph = o.serial && (kind === "climbing" || kind === "falling" || kind === "lost") && window.ArkThemes ? ArkThemes.balloon(o.serial) : null;
    var inner = glyph ? '<div style="width:' + size + "px;height:" + size + "px;box-sizing:border-box;border-radius:50%;border:2.5px solid " + color + ';background:rgba(10,14,24,.78);display:flex;align-items:center;justify-content:center;font-size:' + Math.round(size * 0.62) + 'px;line-height:1">' + glyph + "</div>" : svg(kind, color, size);
    return '<div class="sm-mk" style="width:' + hit + "px;height:" + hit + 'px;margin:-' + hit / 2 + "px 0 0 -" + hit / 2 + 'px"><div style="width:' + size + "px;height:" + size + 'px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.6))">' + inner + "</div>" + tip + "</div>";
  }
  if (!document.getElementById("sondeicons-css")) {
    var st = document.createElement("style"); st.id = "sondeicons-css";
    st.textContent = ".sm-mk{display:flex;align-items:center;justify-content:center;position:relative;cursor:pointer}.sm-mk .sm-lab{position:absolute;left:calc(50% + 14px);top:50%;transform:translateY(-50%);display:inline-block;pointer-events:none}.sm-mk:hover .sm-lab{z-index:5}";
    document.head.appendChild(st);
  }
  window.SondeIcons = {svg: svg, marker: marker, touch: touch};
})();
