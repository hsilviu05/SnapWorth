/* Tag (hero, 404): frames set CSS vars, build_tag.py's CSS makes transforms.
 * btn.tag: the API tag-guide.js drives. */
(function () {
var D = document, M = Math, PI = M.PI, TWO = 2 * PI,
  REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches,
  FINE = matchMedia("(pointer: fine)").matches;
function each(l, f) { [].forEach.call(l, f); }
function attr(e, n) { return e.getAttribute(n) || ""; }
function mix(a, b) { return a + (b - a) * 0.14; }
function now() { return performance.now(); }

function Tag(btn) {
  var svg = btn.querySelector(".tag-svg"); if (!svg) return;
  var cls = svg.classList, st = svg.style;
  var bubble = btn.parentNode.querySelector(".tag-bubble"),
    price = svg.querySelector(".tag-price"), lab = svg.querySelectorAll(".tag-label tspan"),
    hero = attr(btn, "data-tag") === "hero", lines = attr(btn, "data-lines").split("|"), line = 0,
    rest = hero ? "happy" : "wow", mood = rest,
    phi = 0, goal = 0, onGoal = null, tm = [], raf = 0, last = 0,
    seen = 1, hover = 0, led = 0, ex = 0, ey = 0, tx = 0, ty = 0, lean = 0,
    al = 24, ar = -24, toL = 24, toR = -24,
    hopAt = -1e9, landAt = -1e9, blinkAt = now() + 2500, bEnd = 0, glanceAt = 0, walking = 0, burst = -1e9;

  function face(m) {
    mood = m;
    each(svg.querySelectorAll(".tag-f"), function (g) { g.classList.toggle("is-on", g.classList.contains("tag-f-" + m)); });
  }
  function later(f, ms) { tm.push(setTimeout(f, ms)); }
  function fit(el, text, max, size) {
    el.textContent = text; el.style.fontSize = size + "px";
    var w = el.getComputedTextLength();
    if (w > max) el.style.fontSize = size * max / w + "px";
  }
  function words(text) { // two fitted lines
    var w = text.split(" "), h = M.ceil(w.length / 2);
    fit(lab[0], w.slice(0, h).join(" "), 380, 60); fit(lab[1], w.slice(h).join(" "), 380, 60);
  }
  function say(text, ms) {
    if (!bubble || !text) return;
    bubble.textContent = text; bubble.classList.add("is-on");
    clearTimeout(say.t); say.t = setTimeout(function () { bubble.classList.remove("is-on"); }, ms || 2400);
  }
  function arms(l, r) {
    toL = l; toR = r;
    if (!raf) { st.setProperty("--al", l + "deg"); st.setProperty("--ar", r + "deg"); }
  }
  var api = btn.tag = { pace: 1, say: say, react: react,
    face: function (m) { bEnd = m === "blink" ? 1e15 : 0; face(m || rest); },
    point: function (a, b) { // one arm (by sign), both, or rest
      a == null ? arms(24, -24) : b != null ? arms(a, b) : arms(a > 0 ? a : 24, a < 0 ? a : -24);
    },
    walk: function (on) { walking = on; },
    hop: function () { if (raf) { hopAt = now(); landAt = hopAt + 420; } },
    look: function (x, y) { led = 1; tx = x; ty = y; },
    spin: function () { burst = now(); flipTo(TWO, function () { phi = goal = 0; landAt = now(); }); } };
  function flipTo(target, done) {
    goal = target; onGoal = done;
    if (!raf) { // jump
      phi = goal; onGoal = null;
      var b = M.cos(phi) < 0; cls.toggle("is-back", b); st.setProperty("--sx", b ? -1 : 1);
      if (done) done();
    }
  }
  // wow, hop, flip, hold, flip back; a new find resumes the flip
  function react(text, what) {
    tm.forEach(clearTimeout); tm = []; onGoal = null;
    face("wow"); toL = 150; toR = -150;
    fit(price, text, 380, 124); words(what.toUpperCase());
    var still = raf && !phi; // 0 at rest
    if (still) hopAt = now();
    later(function () {
      flipTo(PI, function () {
        later(function () {
          face("joy");
          flipTo(TWO, function () {
            phi = goal = 0; landAt = now(); toL = 24; toR = -24;
            later(function () { face(rest); }, 1200);
          });
        }, 1800);
      });
    }, still ? 260 : 0);
  }

  function frame(t) {
    raf = requestAnimationFrame(frame);
    var dt = M.min(64, t - (last || t)), s = t / 1000, k, sx = 1, sy = 1,
      hop = M.sin(s * TWO * 0.7 * api.pace) * 7, leg = walking ? M.sin(s * TWO * 2.2) * 16 : 0;
    if (walking) hop -= M.abs(leg);
    last = t;
    k = (t - hopAt) / 420;
    if (k >= 0 && k < 1) { hop -= M.sin(PI * k) * 70; sy += 0.05 * M.sin(PI * k); }
    k = (t - landAt) / 260;
    if (k >= 0 && k < 1) { sy -= 0.09 * M.sin(PI * k); sx += 0.08 * M.sin(PI * k); }
    if (phi !== goal) {
      var step = dt * PI / 380, d = goal - phi;
      phi = M.abs(d) <= step ? goal : phi + step * M.sign(d);
      if (phi === goal && onGoal) { var f = onGoal; onGoal = null; f(); }
    }
    var c = M.cos(phi);
    cls.toggle("is-back", c < 0);
    if (M.abs(c) < 0.02) c = c < 0 ? -0.02 : 0.02;
    if (mood === "happy" && rest === "happy" && t > blinkAt) {
      face("blink"); bEnd = t + 130; blinkAt = t + 2500 + M.random() * 2500;
    }
    if (mood === "blink" && t > bEnd) face(rest);
    if (!led && !hero && t > glanceAt) { // 404 glances
      k = M.random() * TWO; tx = M.cos(k) * 11; ty = M.sin(k) * 7; glanceAt = t + 1200 + M.random() * 1400;
    }
    ex = mix(ex, tx); ey = mix(ey, ty); lean = mix(lean, hover ? tx / 3 : 0);
    al = mix(al, toL); ar = mix(ar, toR);
    st.cssText = "--hop:" + hop + "px;--sx:" + sx * c + ";--sy:" + sy + ";--lean:" + lean +
      "deg;--lx:" + ex + "px;--ly:" + ey + "px;--al:" + al + "deg;--ar:" + ar + "deg;--ll:" + leg + "deg;--lr:" + -leg + "deg;--tw:" +
      ((hover ? 1 + 0.18 * M.abs(M.sin(s * 5)) : 1) + M.max(0, 1 - (t - burst) / 700)) + ";--sh:" + (1 + hop / 240);
  }
  function run() {
    var go = seen && !D.hidden && !REDUCED;
    if (go && !raf) { last = 0; raf = requestAnimationFrame(frame); }
    if (!go && raf) { cancelAnimationFrame(raf); raf = 0; if (phi !== goal) flipTo(goal, onGoal); }
  }

  new IntersectionObserver(function (e) { seen = e.pop().isIntersecting; run(); }).observe(btn); // latest
  D.addEventListener("visibilitychange", run);
  if (FINE && !REDUCED) {
    addEventListener("pointermove", function (e) {
      if (!raf) return;
      var r = svg.getBoundingClientRect(), dx = e.clientX - r.left - r.width / 2,
        dy = e.clientY - r.top - r.height * 0.6, n = M.max(160, M.hypot(dx, dy));
      led = 1; tx = dx / n * 12; ty = dy / n * 9;
    }, { passive: true });
    btn.addEventListener("pointerenter", function () { hover = 1; });
    btn.addEventListener("pointerleave", function () { hover = 0; });
  }

  btn.addEventListener("click", function () {
    if (attr(btn, "data-home") === "dock") return; // the guide's
    say(lines[line++ % lines.length]);
    if (!hero) return react(attr(btn, "data-price"), attr(btn, "data-label"));
    var chips = D.querySelectorAll(".sd-chip"), i = 0;
    each(chips, function (c, j) { if (attr(c, "aria-pressed") == "true") i = j; });
    if (chips.length) chips[(i + 1) % chips.length].click();
  });
  var range = hero && D.getElementById("sd-range");
  if (range) {
    var cap = range.parentNode.querySelector(".app-card-label");
    new MutationObserver(function () {
      react(range.textContent.trim(), cap ? cap.textContent.trim() : "");
    }).observe(range, { childList: true, characterData: true, subtree: true });
  }
  run();
}
each(D.querySelectorAll(".tag-mascot[data-tag]"), Tag);
})();
