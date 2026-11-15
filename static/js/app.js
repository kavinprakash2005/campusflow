/* CampusFlow front-end behaviour. No inline scripts anywhere (strict CSP). */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* mobile sidebar */
  $$("[data-nav-toggle]").forEach(function (b) {
    b.addEventListener("click", function () { document.body.classList.toggle("nav-open"); });
  });
  var bd = $(".cf-backdrop");
  if (bd) bd.addEventListener("click", function () { document.body.classList.remove("nav-open"); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") document.body.classList.remove("nav-open"); });

  /* show / hide password */
  $$("[data-toggle-password]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.getAttribute("data-toggle-password"));
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-pressed", String(show));
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      var i = $("i", btn);
      if (i) i.className = show ? "bi bi-eye-slash" : "bi bi-eye";
    });
  });

  /* demo account chips */
  $$("[data-fill-id]").forEach(function (c) {
    c.addEventListener("click", function () {
      $("#identifier").value = c.getAttribute("data-fill-id");
      $("#password").value = c.getAttribute("data-fill-pw");
      $("#password").focus();
    });
  });

  /* confirm dialogs */
  $$("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault(); });
  });

  /* print / copy */
  $$("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });
  $$("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var text = b.getAttribute("data-copy");
      var done = function () { var o = b.innerHTML; b.innerHTML = '<i class="bi bi-check2"></i> Copied'; setTimeout(function () { b.innerHTML = o; }, 1600); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done); else done();
    });
  });

  /* prevent double submits + HTML5 validation styling */
  $$("form").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!f.checkValidity()) { e.preventDefault(); f.classList.add("was-validated"); var bad = $(":invalid", f); if (bad) bad.focus(); return; }
      $$("button[type=submit][data-busy]", f).forEach(function (b) {
        setTimeout(function () { b.disabled = true; b.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>' + (b.getAttribute("data-busy") || "Working…"); }, 0);
      });
    });
  });

  /* auto-dismiss flash messages */
  $$(".toast-zone .alert").forEach(function (a) {
    setTimeout(function () { a.classList.remove("show"); setTimeout(function () { a.remove(); }, 300); }, 6500);
  });

  /* character counters */
  $$("[data-counter]").forEach(function (el) {
    var out = document.getElementById(el.getAttribute("data-counter"));
    var max = el.getAttribute("maxlength");
    var upd = function () { out.textContent = el.value.length + (max ? " / " + max : ""); };
    el.addEventListener("input", upd); upd();
  });

  /* dynamic request form -------------------------------------------------- */
  var form = $("[data-request-form]");
  if (form) {
    var tiles = $$("input[name=type]", form);
    var groups = $$("[data-type-group]", form);
    var dateBlocks = $$("[data-dates]:not(input)", form);
    var submit = $("[data-submit]", form);
    var setEnabled = function (root, on) {
      $$("input, select, textarea", root).forEach(function (i) { i.disabled = !on; });
      root.hidden = !on;
    };
    var apply = function (code, mode) {
      groups.forEach(function (g) { setEnabled(g, g.getAttribute("data-type-group") === code); });
      dateBlocks.forEach(function (d) { setEnabled(d, !!code && d.getAttribute("data-dates") === mode); });
      var details = $("[data-common]", form);
      if (details) { details.hidden = !code; $$("input, select, textarea", details).forEach(function (i) { if (i.closest("[data-type-group]")||i.closest("[data-dates]")) return;i.disabled = !code; }); }
      if (submit) submit.disabled = !code;
      var hint = $("[data-type-hint]", form);
      if (hint) hint.hidden = !!code;
    };
    var current = function () { var t = tiles.filter(function (x) { return x.checked; })[0]; return t ? [t.value, t.getAttribute("data-dates")] : ["", ""]; };
    tiles.forEach(function (t) { t.addEventListener("change", function () { var c = current(); apply(c[0], c[1]); updateDays(); }); });
    var c0 = current(); apply(c0[0], c0[1]);

    var s = $("[name=start_date]:not(:disabled)", form);
    function updateDays() {
      var out = $("[data-days]", form);
      if (!out) return;
      var a = $("[data-dates]:not([hidden]) [name=start_date]", form), b = $("[data-dates]:not([hidden]) [name=end_date]", form);
      if (a && b && a.value && b.value) {
        var d = Math.round((new Date(b.value) - new Date(a.value)) / 86400000) + 1;
        out.textContent = d > 0 ? d + (d === 1 ? " day" : " days") : "End date is before start date";
        out.className = "chip " + (d > 0 ? "indigo" : "lock");
      } else { out.textContent = ""; }
      if (a && b && a.value) b.min = a.value;
    }
    $$("[name=start_date], [name=end_date]", form).forEach(function (i) { i.addEventListener("change", updateDays); });
    updateDays();
  }

  /* notification badge polling (every 45 s, only while the tab is visible) */
  var dot = $("[data-notif-dot]");
  if (dot && window.fetch) {
    setInterval(function () {
      if (document.hidden) return;
      fetch(dot.getAttribute("data-url"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { if (!j) return; dot.textContent = j.unread > 99 ? "99+" : j.unread; dot.hidden = !j.unread; })
        .catch(function () {});
    }, 45000);
  }
})();
