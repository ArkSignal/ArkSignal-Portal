/* The numbers beside Chat, Messages and Friends in the menu: asks the site every few seconds (the interval comes from the site's settings) while the page is in view. Asking also tells the site this member
   is online. */
(function () {
  var timer = null;
  function show(el, n) { if (!el) return; el.textContent = n; el.style.display = n > 0 ? "" : "none"; }
  function tick() {
    if (document.hidden) return schedule(30);
    fetch("/chat/unread.json", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) return schedule(60);
      show(document.getElementById("nb-chat"), d.chat); show(document.getElementById("nb-mail"), d.mail);
      Array.prototype.forEach.call(document.querySelectorAll("[data-badge=friends]"), function (e) { show(e, d.friends); });
      show(document.getElementById("nb-me"), (d.chat || 0) + (d.mail || 0) + (d.friends || 0));   // the flag on the profile menu: everything new, added up
      schedule(d.seconds || 30);
    }).catch(function () { schedule(60); });
  }
  function schedule(s) { clearTimeout(timer); timer = setTimeout(tick, s * 1000); }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
  schedule(5);
})();
