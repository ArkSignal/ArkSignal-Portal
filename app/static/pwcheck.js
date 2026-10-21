// Sign-up and password forms: a checklist that ticks as the person types, so they see what is required and what is already met.
// Rules come from the page (data attributes written by the server from Admin > Settings); the server still checks everything on submit.
(function () {
  function li(text) { var e = document.createElement("li"); e.textContent = text; return e; }
  function put(list, after) { after.parentNode.insertBefore(list, after.nextSibling); }
  function mark(el, state, text) { el.className = state; if (text) el.textContent = text; }
  function list() { var u = document.createElement("ul"); u.className = "pwreq"; u.setAttribute("aria-live", "polite"); return u; }
  function afterHelp(input) { var n = input.nextElementSibling; return n && n.classList.contains("help") ? n : input; }

  document.querySelectorAll("input[data-pwcheck]").forEach(function (pw) {
    var minlen = parseInt(pw.dataset.minlen, 10) || 0, mix = pw.dataset.mix === "1", conf = pw.dataset.confirm ? document.querySelector(pw.dataset.confirm) : null;
    var ul = list(), rLen = li("At least " + minlen + " characters"), rMix = li("Letters and numbers"), rMatch = li("Both passwords match");
    ul.appendChild(rLen); if (mix) ul.appendChild(rMix); if (conf) ul.appendChild(rMatch);
    put(ul, afterHelp(pw));
    var touched = false, confTouched = false;
    function update() {
      var v = pw.value, lenOk = v.length >= minlen;
      mark(rLen, !v ? "" : lenOk ? "ok" : (touched ? "bad" : ""));
      if (mix) { var mixOk = /[A-Za-z]/.test(v) && /\d/.test(v); mark(rMix, !v ? "" : mixOk ? "ok" : (touched ? "bad" : "")); }
      if (conf) {
        var c = conf.value;
        if (c && c === v) mark(rMatch, "ok", "Passwords match");
        else if (c && (c.length >= v.length || confTouched)) mark(rMatch, "bad", "Passwords do not match");
        else mark(rMatch, "", "Both passwords match");
      }
    }
    pw.addEventListener("input", update); pw.addEventListener("blur", function () { touched = true; update(); });
    if (conf) { conf.addEventListener("input", update); conf.addEventListener("blur", function () { confTouched = true; update(); }); }
    update();
  });

  document.querySelectorAll("input[data-check]").forEach(function (inp) {
    var kind = inp.dataset.check, ul = list(), rules = [];
    if (kind === "username") {
      rules = [[li("3 to 32 characters"), function (v) { return v.length >= 3 && v.length <= 32; }],
               [li("Letters, numbers, dot, dash and underscore only"), function (v) { return /^[A-Za-z0-9_.-]*$/.test(v); }]];
    } else if (kind === "email") {
      rules = [[li("Looks like an email address, for example name@example.com"), function (v) { return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v); }]];
    }
    rules.forEach(function (r) { ul.appendChild(r[0]); });
    put(ul, afterHelp(inp));
    var touched = false;
    function update() { rules.forEach(function (r) { mark(r[0], !inp.value ? "" : r[1](inp.value) ? "ok" : (touched ? "bad" : "")); }); }
    inp.addEventListener("input", update); inp.addEventListener("blur", function () { touched = true; update(); }); update();
  });
})();
