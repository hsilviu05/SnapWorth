#!/usr/bin/env python3
"""Generate SnapWorth 1.1 Instagram posts — 3 compact single-image posts
(1080x1350) using the app's real fonts (Fraunces + DM Sans) and real products.
Run with the local venv python (has segno for a real QR):
    marketing/ig/.venv/bin/python marketing/ig/build.py"""
import base64, pathlib
import segno

ROOT = pathlib.Path(__file__).resolve().parents[2]
FONTS = ROOT / "website" / "fonts"
OUT = pathlib.Path(__file__).resolve().parent / "html"
OUT.mkdir(parents=True, exist_ok=True)

APP_STORE_URL = "https://apps.apple.com/app/id6788521307"

def b64(name): return base64.b64encode((FONTS / name).read_bytes()).decode()
FR, FRI, DM = b64("fraunces-latin.woff2"), b64("fraunces-italic-latin.woff2"), b64("dmsans-latin.woff2")

# ── Brand tokens (from website/index.html) ──
DARK="#1C1410"; DARK2="#2B211C"; CREAM="#FAF7F4"; CARD="#F3EDE8"
TERRA="#D96C47"; TERRA_T="#A94E29"; SAGE="#7A9E7E"; SAGE_T="#4F7355"
MUTED="#726860"; BORDER="#E8DDD7"

HEAD = f"""<!doctype html><html><head><meta charset='utf-8'><style>
@font-face{{font-family:'Fraunces';font-weight:400 700;font-style:normal;src:url(data:font/woff2;base64,{FR}) format('woff2');}}
@font-face{{font-family:'Fraunces';font-weight:400 700;font-style:italic;src:url(data:font/woff2;base64,{FRI}) format('woff2');}}
@font-face{{font-family:'DM Sans';font-weight:400 700;font-style:normal;src:url(data:font/woff2;base64,{DM}) format('woff2');}}
*{{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}}
html,body{{width:1080px;height:1350px;overflow:hidden;}}
.slide{{width:1080px;height:1350px;position:relative;padding:90px 84px;display:flex;flex-direction:column;}}
.pill{{display:inline-flex;align-items:center;gap:14px;align-self:flex-start;padding:15px 28px;border-radius:100px;font-family:'DM Sans';font-weight:600;font-size:26px;letter-spacing:.5px;}}
.pill .dot{{width:13px;height:13px;border-radius:50%;background:{TERRA};}}
h1{{font-family:'Fraunces';font-weight:600;line-height:1.02;letter-spacing:-2px;}}
h1 .i{{font-style:italic;color:{TERRA};}}
.sub{{font-family:'DM Sans';font-weight:400;line-height:1.4;}}
.wm{{position:absolute;left:0;bottom:56px;width:1080px;text-align:center;font-family:'DM Sans';font-weight:600;font-size:25px;letter-spacing:7px;text-transform:uppercase;}}
.spacer{{flex:1;}}
.card{{border-radius:26px;padding:28px;}}
.srow{{display:flex;justify-content:space-between;align-items:center;}}
.phone{{border-radius:56px;padding:24px;box-shadow:0 46px 110px rgba(0,0,0,.32);}}
.screen{{border-radius:38px;overflow:hidden;padding:32px 28px;}}
</style></head><body>"""

def page(name, html): (OUT / f"{name}.html").write_text(HEAD + html + "</body></html>")

def qr(url=APP_STORE_URL, size=150, border=1, dark=DARK):
    """Real, scannable QR as inline SVG rects."""
    q = segno.make(url, error='m')
    m = list(q.matrix)
    n = len(m)
    cells = n + 2 * border
    c = size / cells
    rects = ""
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                rects += f"<rect x='{(x+border)*c:.2f}' y='{(y+border)*c:.2f}' width='{c:.2f}' height='{c:.2f}' fill='{dark}'/>"
    return f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}'><rect width='{size}' height='{size}' fill='#fff'/>{rects}</svg>"

# ══════════════ POST 1 — Announcement (both features, dark) ══════════════
page("post1_whatsnew", f"""
<div class='slide' style='background:radial-gradient(120% 80% at 84% 6%, {DARK2} 0%, {DARK} 60%);'>
  <span class='pill' style='background:{TERRA}26;color:{TERRA};'><span class='dot'></span>SNAPWORTH 1.1 IS HERE</span>
  <h1 style='color:#fff;font-size:96px;margin-top:34px;'>Bigger.<br><span class='i' style='color:{TERRA}'>Better.</span> Flips.</h1>
  <div class='spacer'></div>
  <div class='card' style='background:#ffffff0d;border:1px solid #ffffff1a;margin-bottom:22px;'>
    <div class='srow'><div style='font-family:Fraunces;font-weight:600;font-size:50px;color:#fff;'>Share Cards</div>
      <div style='font-family:Fraunces;font-size:44px;color:{TERRA}'>◔</div></div>
    <p style='font-family:DM Sans;font-size:29px;color:#C0B6AB;margin-top:8px;'>Turn any valuation into a shareable card with a scan-me QR.</p></div>
  <div class='card' style='background:#ffffff0d;border:1px solid #ffffff1a;margin-bottom:40px;'>
    <div class='srow'><div style='font-family:Fraunces;font-weight:600;font-size:50px;color:#fff;'>My Flips</div>
      <div style='font-family:Fraunces;font-size:44px;color:{SAGE}'>◉</div></div>
    <p style='font-family:DM Sans;font-size:29px;color:#C0B6AB;margin-top:8px;'>A profit ledger that tracks what you actually made.</p></div>
  <div class='srow' style='background:{TERRA};border-radius:20px;padding:26px 34px;'>
    <span style='font-family:DM Sans;font-weight:700;font-size:31px;color:#fff;'>Free on the App Store</span>
    <span style='font-family:DM Sans;font-weight:700;font-size:31px;color:#3a1c0f;'>link in bio →</span></div>
  <span class='wm' style='color:#8a7f77;'>SnapWorth</span>
</div>""")

# ══════════════ POST 2 — My Flips dashboard (real products, cream) ══════════════
rows = [
    ("The North Face Nuptse 700","$22","$135","+$113"),
    ("Carhartt Detroit Jacket","$14","$98","+$84"),
    ("Patagonia Synchilla Fleece","$9","$64","+$55"),
    ("Levi's 501 Vintage","$8","$50","+$42"),
]
row_html=""
for n,p,s,pr in rows:
    row_html+=f"""<div class='srow' style='padding:20px 4px;border-top:1px solid {BORDER};'>
      <div><div style='font-family:DM Sans;font-weight:600;font-size:26px;color:{DARK};'>{n}</div>
        <div style='font-family:DM Sans;font-size:21px;color:{MUTED};margin-top:4px;'>paid {p} · sold {s}</div></div>
      <div style='font-family:DM Sans;font-weight:700;font-size:28px;color:{SAGE_T};'>{pr}</div></div>"""

page("post2_myflips", f"""
<div class='slide' style='background:radial-gradient(120% 80% at 20% 0%, #FFFDFB 0%, {CREAM} 60%);'>
  <span class='pill' style='background:{SAGE}1f;color:{SAGE_T};'><span class='dot' style='background:{SAGE}'></span>NEW: MY FLIPS</span>
  <h1 style='color:{DARK};font-size:84px;margin-top:26px;'>Know your <span class='i'>profit.</span></h1>
  <p class='sub' style='color:{MUTED};font-size:31px;margin-top:20px;'>Not just what it's worth — what you actually made.</p>
  <div class='phone' style='background:#fff;border:1px solid {BORDER};margin:40px auto 0;width:620px;'>
    <div class='screen' style='background:{CREAM};'>
      <div class='srow' style='margin-bottom:20px;'>
        <div style='font-family:Fraunces;font-weight:600;font-size:32px;color:{DARK};'>My Flips</div>
        <div style='font-family:DM Sans;font-size:21px;color:{MUTED};'>July</div></div>
      <div class='card' style='background:{SAGE}1f;margin-bottom:12px;'>
        <div style='font-family:DM Sans;font-size:22px;color:{SAGE_T};'>Total profit</div>
        <div style='font-family:Fraunces;font-weight:600;font-size:66px;color:{SAGE_T};line-height:1.1;'>$1,180</div>
        <div style='font-family:DM Sans;font-weight:600;font-size:21px;color:{SAGE};'>+34% this month</div></div>
      {row_html}
    </div></div>
  <span class='wm' style='color:{MUTED};'>SnapWorth</span>
</div>""")

# ══════════════ POST 3 — Share Card (real product + real QR, dark) ══════════════
page("post3_sharecard", f"""
<div class='slide' style='background:radial-gradient(120% 85% at 82% 8%, {DARK2} 0%, {DARK} 62%);'>
  <span class='pill' style='background:{TERRA}26;color:{TERRA};'><span class='dot'></span>NEW: SHARE CARDS</span>
  <h1 style='color:#fff;font-size:88px;margin-top:26px;'>Flex your <span class='i'>finds.</span></h1>
  <p class='sub' style='color:#C0B6AB;font-size:30px;margin-top:18px;'>Share your finds with a scan-me QR baked right in.</p>
  <div class='card' style='background:#fff;width:560px;margin:36px auto 0;padding:0;overflow:hidden;box-shadow:0 46px 110px rgba(0,0,0,.4);'>
    <div style='height:140px;background:linear-gradient(120deg,{SAGE} 0%,{TERRA} 100%);'></div>
    <div style='padding:32px;'>
      <div style='font-family:DM Sans;font-weight:600;font-size:27px;color:{DARK};'>The North Face Nuptse 700</div>
      <div class='srow' style='align-items:flex-end;margin-top:12px;'>
        <div style='font-family:Fraunces;font-weight:600;font-size:60px;color:{TERRA_T};line-height:1;'>$110–$180</div>
        <div style='font-family:DM Sans;font-weight:600;font-size:21px;color:{SAGE_T};padding-bottom:10px;'>High confidence</div></div>
      <div class='srow' style='margin-top:28px;padding-top:24px;border-top:1px solid {BORDER};'>
        <div>{qr()}</div>
        <div style='text-align:right;'>
          <div style='font-family:DM Sans;font-weight:700;font-size:25px;color:{DARK};'>Scan to value<br>yours</div>
          <div style='font-family:DM Sans;font-weight:600;font-size:19px;letter-spacing:4px;color:{MUTED};margin-top:10px;'>SNAPWORTH</div></div></div>
    </div></div>
  <span class='wm' style='color:#8a7f77;'>SnapWorth</span>
</div>""")

for old in OUT.glob("*.html"):
    if old.stem not in {"post1_whatsnew","post2_myflips","post3_sharecard"}: old.unlink()
print("generated 3 posts (real QR ->", APP_STORE_URL, ") ->", OUT)
