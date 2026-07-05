"""
SnapWorth Backend — FastAPI + Google Gemini vision API
POST /scan  →  identify item, estimate resale value
GET  /health → liveness check
"""

import base64
import json
import os
import time
from collections import defaultdict

import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

load_dotenv()

genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))

app = FastAPI(title="SnapWorth API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting (in-memory; swap for Redis in production) ──────────────────
_rate_store: dict[str, list[float]] = defaultdict(list)
RATE_WINDOW_SECS = 3600
RATE_MAX_REQUESTS = 20


def _check_rate_limit(device_id: str) -> None:
    device_id = device_id[:64]  # cap length before using as dict key
    now = time.time()
    timestamps = _rate_store[device_id]
    timestamps[:] = [t for t in timestamps if now - t < RATE_WINDOW_SECS]
    if len(timestamps) >= RATE_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: {RATE_MAX_REQUESTS} scans/hour per device.",
        )
    timestamps.append(now)


SCAN_PROMPT = """You are an expert in secondhand and thrift market valuations with deep knowledge of eBay, Poshmark, ThredUp, Depop, and Facebook Marketplace sold listings.

Analyze the provided image of a secondhand or thrift item and return ONLY a valid JSON object — no markdown, no explanation, no extra text.

Required JSON schema:
{
  "item_name": "Specific item name including brand, model, size if visible (e.g. 'Patagonia Better Sweater 1/4-Zip, Size M')",
  "brand": "Brand name, or 'Unknown' if not identifiable",
  "category": "One of: clothing, shoes, accessories, electronics, books, furniture, home, sports, toys, collectibles, other",
  "condition_notes": "Brief honest condition summary (e.g. 'Good — light pilling on cuffs, no stains')",
  "est_value_low_usd": 12.00,
  "est_value_high_usd": 45.00,
  "confidence": "High, Medium, or Low based on how clearly you can identify the item",
  "sold_listings_count": 38,
  "listing_title": "Compelling, SEO-friendly resale title under 80 chars",
  "listing_description": "2-3 sentences highlighting key selling points, condition, and why it's a good buy"
}

Rules:
- Base value estimates ONLY on real, recent sold listings — not asking prices
- If brand is clearly visible, weight values to that brand's specific secondhand market
- sold_listings_count is your estimate of how many comparable sold listings you're basing the range on (use 0 if truly unknown)
- Never return values outside the JSON object"""

_model = genai.GenerativeModel("gemini-2.5-flash")


# ── Response schema ──────────────────────────────────────────────────────────
class ScanResponse(BaseModel):
    item_name: str
    brand: str
    category: str
    condition_notes: str
    est_value_low_usd: float = Field(ge=0)
    est_value_high_usd: float = Field(ge=0)
    confidence: str
    sold_listings_count: int = Field(ge=0, default=0)
    listing_title: str
    listing_description: str


# ── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


_STYLE = """
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       max-width:680px;margin:48px auto;padding:0 24px;color:#2B211C;line-height:1.7}
  h1{font-size:1.8rem;margin-bottom:4px} h2{font-size:1.1rem;margin-top:2rem}
  p,li{font-size:.95rem;color:#5a4a42} a{color:#D96C47}
"""

@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy — SnapWorth</title><style>{_STYLE}</style></head><body>
<h1>Privacy Policy</h1>
<p>Last updated: July 5, 2026</p>
<p>SnapWorth ("we", "our", or "us") operates the SnapWorth mobile application.
This page informs you of our policies regarding the collection, use, and
disclosure of personal data when you use our Service.</p>

<h2>Information We Collect</h2>
<p>We collect photos you submit for valuation. Photos are sent to our server,
processed by an AI model to identify the item and estimate resale value, and
are not stored after the response is returned.</p>
<p>We collect an anonymous device identifier (UUID) solely for rate-limiting
purposes (20 scans per hour). This ID is not linked to your identity.</p>

<h2>How We Use Your Information</h2>
<p>Photos are used only to generate the valuation response you requested.
We do not sell, rent, or share your photos or device identifier with third parties,
except as required by law.</p>

<h2>Data Retention</h2>
<p>Photos and scan results are processed in real time and are not retained on our
servers. Scan history is stored locally on your device and can be deleted at any
time from the app's Settings.</p>

<h2>Children's Privacy</h2>
<p>SnapWorth is not directed to children under 13. We do not knowingly collect
personal information from children under 13.</p>

<h2>Changes to This Policy</h2>
<p>We may update this Privacy Policy from time to time. Changes are effective
when posted on this page.</p>

<h2>Contact</h2>
<p>If you have questions about this Privacy Policy, contact us at
<a href="mailto:silh6767@gmail.com">silh6767@gmail.com</a>.</p>
</body></html>"""


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terms of Service — SnapWorth</title><style>{_STYLE}</style></head><body>
<h1>Terms of Service</h1>
<p>Last updated: July 5, 2026</p>
<p>By downloading or using SnapWorth you agree to these Terms. If you disagree,
please do not use the app.</p>

<h2>Use of Service</h2>
<p>SnapWorth provides AI-generated resale value estimates for informational
purposes only. Estimates are not guarantees of actual sale prices. We are not
responsible for any financial decisions made based on our estimates.</p>

<h2>Subscriptions</h2>
<p>SnapWorth offers auto-renewing subscriptions (weekly and yearly). Subscriptions
are charged to your Apple ID account. You can cancel at any time in your device's
subscription settings. Cancellation takes effect at the end of the current
billing period. A 3-day free trial is available for new subscribers.</p>

<h2>Prohibited Use</h2>
<p>You may not use SnapWorth to submit illegal content, attempt to reverse-engineer
the service, or abuse the rate limits.</p>

<h2>Disclaimer</h2>
<p>THE SERVICE IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND. TO THE MAXIMUM
EXTENT PERMITTED BY LAW, WE DISCLAIM ALL WARRANTIES, EXPRESS OR IMPLIED.</p>

<h2>Contact</h2>
<p>Questions? Email us at
<a href="mailto:silh6767@gmail.com">silh6767@gmail.com</a>.</p>
</body></html>"""


@app.post("/scan", response_model=ScanResponse)
async def scan(
    file: UploadFile = File(...),
    x_device_id: str = Header(default="anonymous", alias="x-device-id"),
) -> ScanResponse:
    _check_rate_limit(x_device_id)

    content_type = file.content_type or "application/octet-stream"
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{content_type}'. Use JPEG, PNG, GIF, or WebP.",
        )

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image exceeds 10 MB limit.")
    if len(image_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty image file.")

    image_part = {"mime_type": content_type, "data": base64.standard_b64encode(image_bytes).decode()}

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = await _model.generate_content_async([SCAN_PROMPT, image_part])
            raw = response.text.strip()
            break
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                await __import__("asyncio").sleep(1.5)
    else:
        raise HTTPException(status_code=502, detail=f"AI service error: {last_exc}")

    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Could not parse AI response as JSON: {exc}")

    return ScanResponse(
        item_name=str(data.get("item_name", "Unknown Item")),
        brand=str(data.get("brand", "Unknown")),
        category=str(data.get("category", "other")),
        condition_notes=str(data.get("condition_notes", "Condition unknown")),
        est_value_low_usd=_safe_float(data.get("est_value_low_usd", 0)),
        est_value_high_usd=_safe_float(data.get("est_value_high_usd", 0)),
        confidence=str(data.get("confidence", "Low")),
        sold_listings_count=int(data.get("sold_listings_count", 0)),
        listing_title=str(data.get("listing_title", "")),
        listing_description=str(data.get("listing_description", "")),
    )


def _safe_float(value: object) -> float:
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
