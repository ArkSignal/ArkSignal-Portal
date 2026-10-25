/* More about each flight in a list (the Flights search and Flight History share this, so both show the same things): the airline, the make and model, the owner and the route, asked for from
   /api/flight-extra.json a page of rows at a time and put into the cells of each row. A row is <tr data-x data-hex data-cs data-lat data-lon> with cells .x-airline, .x-model, .x-route.
   ArkFlightExtra.fill(root). The route is the typical route of the flight number, checked against the aircraft's position when there is one; a route that does not fit shows dimmed with a question mark. */
(function () {
  "use strict";
  var done = {}, store = {};   // what was asked for, and what came back (a list that redraws itself gets its cells filled again from here)
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function put(tr, d) {
    var a = tr.querySelector(".x-airline"), m = tr.querySelector(".x-model"), r = tr.querySelector(".x-route");
    if (a) a.textContent = d.airline || (d.owner || "");
    if (m && d.model) m.textContent = d.model;
    if (r) {
      if (d.route && d.route.from && d.route.to && d.route.from.code && d.route.to.code) {
        var dim = d.route.plausible === false;
        r.innerHTML = '<span' + (dim ? ' class="dim"' : "") + ' title="' + esc((d.route.from.city || "") + " to " + (d.route.to.city || "") + (dim ? ". This route does not fit the aircraft's position, so it may belong to another flight." : d.route.source === "callsign" ? ". The typical route for this flight number." : "")) + '"><b>' + esc(d.route.from.code) + "</b> &rarr; <b>" + esc(d.route.to.code) + "</b>" + (dim ? " ?" : "") + "</span>";
      } else r.textContent = "";
    }
  }
  window.ArkFlightExtra = {
    fill: function (root) {
      var rows = Array.prototype.filter.call((root || document).querySelectorAll("tr[data-x]"), function (tr) { var k = tr.dataset.hex + "|" + tr.dataset.cs; if (store[k]) put(tr, store[k]); return !done[k]; });
      for (var i = 0; i < rows.length; i += 40) (function (chunk) {
        chunk.forEach(function (tr) { done[tr.dataset.hex + "|" + tr.dataset.cs] = 1; });
        var items = chunk.map(function (tr) { return [tr.dataset.hex, (tr.dataset.cs || "").replace(/[^A-Za-z0-9]/g, ""), tr.dataset.lat || "", tr.dataset.lon || ""].join(":"); }).join(",");
        fetch("/api/flight-extra.json?items=" + encodeURIComponent(items)).then(function (r) { return r.json(); }).then(function (j) {
          chunk.forEach(function (tr) { if (j[tr.dataset.hex]) { store[tr.dataset.hex + "|" + tr.dataset.cs] = j[tr.dataset.hex]; put(tr, j[tr.dataset.hex]); } });
        }).catch(function () { chunk.forEach(function (tr) { delete done[tr.dataset.hex + "|" + tr.dataset.cs]; }); });
      })(rows.slice(i, i + 40));
    }
  };
})();
