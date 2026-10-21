/* Live member search for the administrator's add/assign forms.
   <input data-memberpicker> looks members up as you type (user name, display name, call sign or email) and fills the box with the chosen user name.
   It fires "memberpicked" (detail = the member with their feeders) on the input when one is chosen, and "memberpicked" with detail null when the box is edited again. */
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function setup(inp) {
    if (inp.dataset.mpReady) return;
    inp.dataset.mpReady = "1";
    inp.setAttribute("autocomplete", "off");
    var wrap = document.createElement("div");
    wrap.style.cssText = "position:relative;display:inline-block";
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    var box = document.createElement("div");
    box.setAttribute("role", "listbox");
    box.style.cssText = "display:none;position:absolute;left:0;top:100%;z-index:50;min-width:100%;max-width:420px;max-height:300px;overflow:auto;background:var(--panel,#fff);border:1px solid var(--line,#888);border-radius:8px;box-shadow:0 6px 18px rgba(0,0,0,.25)";
    wrap.appendChild(box);
    var note = document.createElement("div");
    note.className = "help";
    wrap.parentNode.insertBefore(note, wrap.nextSibling);
    var items = [], hi = -1, timer = null, seq = 0, picked = null;
    function hide() { box.style.display = "none"; hi = -1; }
    function paint() {
      box.innerHTML = items.map(function (m, i) {
        var l2 = [m.display_name, m.callsign, m.how === "email" ? "found by email address" : "", m.friend ? "friend" : "", m.email].filter(Boolean).join(" · ");
        return '<div role="option" data-i="' + i + '" style="padding:6px 10px;cursor:pointer;' + (i === hi ? "background:var(--acc2,#2563eb);color:#fff" : "") + '"><b>' + esc(m.username) + '</b>' + (l2 ? '<br><span style="font-size:.8rem;opacity:.75">' + esc(l2) + '</span>' : "") + '</div>';
      }).join("") || '<div style="padding:6px 10px;opacity:.7">No member matches.</div>';
      box.style.display = "";
    }
    function pick(i) {
      var m = items[i];
      if (!m) return;
      picked = m;
      inp.value = inp.dataset.multi ? (inp.value.match(/^.*[,;]\s*/) || [""])[0] + m.username + ", " : m.username;
      hide();
      note.textContent = inp.dataset.multi ? "" : [m.display_name, m.callsign, m.email].filter(Boolean).join(" · ") + ((m.feeders || []).length ? " · " + m.feeders.length + " feeder" + (m.feeders.length > 1 ? "s" : "") : "");
      inp.dispatchEvent(new CustomEvent("memberpicked", {detail: m, bubbles: true}));
    }
    function lookup() {
      var q = (inp.dataset.multi ? inp.value.split(/[,;]/).pop() : inp.value).trim().replace(/^@/, ""), my = ++seq;
      if (!q) { items = []; hide(); return; }
      fetch((inp.dataset.memberpicker === "people" ? "/people/search.json?q=" : "/admin/members/search.json?q=") + encodeURIComponent(q), {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (j) {
        if (my !== seq) return;
        items = j.members || [];
        if (j.limited) { note.textContent = "Too many searches for a minute: wait a little."; }
        hi = items.length ? 0 : -1;
        paint();
        // an exact user name or email typed in full counts as chosen
        var exact = items.filter(function (m) { return m.username.toLowerCase() === q.toLowerCase() || (m.email && m.email.toLowerCase() === q.toLowerCase()); });
        if (exact.length === 1 && !picked && !inp.dataset.multi) { items = exact; pick(0); }
      }).catch(function () { hide(); });
    }
    inp.addEventListener("input", function () {
      if (picked) { picked = null; note.textContent = ""; inp.dispatchEvent(new CustomEvent("memberpicked", {detail: null, bubbles: true})); }
      clearTimeout(timer); timer = setTimeout(lookup, 150);
    });
    inp.addEventListener("keydown", function (e) {
      if (box.style.display === "none") return;
      if (e.key === "ArrowDown") { hi = Math.min(items.length - 1, hi + 1); paint(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { hi = Math.max(0, hi - 1); paint(); e.preventDefault(); }
      else if (e.key === "Enter" && hi >= 0) { pick(hi); e.preventDefault(); }
      else if (e.key === "Escape") { hide(); }
    });
    box.addEventListener("mousedown", function (e) { var el = e.target.closest("[data-i]"); if (el) { e.preventDefault(); pick(+el.dataset.i); } });
    inp.addEventListener("blur", function () { setTimeout(hide, 120); });
    inp.addEventListener("focus", function () { if (inp.value.trim() && !picked) lookup(); });
    if (inp.value.trim()) lookup();
  }
  function init() { document.querySelectorAll("input[data-memberpicker]").forEach(setup); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
