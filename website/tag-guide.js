/* Tag the guide: one Tag, two homes. Drives btn.tag; words live in the page. */
(function () {
var D = document, M = Math,
  unit = D.querySelector(".tag-unit"), btn = unit && unit.querySelector(".tag-mascot"), T = btn && btn.tag;
if (!T) return;
function $(s, e) { return (e || D).querySelector(s); }
function rect(e) { return e.getBoundingClientRect(); }
function ga(e, n) { return e.getAttribute(n); }
function cl(v, a, b) { return M.max(a, M.min(b, v)); }
function css(n, v) { dock.style.setProperty("--" + n, v + "px"); }
var P = { preventScroll: true };
function at(e, n, v) { v == null ? e.removeAttribute(n) : e.setAttribute(n, v); }
function on(e, n, f, o) { e.addEventListener(n, f, o); }
function later(f, ms) { return setTimeout(f, ms); }
function get(k) { try { return localStorage.getItem("tag." + k); } catch (e) {} }
function put(k, v) { try { v ? localStorage.setItem("tag." + k, 1) : localStorage.removeItem("tag." + k); } catch (e) {} }
var R = matchMedia("(prefers-reduced-motion: reduce)").matches,
  home = unit.parentNode, dock = $("#tag-dock"), panel = $("#tag-panel"), show = $(".tag-show"),
  mcta = $("#mcta"), nav = $("nav"), dlg = $("[data-mode=tour]", panel),
  labels = [ga(btn, "aria-label"), ga(btn, "data-dock-label")],
  stops = [].slice.call(D.querySelectorAll("[data-tag-tour]")).sort(function (a, b) {
    return ga(a, "data-tag-tour") - ga(b, "data-tag-tour"); }),
  docked = 0, mode = "", tour = -1, heroIn = 1, gone = 0, away = 0, said = 0, fast = 0, sleepy = 0,
  hidden = get("hidden"), path = [], idle, settleT, onSettle, lastY = scrollY, lastT = 0, seen = [];
function to(y) { scrollTo({ top: y, behavior: R ? "auto" : "smooth" }); }
function top(e) { return rect(e).top + scrollY - (nav ? rect(nav).bottom : 0) - 24; }
function act(n) { return $("[data-act=" + n + "]", dlg); }

// Homes
function sync() {
  dock.hidden = !docked;
  dock.classList.toggle("is-out", tour < 0 && !!(hidden || gone || away));
  if (show) show.hidden = !hidden;
}
function move(d) { // FLIP hop
  if (docked === d) return;
  var a = rect(btn), f = D.activeElement, keep = unit.contains(f);
  docked = d; sync();
  (d ? dock : home).appendChild(unit);
  if (keep) f.focus(P); // move drops focus
  at(btn, "data-home", d ? "dock" : "hero"); at(btn, "aria-label", labels[d]);
  at(btn, "aria-controls", d ? "tag-panel" : null); at(btn, "aria-expanded", d ? mode === "menu" : null);
  if (!d && mode === "menu") close();
  var b = rect(btn), x = a.left - b.left, y = a.top - b.top, s = a.height / b.height;
  if (R || !unit.animate) return;
  unit.style.transformOrigin = "0 0";
  unit.animate([{ transform: "translate(" + x + "px," + y + "px) scale(" + s + ")" },
    { transform: "translate(" + x / 2 + "px," + (y / 2 - 70) + "px) scale(" + (s + 1) / 2 + ")" },
    { transform: "none" }], { duration: 560, easing: "ease-in-out" });
  later(T.hop, 420);
}
new IntersectionObserver(function (e) {
  heroIn = e.pop().isIntersecting; if (tour < 0) move(heroIn ? 0 : 1);
}).observe(home);

// Panel: greeting, menu (disclosure), tour (dialog)
function open(m) {
  mode = m; panel.hidden = false;
  [].forEach.call(panel.children, function (x) { x.hidden = ga(x, "data-mode") !== m; });
  if (docked) at(btn, "aria-expanded", m === "menu");
}
function close() { mode = ""; panel.hidden = true; if (docked) at(btn, "aria-expanded", false); }
function chip() {
  var c = D.querySelectorAll(".sd-chip"), i = 0;
  [].forEach.call(c, function (x, j) { if (ga(x, "aria-pressed") == "true") i = j; });
  if (c.length) c[(i + 1) % c.length].click();
}
on(btn, "click", function () { if (docked) mode === "menu" ? close() : open("menu"); });
on(panel, "click", function (e) {
  var a = e.target.closest("[data-act]"), n = a && ga(a, "data-act");
  if (n === "tour") { put("greeted", 1); start(); }
  if (n === "nope") { put("greeted", 1); close(); }
  if (n === "dl") close();
  if (n === "find") { close(); onSettle = chip; to(top($(".hd-try"))); if (R) later(chip, 50); }
  if (n === "hide") { put("hidden", 1); hidden = 1; close(); sync(); if (show) show.focus(P); }
  if (n === "back") step(tour - 1, -1);
  if (n === "next") step(tour + 1, 1);
  if (n === "done") end(tour === path.length - 1, 1);
});
if (show) on(show, "click", function () { put("hidden"); hidden = gone = away = 0; sync(); T.hop(); });
on(D, "click", function (e) { if (mode === "menu" && !unit.contains(e.target)) close(); });
on(D, "keydown", function (e) {
  if (e.key === "Escape") { if (tour >= 0) end(0, 1); else if (mode === "menu") { close(); btn.focus(); } }
});

// Tour, only when asked
function start() { // only stops that exist
  path = stops.filter(function (s) { return s.getClientRects().length; });
  close(); tour = -1; move(1); step(0, 1);
}
function step(i, dir) { // skip missing
  while (path[i] && !path[i].getClientRects().length) i += dir;
  if (i < 0) i = tour;
  if (!path[i]) return end(1, 1);
  if (path[tour]) path[tour].classList.remove("tag-spot");
  tour = i; var e = path[i], last = i === path.length - 1;
  e.classList.add("tag-spot"); to(top(e));
  $("#tag-step").textContent = ga(dlg, "data-step").replace("{n}", i + 1).replace("{total}", path.length);
  $("#tag-tip").textContent = ga(e, "data-tag-tip");
  act("back").hidden = !i; act("next").hidden = last;
  open("tour"); sync();
  act(last ? "done" : "next").focus(P);
  onSettle = aim; if (R) aim();
}
function aim() { // walk under the stop, point at it
  var e = path[tour]; if (!e) return;
  var r = rect(e), b = rect(btn),
    x = innerWidth > 760 ? cl((r.left + r.right - b.width) / 2 - dock.offsetLeft, 0, innerWidth - b.width - 48) : 0,
    go = !R && M.abs(x - (parseFloat(dock.style.getPropertyValue("--dx")) || 0)) > 4;
  css("dx", x);
  if (go) T.walk(1);
  later(function () {
    T.walk(0);
    var c = rect(btn), cx = c.left + c.width / 2; // at its top
    T.point(M.atan2(cx - cl(cx, r.left, r.right), cl(r.top, 0, innerHeight) + 30 - c.top) * 180 / M.PI);
  }, go ? 900 : 0);
}
function end(done, focus) {
  if (tour < 0) return;
  if (path[tour]) path[tour].classList.remove("tag-spot");
  var inside = panel.contains(D.activeElement);
  tour = -1; close(); onSettle = 0; css("dx", 0); T.point();
  if (done) { put("toured", 1); T.spin(); }
  if (focus || inside) btn.focus(P);
  move(heroIn ? 0 : 1); sync();
}

// Scrolling
function wake() {
  if (sleepy) { sleepy = 0; T.face(); T.pace = 1; }
  clearTimeout(idle);
  idle = later(function () { if (tour < 0 && !mode) { sleepy = 1; T.face("blink"); T.pace = 0.4; } }, 20000);
}
function settle() {
  T.walk(0); T.look(0, 0);
  if (fast) { fast = 0; T.face(); T.point(); }
  if (tour >= 0) { var r = rect(path[tour]); // far off: end
    if (r.bottom < -innerHeight / 2 || r.top > innerHeight * 1.5) return end(0, 0); }
  if (onSettle) { var f = onSettle; onSettle = 0; f(); }
  clear();
}
function clear() { // never cover a link
  away = 0;
  if (docked && tour < 0) {
    var r = rect(btn);
    [[0, 0], [1, 0], [0, 1], [1, 1], [.5, .5]].forEach(function (p) {
      D.elementsFromPoint(r.left + 6 + (r.width - 12) * p[0], r.top + 6 + (r.height - 12) * p[1]).forEach(function (e) {
        if (!dock.contains(e) && e.closest("a,button,input,select,textarea,[tabindex]")) away = 1;
      });
    });
  }
  sync();
}
on(window, "scroll", function () {
  wake();
  var t = performance.now(), dy = scrollY - lastY, v = M.abs(dy) / M.max(16, t - lastT);
  lastY = scrollY; lastT = t;
  if (docked && !R && tour < 0) {
    T.walk(1); T.look(0, dy > 0 ? -8 : 8);
    if (v > 2.5 && !fast) { fast = 1; T.face("joy"); T.point(150, -150); }
  }
  clearTimeout(settleT); settleT = later(settle, 160);
}, { passive: true });
["pointermove", "keydown", "touchstart"].forEach(function (n) { on(window, n, wake, { passive: true }); });
on(window, "resize", function () { lift(); tour >= 0 ? aim() : clear(); });

// Tips: once each, 3 a visit
var tips = new IntersectionObserver(function (es) {
  es.forEach(function (e) {
    var i = stops.indexOf(e.target);
    if (!e.isIntersecting || !docked || tour >= 0 || hidden || gone || away || seen[i] || said > 2) return;
    seen[i] = 1; said++;
    T.look(cl((rect(e.target).left + e.target.offsetWidth / 2 - rect(btn).left) / 40, -12, 12), -9);
    T.say(ga(e.target, "data-tag-tip"), 4000);
  });
}, { threshold: 0.5 });
stops.forEach(function (s) { tips.observe(s); });

// Footer: bye, hop out
var foot = $("footer");
if (foot) new IntersectionObserver(function (e) {
  if (!e.pop().isIntersecting) { gone = 0; return sync(); }
  if (!docked || tour >= 0 || hidden || gone) return;
  T.say(ga(dock, "data-bye"), 1600); T.point(-140);
  later(function () { gone = 1; T.point(); T.hop(); sync(); }, R ? 0 : 1300);
}).observe(foot);

// Clear .mcta
function lift() {
  css("lift", mcta && !mcta.hidden && mcta.classList.contains("is-on") ? mcta.offsetHeight + 12 : 0);
  later(clear, 400);
}
if (mcta) new MutationObserver(lift).observe(mcta, { attributes: true });

// Greeting: once; no focus, no announcing
function greet() { later(function () { if (!get("greeted") && !hidden && tour < 0 && !mode) open("greet"); }, 4000); }
D.readyState === "complete" ? greet() : on(window, "load", greet);
lift(); sync(); wake();
})();
