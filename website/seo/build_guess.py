#!/usr/bin/env python3
"""Builds /guess — the price-guessing game, on the web.

Why this exists
---------------
The app is iPhone-only. Every link posted to Reddit, X or TikTok is a dead end
for everyone on Android or desktop, which is most of the people who see it. The
one mechanic in the app that people actually want to share — guess the price,
then reveal — was locked behind an install.

This is that mechanic as a static page. No backend, no scan quota, no model
call, no cost per play: the numbers are the same `ITEMS` dataset the /worth
pages are generated from, so a play is free and the game cannot contradict the
guides it links to.

The funnel it serves: play → find out you are worse at this than you thought →
install the app to do it on your own finds. That order matters. Leading with
the download asks for a commitment before the person has felt the problem.

Honesty rules, same as everywhere else
--------------------------------------
These are typical secondhand ranges for guidance, framed as such. Nothing here
claims sold-listing data or comps, because there is no such source — see
`marketing/x_account_setup.md`'s claims check and `docs/AUDIT-2026-09-07.md`.

Run: python3 website/seo/build_guess.py
"""

from __future__ import annotations

import html
import json
import pathlib
import re

from build_seo import APP_STORE, ITEMS, SITE, STYLE

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ID = "6788521307"


def rounds() -> list[dict]:
    """One question per condition tier, from the same data as /worth.

    Each tier is its own question rather than one per item: "what is a *worn*
    Better Sweater worth" is a genuinely different question from the like-new
    one, and the gap between them is the thing most people get wrong.
    """
    out = []
    for item in ITEMS:
        for label, span in item["conditions"]:
            figures = [int(n) for n in re.findall(r"\d+", span)]
            if len(figures) != 2:
                continue
            low, high = min(figures), max(figures)
            out.append({
                "item": item["name"],
                "cat": item["cat"],
                "slug": item["slug"],
                "condition": label,
                "low": low,
                "high": high,
                # The one line from the guide that explains the number, so a
                # wrong guess teaches something instead of just scoring you.
                "why": item["factors"][0] if item.get("factors") else "",
            })
    return out


PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-itunes-app" content="app-id={app_id}">
<title>Guess the Resale Price — How Good Is Your Thrift Eye?</title>
<meta name="description" content="Ten rounds. Guess what each secondhand item is really worth. Most people overrate anything that looks old and underrate plain workwear — find out if you do.">
<link rel="canonical" href="{site}/guess">
<meta property="og:type" content="website">
<meta property="og:title" content="Guess the Resale Price — how good is your thrift eye?">
<meta property="og:description" content="Ten rounds of secondhand items. Guess what each one is worth. Most people are confidently wrong in the same direction.">
<meta property="og:url" content="{site}/guess">
<meta property="og:image" content="{site}/og-image.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Guess the Resale Price — how good is your thrift eye?">
<meta name="twitter:description" content="Ten rounds of secondhand items. Guess what each one is worth. Most people are confidently wrong in the same direction.">
<meta name="twitter:image" content="{site}/og-image.png">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>{style}
/* ── Game ──────────────────────────────────────────────────────────────── */
.g-wrap{{max-width:560px;margin:0 auto;padding:28px 20px 64px;}}
.g-head{{text-align:center;margin-bottom:26px;}}
.g-head h1{{font-family:'Fraunces',serif;font-weight:900;font-size:clamp(30px,7vw,44px);
  line-height:1.05;letter-spacing:-1px;color:var(--ink);margin-bottom:10px;}}
.g-head p{{color:var(--warm-gray);font-size:15px;line-height:1.55;}}
.g-bar{{display:flex;align-items:center;gap:10px;margin-bottom:18px;}}
.g-track{{flex:1;height:6px;background:var(--border);border-radius:99px;overflow:hidden;}}
.g-fill{{height:100%;background:var(--terra-fill);border-radius:99px;width:0;
  transition:width .35s ease;}}
.g-count{{font-size:12px;font-weight:700;color:var(--warm-gray);
  letter-spacing:.4px;text-transform:uppercase;white-space:nowrap;}}
.g-card{{background:var(--card);border:1px solid var(--border);border-radius:22px;
  padding:26px 22px;}}
.g-cat{{font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
  color:var(--terra-text);margin-bottom:8px;}}
.g-item{{font-family:'Fraunces',serif;font-weight:700;font-size:26px;line-height:1.2;
  color:var(--ink);margin-bottom:6px;}}
.g-cond{{font-size:14px;color:var(--warm-gray);margin-bottom:22px;}}
.g-cond b{{color:var(--ink);font-weight:600;}}
.g-label{{display:block;font-size:13px;font-weight:600;color:var(--warm-gray);
  margin-bottom:8px;}}
.g-input{{display:flex;align-items:center;gap:8px;background:var(--surface);
  border:2px solid var(--border);border-radius:14px;padding:0 14px;
  transition:border-color .15s;}}
.g-input:focus-within{{border-color:var(--terra-text);}}
.g-input span{{font-family:'Fraunces',serif;font-size:22px;font-weight:700;
  color:var(--warm-gray);}}
.g-input input{{flex:1;border:0;outline:0;background:transparent;font-family:'DM Sans',sans-serif;
  font-size:22px;font-weight:600;color:var(--ink);padding:14px 0;min-width:0;}}
.g-btn{{display:block;width:100%;margin-top:14px;background:var(--terra-fill);color:#fff;
  border:0;border-radius:14px;padding:15px;font-family:'DM Sans',sans-serif;
  font-size:16px;font-weight:700;cursor:pointer;min-height:48px;}}
.g-btn:disabled{{opacity:.45;cursor:default;}}
.g-btn.ghost{{background:transparent;color:var(--terra-text);
  border:2px solid var(--border);}}
.g-reveal{{margin-top:20px;padding-top:20px;border-top:1px solid var(--border);}}
.g-verdict{{font-family:'Fraunces',serif;font-size:21px;font-weight:700;
  margin-bottom:6px;}}
.g-hit{{color:var(--sage-text);}} .g-miss{{color:var(--terra-text);}}
.g-actual{{font-size:15px;color:var(--ink);margin-bottom:10px;}}
.g-why{{font-size:14px;line-height:1.55;color:var(--warm-gray);}}
.g-why a{{color:var(--terra-text);font-weight:600;}}
.g-score{{text-align:center;}}
.g-big{{font-family:'Fraunces',serif;font-size:60px;font-weight:900;
  color:var(--terra-text);line-height:1;margin:6px 0 2px;}}
.g-outof{{font-size:14px;color:var(--warm-gray);margin-bottom:16px;}}
.g-verdict-line{{font-size:16px;line-height:1.6;color:var(--ink);margin-bottom:22px;}}
.g-cta{{background:var(--card);border:1px solid var(--border);border-radius:18px;
  padding:22px;margin-top:22px;text-align:center;}}
.g-cta h3{{font-family:'Fraunces',serif;font-size:20px;font-weight:700;
  color:var(--ink);margin-bottom:8px;}}
.g-cta p{{font-size:14px;line-height:1.55;color:var(--warm-gray);margin-bottom:16px;}}
.g-cta a{{display:inline-block;background:var(--terra-fill);color:#fff;
  text-decoration:none;font-weight:700;padding:13px 26px;border-radius:12px;
  min-height:44px;line-height:20px;}}
.g-foot{{margin-top:26px;font-size:12.5px;line-height:1.6;color:var(--warm-gray);
  text-align:center;}}
.g-foot a{{color:var(--terra-text);}}
/* Visible to a screen reader and to nobody else. Not `display:none` and not
   `visibility:hidden` — neither is announced. */
.g-sr{{position:absolute;width:1px;height:1px;margin:-1px;padding:0;
  overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);
  white-space:nowrap;border:0;}}
@media (prefers-reduced-motion:reduce){{
  .g-fill{{transition:none;}}
}}
</style>
</head><body>
<div class="g-wrap">
  <header class="g-head">
    <h1>How good is your thrift eye?</h1>
    <p>Ten secondhand items. Guess what each one actually resells for.
       Most people are confidently wrong — and wrong in the same direction.</p>
  </header>

  <main>
    <div class="g-bar">
      <div class="g-track"><div class="g-fill" id="fill"></div></div>
      <div class="g-count" id="count">1 / 10</div>
    </div>

    <div class="g-card" id="card"></div>

    <!-- Outside #card deliberately. The script replaces the card's innerHTML
         on every round, and a live region created in the same paint as the
         text it should announce is unreliable: it has to already be in the
         document when the text arrives. -->
    <div id="g-say" class="g-sr" role="status" aria-live="polite"></div>
  </main>

  <footer>
    <p class="g-foot">
      Ranges are typical US secondhand prices for guidance, not sold-listing data.
      <a href="/worth">See where they come from</a>.
    </p>
  </footer>
</div>

<script id="rounds" type="application/json">{data}</script>
<script>
(function () {{
  var ALL = JSON.parse(document.getElementById('rounds').textContent);
  var ROUNDS = 10, PER = 100;
  var card = document.getElementById('card');
  var fill = document.getElementById('fill');
  var count = document.getElementById('count');
  var deck = [], i = 0, score = 0, hits = 0, high = 0, low = 0;

  function shuffle(a) {{
    a = a.slice();
    for (var n = a.length - 1; n > 0; n--) {{
      var j = Math.floor(Math.random() * (n + 1));
      var t = a[n]; a[n] = a[j]; a[j] = t;
    }}
    return a;
  }}

  // Accepts 12.50 and 12,50 — the same rule the app uses, because a comma is
  // a decimal separator for most of the people who will play this.
  function parse(text) {{
    var kept = (text || '').replace(/[^0-9.,]/g, '');
    if (!/[0-9]/.test(kept)) return null;
    var lc = kept.lastIndexOf(','), ld = kept.lastIndexOf('.');
    var dec = -1;
    if (lc > -1 && ld > -1) dec = Math.max(lc, ld);
    else if (ld > -1) dec = ld;
    else if (lc > -1) dec = (kept.length - lc - 1) === 3 ? -1 : lc;
    var out = '';
    for (var n = 0; n < kept.length; n++) {{
      if (kept[n] >= '0' && kept[n] <= '9') out += kept[n];
      else if (n === dec) out += '.';
    }}
    var v = parseFloat(out);
    return isFinite(v) && v >= 0 ? v : null;
  }}

  function money(v) {{ return '$' + Math.round(v); }}

  function points(guess, r) {{
    if (guess >= r.low && guess <= r.high) return PER;
    var mid = (r.low + r.high) / 2;
    var off = Math.abs(guess - mid) / mid;           // relative, so a $20 item
    return Math.max(0, Math.round(PER * (1 - off))); // isn't easier than a $300 one
  }}

  /* Announce, and move focus.
   *
   * `card.innerHTML = ...` destroys the element the user is on — the very
   * button they just pressed — so focus fell back to the document body ten
   * times a game, the result was never spoken, and a keyboard user had to tab
   * from the top of the page to reach "Next item". */
  function say(message) {{
    var region = document.getElementById('g-say');
    if (!region) return;
    region.textContent = '';   // so an identical string is announced again
    window.setTimeout(function () {{ region.textContent = message; }}, 50);
  }}

  function ask() {{
    var r = deck[i];
    count.textContent = (i + 1) + ' / ' + ROUNDS;
    fill.style.width = (i / ROUNDS * 100) + '%';
    card.innerHTML =
      '<div class="g-cat">' + r.cat + '</div>' +
      '<div class="g-item">' + r.item + '</div>' +
      '<div class="g-cond">Condition: <b>' + r.condition + '</b></div>' +
      '<label class="g-label" for="guess">What does it resell for?</label>' +
      '<div class="g-input"><span>$</span>' +
      '<input id="guess" type="text" inputmode="decimal" autocomplete="off" placeholder="0"></div>' +
      '<button class="g-btn" id="go" disabled>Lock it in</button>';
    var input = document.getElementById('guess'), go = document.getElementById('go');
    input.addEventListener('input', function () {{ go.disabled = parse(input.value) === null; }});
    input.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter' && !go.disabled) go.click();
    }});
    go.addEventListener('click', function () {{ reveal(parse(input.value), r); }});
    input.focus();
  }}

  function reveal(guess, r) {{
    var got = points(guess, r), inside = guess >= r.low && guess <= r.high;
    score += got;
    if (inside) hits++;
    else if (guess > r.high) high++;
    else low++;
    card.innerHTML =
      '<div class="g-cat">' + r.cat + '</div>' +
      '<div class="g-item">' + r.item + '</div>' +
      '<div class="g-cond">Condition: <b>' + r.condition + '</b></div>' +
      '<div class="g-reveal">' +
        '<div class="g-verdict ' + (inside ? 'g-hit' : 'g-miss') + '">' +
          (inside ? 'Inside the range.' :
            guess > r.high ? 'Too high by ' + money(guess - r.high) + '.'
                           : 'Too low by ' + money(r.low - guess) + '.') +
        '</div>' +
        '<div class="g-actual">Typically ' + money(r.low) + '–' + money(r.high) +
          '. You said ' + money(guess) + ' · +' + got + ' pts</div>' +
        (r.why ? '<div class="g-why">' + r.why +
          ' · <a href="/worth/' + r.slug + '">full guide</a></div>' : '') +
      '</div>' +
      '<button class="g-btn" id="next">' +
        (i + 1 >= ROUNDS ? 'See how you did' : 'Next item') + '</button>';
    var next = document.getElementById('next');
    next.addEventListener('click', function () {{
      i++;
      if (i >= ROUNDS) finish(); else ask();
    }});
    say((inside ? 'Inside the range.'
                : guess > r.high ? 'Too high by ' + money(guess - r.high) + '.'
                                 : 'Too low by ' + money(r.low - guess) + '.') +
        ' Typically ' + money(r.low) + ' to ' + money(r.high) +
        '. You said ' + money(guess) + '. Plus ' + got + ' points.');
    next.focus();
  }}

  function finish() {{
    fill.style.width = '100%';
    count.textContent = 'Done';
    var max = ROUNDS * PER;
    var pct = Math.round(score / max * 100);
    var lean = high > low ? 'You lean high — you overrate things that look valuable.'
             : low > high ? 'You lean low — you are leaving money on the shelf.'
             : 'Your misses go both ways, which is rarer than you would think.';
    var band = pct >= 80 ? 'You have done this before.'
             : pct >= 55 ? 'Better than most.'
             : pct >= 30 ? 'Rough, but the instinct is there.'
                         : 'Genuinely bad. Which is the normal result.';
    var share = 'I scored ' + score + '/' + max +
      ' guessing what thrift finds resell for. ' + band + ' Try it:';
    var url = '{site}/guess';
    card.innerHTML =
      '<div class="g-score">' +
        '<div class="g-cat">Your score</div>' +
        '<div class="g-big">' + score + '</div>' +
        '<div class="g-outof">out of ' + max + ' · ' + hits + ' of ' + ROUNDS +
          ' inside the range</div>' +
        '<div class="g-verdict-line"><b>' + band + '</b><br>' + lean + '</div>' +
        '<button class="g-btn" id="share">Share your score</button>' +
        '<button class="g-btn ghost" id="again">Play again</button>' +
      '</div>' +
      '<div class="g-cta">' +
        '<h3>Now do it with something in your hands</h3>' +
        '<p>SnapWorth gives you an AI resale estimate from a photo of the actual ' +
          'item — with the price hidden first, so you can guess. One free scan a ' +
          'day, no account.</p>' +
        '<a href="{app_store}">Get it on the App Store</a>' +
      '</div>';
    document.getElementById('again').addEventListener('click', start);
    say('Game over. You scored ' + score + ' out of ' + max + ', with ' +
        hits + ' of ' + ROUNDS + ' inside the range. ' + band + ' ' + lean);
    document.getElementById('share').focus();
    document.getElementById('share').addEventListener('click', function () {{
      if (navigator.share) {{
        navigator.share({{ text: share, url: url }}).catch(function () {{}});
      }} else {{
        window.open('https://twitter.com/intent/tweet?text=' +
          encodeURIComponent(share) + '&url=' + encodeURIComponent(url),
          '_blank', 'noopener');
      }}
    }});
  }}

  function start() {{
    /* One row per *item*, not ten rows out of forty-eight.
     *
     * ALL is 16 items x 3 condition tiers, so slicing a shuffle of the rows
     * drew with replacement at the item level: the chance of ten distinct
     * items was C(16,10)*3^10 / C(48,10) = 7.2%, so 92.8% of games asked
     * about the same jacket twice — while the page promises "Ten secondhand
     * items". */
    var byItem = {{}};
    ALL.forEach(function (row) {{
      (byItem[row.item] = byItem[row.item] || []).push(row);
    }});
    deck = shuffle(Object.keys(byItem)).slice(0, ROUNDS).map(function (name) {{
      return shuffle(byItem[name])[0];   // one tier, chosen at random
    }});
    /* If the dataset ever carries fewer than ROUNDS distinct items, top up
     * from the spare tiers rather than shortening the game: every count on
     * screen is ROUNDS. */
    if (deck.length < ROUNDS) {{
      var spare = shuffle(ALL.filter(function (row) {{
        return deck.indexOf(row) < 0;
      }}));
      deck = deck.concat(spare.slice(0, ROUNDS - deck.length));
    }}
    i = 0; score = 0; hits = 0; high = 0; low = 0;
    ask();
  }}

  start();
}})();
</script>
</body></html>
"""


def build() -> None:
    data = rounds()
    assert len(data) >= 10, f"only {len(data)} rounds available"
    page = PAGE.format(
        site=SITE,
        app_store=APP_STORE,
        app_id=APP_ID,
        style=STYLE,
        data=html.escape(json.dumps(data, ensure_ascii=False), quote=False),
    )
    (ROOT / "guess.html").write_text(page, encoding="utf-8")
    print(f"Built /guess from {len(data)} rounds across {len(ITEMS)} items "
          f"-> {ROOT / 'guess.html'}")


if __name__ == "__main__":
    build()
