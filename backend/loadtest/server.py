"""The production app with the model call replaced, for `loadtest.run`.

Everything else is the real `main.app`: middleware, auth, quota, rate limits,
image validation, the quality decode, JSON parsing and scoring. Only
`main._model` is swapped for a stand-in that waits as long as Gemini does and
answers with a fixed valuation, so a load test measures this process and
never bills a model call.

Run by `loadtest.run` as `uvicorn loadtest.server:app`, with the Dockerfile's
flags.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
from types import SimpleNamespace
from typing import Any

# Never the developer's `backend/.env`, which is a copy of the production
# config: its REDIS_URL would point this load at production Redis, and its
# Telegram token would page the operator. `main` calls `load_dotenv()` at
# import, so it is disarmed first, as tests/conftest.py does. `loadtest.run`
# passes every setting this process needs explicitly.
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False

import main  # noqa: E402

#: /scan latency measured in production over 7 days: p95 18.9 s, max 21.6 s
#: (backend/Dockerfile). A median of 11 s with sigma 0.33 puts a lognormal's
#: p95 at about 19 s.
_MEDIAN_S = float(os.environ.get("LOADTEST_MODEL_MEDIAN_S", "11"))
_SIGMA = float(os.environ.get("LOADTEST_MODEL_SIGMA", "0.33"))
_CAP_S = float(os.environ.get("LOADTEST_MODEL_CAP_S", "25"))

_VALUATION = json.dumps({
    "item_name": "Patagonia Better Sweater",
    "brand": "Patagonia",
    "category": "clothing",
    "condition_notes": "Good, light pilling",
    "est_value_low_usd": 45.0,
    "est_value_high_usd": 90.0,
    "confidence": "High",
    "listing_title": "Patagonia Better Sweater Fleece",
    "listing_description": "Great used condition.",
})

#: The token counts RUNBOOK §10 costs a scan at, so /costs stays plausible.
_USAGE = SimpleNamespace(prompt_token_count=960, candidates_token_count=800,
                         thoughts_token_count=1450, total_token_count=3210)


class _FakeModel:
    async def generate_content_async(self, contents: Any, **_: Any) -> SimpleNamespace:
        delay = min(_CAP_S, math.exp(random.gauss(math.log(_MEDIAN_S), _SIGMA)))
        await asyncio.sleep(delay)
        return SimpleNamespace(text=_VALUATION, candidates=[], usage_metadata=_USAGE)


main._model = _FakeModel()  # pyright: ignore[reportAttributeAccessIssue]
app = main.app
