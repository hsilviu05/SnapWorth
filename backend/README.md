# SnapWorth Backend

FastAPI service that accepts an image of a secondhand item and returns AI-powered identification + resale value estimates via the Anthropic API.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `POST` | `/scan` | Identify item + estimate resale value |

### POST /scan

**Request:** `multipart/form-data`
- `file` — image file (JPEG / PNG / WebP / GIF, max 10 MB)
- `x-device-id` header — opaque device identifier for per-device rate limiting

**Response:**
```json
{
  "item_name": "Patagonia Better Sweater 1/4-Zip, Size M",
  "brand": "Patagonia",
  "category": "clothing",
  "condition_notes": "Good — light pilling on cuffs, no stains",
  "est_value_low_usd": 45.0,
  "est_value_high_usd": 90.0,
  "confidence": "High",
  "sold_listings_count": 38,
  "listing_title": "Patagonia Better Sweater Fleece 1/4-Zip Medium Gray",
  "listing_description": "Classic Patagonia Better Sweater in great used condition. Light pilling typical of normal wear, no stains or damage. Ships same day."
}
```

## Local Development

```bash
# 1. Clone and enter the backend directory
cd backend

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 5. Run the server
uvicorn main:app --reload
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## Docker

```bash
docker build -t snapworth-backend .
docker run -p 8000:8000 --env-file .env snapworth-backend
```

## Deploy to Railway

1. Push this repository to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select this repo and set the **root directory** to `/backend`
4. Add environment variable: `ANTHROPIC_API_KEY=sk-ant-...`
5. Railway auto-detects the Dockerfile and builds
6. Copy the generated URL — this is your `Config.baseURL` in the iOS app

## Deploy to Fly.io

```bash
cd backend
fly launch          # follow prompts, name it e.g. snapworth-api
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

## Rate Limiting

Per-device rate limiting is enforced in-memory: 20 scans/hour per `x-device-id`. For production, swap the in-memory `_rate_store` dict for a Redis-backed store (e.g. via `redis-py`).
