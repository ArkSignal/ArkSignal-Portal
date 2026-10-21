/* The Chat page: the list of conversations, the open conversation with its bubbles, new messages arriving without reloading, who is typing, who has seen a message, who is online, starting a chat or a group,
   and the group's controls. Messages are put on the page as text, never as HTML. Needs #chatapp with data-csrf, data-open (the open conversation's id, or empty), data-seconds and data-max. */
(function () {
  var app = document.getElementById("chatapp"); if (!app) return;
  var csrf = app.dataset.csrf, open = parseInt(app.dataset.open, 10) || 0, secs = Math.max(2, parseInt(app.dataset.seconds, 10) || 4), maxLen = parseInt(app.dataset.max, 10) || 500;
  var listEl = document.getElementById("ch-items"), msgsEl = document.getElementById("ch-msgs"), typingEl = document.getElementById("ch-typing"), form = document.getElementById("ch-form"), input = document.getElementById("ch-input"),
      note = document.getElementById("ch-note"), dot = document.getElementById("ch-dot"), seenEl = document.getElementById("ch-seen");
  var last = 0, busy = false, others = [], lastTyping = 0, listBusy = false;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function when(iso) {
    var d = new Date(iso), n = new Date(), t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toDateString() === n.toDateString() ? t : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + t;
  }
  function post(url, data) {
    var f = new FormData(); f.append("csrf_token", csrf); Object.keys(data || {}).forEach(function (k) { f.append(k, data[k]); });
    return fetch(url, { method: "POST", body: f, credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } }).then(function (r) { return r.json(); });
  }
  function say(t) { if (note) note.textContent = t || ""; }

  /* ---- the list */
  function drawList(chats) {
    if (!listEl) return;
    listEl.innerHTML = "";
    chats.forEach(function (c) {
      var a = el("a", "ch-item" + (c.id === open ? " on" : "") + (c.unread ? " unread" : "")); a.href = "/chat/" + c.id;
      var top = el("div", "ch-top"); var nm = el("b", "", c.title);
      if (c.kind === "direct") { var s = el("span", "dotl" + (c.online ? " on" : "")); s.title = c.online ? "Online" : "Offline"; top.appendChild(s); } else { top.appendChild(el("span", "grp", "\u{1F465}")); }
      top.appendChild(nm);
      if (c.unread) top.appendChild(el("span", "msgbadge", String(c.unread)));
      top.appendChild(el("time", "dim", when(c.at)));
      a.appendChild(top); a.appendChild(el("div", "dim ch-last", (c.mine ? "You: " : "") + (c.last || "No messages yet")));
      listEl.appendChild(a);
    });
    var none = document.getElementById("ch-none"); if (none) none.style.display = chats.length ? "none" : "";
  }
  function loadList() {
    if (listBusy || document.hidden) return; listBusy = true;
    fetch("/chat/list.json", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) { drawList(d.chats || []); }).catch(function () {}).then(function () { listBusy = false; });
  }

  /* ---- the open conversation */
  function render(m) {
    if (msgsEl.querySelector('[data-id="' + m.id + '"]')) return;
    var row = el("div", "bub " + (m.mine ? "mine" : "theirs")); row.setAttribute("data-id", m.id); row.setAttribute("data-mine", m.mine ? "1" : "0");
    if (!m.mine) row.appendChild(el("div", "who", m.name + (m.via ? " (via " + m.via + ")" : "")));
    row.appendChild(el("div", "cbody", m.body));
    var meta = el("div", "meta"); meta.appendChild(el("time", "dim", when(m.at)));
    if (m.del_url) { var del = el("button", "linkbtn", "Delete"); del.type = "button"; del.addEventListener("click", function () { if (confirm("Delete this message for everyone?")) post(m.del_url).then(function () { row.remove(); }); }); meta.appendChild(del); }
    if (m.report_url) { var rep = el("button", "linkbtn", "Report"); rep.type = "button"; rep.addEventListener("click", function () { var why = prompt("Why are you reporting this message? (optional)"); if (why === null) return; post(m.report_url, { note: why }).then(function (d) { say(d.message); }); }); meta.appendChild(rep); }
    row.appendChild(meta); msgsEl.appendChild(row);
  }
  function drawSeen() {
    if (!seenEl) return;
    var mine = msgsEl.querySelectorAll('.bub.mine'); if (!mine.length || !others.length) { seenEl.textContent = ""; return; }
    var id = parseInt(mine[mine.length - 1].getAttribute("data-id"), 10);
    var seen = others.filter(function (o) { return o.read >= id; });
    seenEl.textContent = seen.length === 0 ? "Sent" : (seen.length === others.length ? (others.length === 1 ? "Seen" : "Seen by everyone") : "Seen by " + seen.map(function (o) { return o.name; }).join(", "));
  }
  function poll() {
    if (!open || busy || document.hidden) return; busy = true;
    fetch("/chat/" + open + "/poll?after=" + last, { credentials: "same-origin" }).then(function (r) { if (r.status === 404) { location.href = "/chat/"; throw 0; } return r.json(); }).then(function (d) {
      var near = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 90 || !last;
      (d.messages || []).forEach(render); (d.gone || []).forEach(function (id) { var x = msgsEl.querySelector('[data-id="' + id + '"]'); if (x) x.remove(); });
      last = d.last || last; others = d.others || [];
      var empty = document.getElementById("ch-empty"); if (empty) empty.style.display = msgsEl.children.length ? "none" : "";
      if (typingEl) typingEl.textContent = (d.typing && d.typing.length) ? d.typing.join(", ") + (d.typing.length === 1 ? " is typing..." : " are typing...") : "";
      if (dot && others.length === 1) { dot.className = "dotl" + (others[0].online ? " on" : ""); dot.title = others[0].online ? "Online" : "Offline"; }
      var on = document.getElementById("ch-online"); if (on && others.length > 1) on.textContent = others.filter(function (o) { return o.online; }).length + " of " + others.length + " online";
      drawSeen();
      if (near && (d.messages || []).length) msgsEl.scrollTop = msgsEl.scrollHeight;
    }).catch(function () {}).then(function () { busy = false; });
  }
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault(); var t = input.value.trim(); if (!t) return;
      post("/chat/" + open + "/send", { body: t }).then(function (d) { if (d.ok) { input.value = ""; say(d.note || ""); poll(); loadList(); } else say(d.message || "Could not send."); }).catch(function () { say("Could not send."); });
    });
    input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
    input.addEventListener("input", function () { var n = Date.now(); if (n - lastTyping > 3000 && input.value) { lastTyping = n; post("/chat/" + open + "/typing"); } });
  }

  /* ---- starting chats and the group's controls */
  var nf = document.getElementById("ch-new");
  if (nf) nf.addEventListener("submit", function (e) {
    e.preventDefault();
    var names = [];
    Array.prototype.forEach.call(nf.querySelectorAll("input[name=friend]:checked"), function (c) { names.push(c.value); });
    if (nf.to.value.trim()) names.push(nf.to.value.trim());
    post("/chat/new", { to: names.join(","), name: nf.name ? nf.name.value : "", body: nf.body.value }).then(function (d) { if (d.ok) location.href = d.url; else document.getElementById("ch-newnote").textContent = d.message || "Could not start the chat."; });
  });
  function wire(id, fn) { var b = document.getElementById(id); if (b) b.addEventListener("click", fn); }
  wire("ch-close", function () { post("/chat/" + open + "/close").then(function (d) { location.href = d.url || "/chat/"; }); });
  wire("ch-leave", function () { if (confirm("Leave this group?")) post("/chat/" + open + "/leave").then(function (d) { location.href = d.url || "/chat/"; }); });
  wire("ch-block", function () { var u = this.getAttribute("data-user"); if (confirm("Block " + u + "? They can no longer chat, message or send you requests, and they are not told.")) post("/my/friends/" + encodeURIComponent(u) + "/block").then(function (d) { say(d.message); setTimeout(function () { location.href = "/chat/"; }, 1200); }); });
  var rn = document.getElementById("ch-rename"); if (rn) rn.addEventListener("submit", function (e) { e.preventDefault(); post("/chat/" + open + "/rename", { name: rn.name.value }).then(function (d) { say(d.message); if (d.ok) setTimeout(function () { location.reload(); }, 600); }); });
  var ad = document.getElementById("ch-add"); if (ad) ad.addEventListener("submit", function (e) { e.preventDefault(); post("/chat/" + open + "/add", { to: ad.to.value }).then(function (d) { say(d.message); if (d.ok) setTimeout(function () { location.reload(); }, 600); }); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-remove]"), function (b) { b.addEventListener("click", function () { var u = b.getAttribute("data-remove"); if (confirm("Remove " + u + " from the group?")) post("/chat/" + open + "/remove", { to: u }).then(function (d) { say(d.message); if (d.ok) setTimeout(function () { location.reload(); }, 600); }); }); });

  loadList(); poll();
  setInterval(poll, secs * 1000); setInterval(loadList, Math.max(8, secs * 2) * 1000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { poll(); loadList(); } });
})();
