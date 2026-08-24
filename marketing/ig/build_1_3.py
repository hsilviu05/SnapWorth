#!/usr/bin/env python3
"""Generate the SnapWorth 1.3 Instagram posts (1080x1350) as PNGs.

Source of truth is the design canvas artboards in marketing/ig/dc-1.3/*.dc.html,
so the published canvas and these PNGs cannot drift. This script strips the
Design Component wrapper, substitutes the template holes with their defaults,
swaps the Google Fonts <link> for the real woff2 faces embedded as data URIs
(Google Fonts cannot be embedded by a screenshot renderer, and the fallback
serif is visibly wrong), then renders with headless Chrome.

    python3 marketing/ig/build_1_3.py
"""
import base64, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
FONTS = ROOT / "website" / "fonts"
DC = pathlib.Path(__file__).resolve().parent / "dc-1.3"
HTML = pathlib.Path(__file__).resolve().parent / "html"
OUT = pathlib.Path(__file__).resolve().parent / "out"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

HTML.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

def b64(name): return base64.b64encode((FONTS / name).read_bytes()).decode()
FR, FRI, DM = b64("fraunces-latin.woff2"), b64("fraunces-italic-latin.woff2"), b64("dmsans-latin.woff2")

FACES = f"""<style>
@font-face{{font-family:'Fraunces';font-weight:400 700;font-style:normal;src:url(data:font/woff2;base64,{FR}) format('woff2');}}
@font-face{{font-family:'Fraunces';font-weight:400 700;font-style:italic;src:url(data:font/woff2;base64,{FRI}) format('woff2');}}
@font-face{{font-family:'DM Sans';font-weight:400 700;font-style:normal;src:url(data:font/woff2;base64,{DM}) format('woff2');}}
*{{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}}
html,body{{width:1080px;height:1350px;overflow:hidden;}}
</style>"""

# The canvas exposes one `accent` tweak; the PNGs render its default.
ACCENT = "#D96C47"
HOLES = {
    "{{accent}}": ACCENT,
    "{{accentSoft}}": f"color-mix(in srgb, {ACCENT} 15%, transparent)",
}

POSTS = {
    "Main.dc.html": "post1_13_whatsnew",
    "Portfolio.dc.html": "post2_13_portfolio",
    "Weekly.dc.html": "post3_13_weekly",
}

rendered = []
for artboard, name in POSTS.items():
    src = (DC / artboard).read_text(encoding="utf-8")
    body = re.search(r"<x-dc>(.*?)</x-dc>", src, re.S).group(1)
    body = re.sub(r"<helmet>.*?</helmet>", "", body, flags=re.S)  # drops the Google Fonts link
    for hole, value in HOLES.items():
        body = body.replace(hole, value)
    if "{{" in body:
        sys.exit(f"{artboard}: unsubstituted template hole — {re.findall(r'{{[^}]*}}', body)}")
    page = f"<!doctype html><html><head><meta charset='utf-8'>{FACES}</head><body>{body}</body></html>"
    (HTML / f"{name}.html").write_text(page, encoding="utf-8")

    if not pathlib.Path(CHROME).exists():
        print(f"{name}.html written; Chrome not found — export at 1080x1350 manually.")
        continue
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=1080,1350",
        f"--screenshot={OUT / (name + '.png')}", (HTML / f"{name}.html").as_uri()],
        check=True, capture_output=True)
    rendered.append(OUT / f"{name}.png")

for p in rendered:
    print("rendered ->", p)
