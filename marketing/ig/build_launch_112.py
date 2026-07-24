#!/usr/bin/env python3
"""Generate the SnapWorth 1.1.2 launch post (1080x1350) — "3 free scans a day."
Reuses the app fonts + brand tokens and renders to PNG with headless Chrome:

    marketing/ig/.venv/bin/python marketing/ig/build_launch_112.py

Output: marketing/ig/out/launch_112_daily_scans.png
"""
import base64, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
FONTS = ROOT / "website" / "fonts"
HERE = pathlib.Path(__file__).resolve().parent
HTML = HERE / "html"; OUT = HERE / "out"
HTML.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

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
.cta{{display:flex;justify-content:space-between;align-items:center;border-radius:20px;padding:26px 34px;}}
</style></head><body>"""

page = f"""
<div class='slide' style='background:radial-gradient(120% 80% at 84% 6%, {DARK2} 0%, {DARK} 60%);'>
  <span class='pill' style='background:{SAGE}1f;color:#B7CDBA;'><span class='dot' style='background:{SAGE}'></span>JUST UPDATED</span>
  <h1 style='color:#fff;font-size:96px;margin-top:32px;'><span style="font-family:'DM Sans';font-weight:700;letter-spacing:-4px;">3</span> free scans,<br><span class='i'>every day.</span></h1>
  <p class='sub' style='color:#C0B6AB;font-size:31px;margin-top:22px;max-width:840px;'>Scan any thrift find, get its resale value in seconds. Your free scans now refresh every single day — no account, no card.</p>
  <div class='spacer'></div>
  <div class='card' style='background:#ffffff0d;border:1px solid #ffffff1a;margin-bottom:26px;'>
    <div class='srow'>
      <div style='font-family:DM Sans;font-weight:700;font-size:116px;color:{TERRA};line-height:1;'>3</div>
      <div style='text-align:right;'>
        <div style='font-family:DM Sans;font-weight:700;font-size:34px;color:#fff;'>free scans</div>
        <div style='font-family:DM Sans;font-weight:600;font-size:27px;color:{SAGE};margin-top:6px;'>↻ refreshes daily</div>
      </div>
    </div>
  </div>
  <div class='cta' style='background:{TERRA};'>
    <span style='font-family:DM Sans;font-weight:700;font-size:31px;color:#fff;'>Update free on the App Store</span>
    <span style='font-family:DM Sans;font-weight:700;font-size:31px;color:#3a1c0f;'>link in bio →</span>
  </div>
  <span class='wm' style='color:#8a7f77;'>SnapWorth</span>
</div>"""

NAME = "launch_112_daily_scans"
(HTML / f"{NAME}.html").write_text(HEAD + page + "</body></html>")

if not pathlib.Path(CHROME).exists():
    print("HTML written; Chrome not found — export the .html at 1080x1350 manually."); sys.exit(0)
subprocess.run([CHROME,"--headless=new","--disable-gpu","--hide-scrollbars",
    "--force-device-scale-factor=1","--window-size=1080,1350",
    f"--screenshot={OUT / (NAME+'.png')}", (HTML / (NAME+'.html')).as_uri()],
    check=True, capture_output=True)
print("rendered ->", OUT / (NAME+".png"))
"""
"""
