"""Did the scan name the item the label names? A judge for the eval (#352).

`metrics.field_accuracy` and `hallucination_rate`'s brand check compare text
exactly, which fails both ways. "Levi's 501" against a label of "Levi Strauss
501 Original" is scored wrong, and "Nike Air Max 95" against "Nike Air Max 97"
passes a brand check while being a different shoe at a different price —
`comps/matching.py` explains why soft similarity cannot see that.

So the run asks TypeSafe's Jev one yes/no question per labelled item: is the
scan's answer the same product as the label? Jev returns the probability of
yes, and `metrics.identity_match` scores the run from those.

Off unless TYPESAFE_API_KEY is set: the run is then exactly what it was, and
the metric is absent rather than zero. A failed or refused call leaves that
item unjudged, never wrong, so Jev being down cannot fail an eval.

Only gold-set labels and the scan's own text answer are sent — never a photo,
and never a user's scan: the gold set is the operator's own sales.

The wire format is the one `typesafe-sdk` 0.7.5 generates from
api.typesafe.ai's OpenAPI schema (`SystemOneRequest`, `NoulQuestion`,
`NoulAnswer`). It is called with httpx, which the backend already pins, rather
than by adding the SDK and its dependencies for one request shape.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

import promptsafety
from confidence import UNKNOWN_BRANDS, brand_is_known

log = logging.getLogger("snapworth.eval.identity")

# The SDK's own environment names, so a key set up for it works here too.
API_KEY_ENV = "TYPESAFE_API_KEY"
BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
# `jev-latest` moves when TypeSafe ships a build, so the model that actually
# answered is recorded from each response; set TYPESAFE_MODEL to pin one.
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
TIMEOUT_S = 10.0

QUESTION = "same_product"
# At or above this, the scan named the labelled product.
MATCH_THRESHOLD = 0.5
# A model field that says "I don't know", read like an unknown brand.
UNKNOWN_MODELS = UNKNOWN_BRANDS

_INSTRUCTIONS = (
    "Is the item the scan identified the same product as the labelled item? "
    "Wording, word order, abbreviations and a missing size or colour do not make "
    "it a different product. A different model, model number, generation or "
    "brand does."
)
_CRITERIA = {
    "true": "The same product, described differently or in less detail.",
    "false": "A different product, or a different model or version of it.",
}


@dataclass(frozen=True)
class Verdict:
    """One item's judgement. `probability` is None when the call failed."""
    probability: float | None
    model: str | None = None
    input_tokens: int | None = None


def enabled() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def _clean(value: object, limit: int) -> str | None:
    text = promptsafety.sanitize_text(value, limit)
    return text or None


def describe(category: str | None, brand: str | None,
             model: str | None) -> dict[str, str] | None:
    """An item as the judge sees it, or None when it names no product.

    For a label, None means it says only "a jacket": any jacket would match,
    so there is nothing to judge. For a scan, None means it answered
    "Unknown" for both brand and model — it declined to guess, which is
    counted as an abstention, not sent as a question.
    """
    brand_text = _clean(brand, promptsafety.MAX_BRAND) if brand_is_known(brand) else None
    model_text = _clean(model, promptsafety.MAX_ITEM_NAME)
    if model_text and model_text.lower() in UNKNOWN_MODELS:
        model_text = None
    if not brand_text and not model_text:
        return None
    out: dict[str, str] = {}
    category_text = _clean(category, promptsafety.MAX_CATEGORY)
    if category_text:
        out["category"] = category_text
    if brand_text:
        out["brand"] = brand_text
    if model_text:
        out["model"] = model_text
    return out


def build_request(label: dict[str, str], scan: dict[str, str],
                  model: str = MODEL) -> dict[str, Any]:
    """The `/v1/systemone` body. Both descriptions travel as JSON values in
    `state`, apart from the question, and each string is sanitised and fenced
    as `/listing` treats model output: text printed on a photographed object
    reaches the scan's answer."""
    def fenced(fields: dict[str, str]) -> dict[str, str]:
        return {key: promptsafety.fence(value) for key, value in fields.items()}

    return {
        "model": model,
        "state": {"labelled_item": fenced(label), "scan_answer": fenced(scan)},
        "questions": {QUESTION: {"type": "noul", "instructions": _INSTRUCTIONS,
                                 "criteria": _CRITERIA}},
    }


def parse_response(body: object) -> Verdict:
    """The probability of yes from a `/v1/systemone` response, or an unjudged
    verdict when the body is not the shape the schema promises."""
    if not isinstance(body, dict):
        return Verdict(None)
    answers = body.get("answers")
    answer = answers.get(QUESTION) if isinstance(answers, dict) else None
    value = answer.get("noul") if isinstance(answer, dict) else None
    # bool is an int: a `true` here is a malformed body, not probability 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        return Verdict(None)
    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    tokens = usage.get("input_tokens")
    model = body.get("model")
    return Verdict(float(value), model if isinstance(model, str) else None,
                   tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None)


async def judge(client: httpx.AsyncClient, label: dict[str, str],
                scan: dict[str, str]) -> Verdict:
    """Ask once. Any failure — network, status, body — is an unjudged item."""
    try:
        response = await client.post("/v1/systemone", json=build_request(label, scan))
        response.raise_for_status()
        return parse_response(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        # The status and the exception type only: the body could echo the key.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("identity judge failed: %s%s", type(exc).__name__,
                    f" (HTTP {status})" if status else "")
        return Verdict(None)


def make_client(api_key: str | None = None) -> httpx.AsyncClient:
    key = (api_key or os.environ.get(API_KEY_ENV, "")).strip()
    return httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT_S,
                             headers={"Authorization": f"Bearer {key}"})


async def judge_all(pairs: list[tuple[dict[str, str], dict[str, str]]],
                    concurrency: int = 4,
                    client: httpx.AsyncClient | None = None) -> list[Verdict]:
    """One verdict per (label, scan) pair, in order."""
    owned = client is None
    http = client or make_client()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def guarded(label: dict[str, str], scan: dict[str, str]) -> Verdict:
        async with semaphore:
            return await judge(http, label, scan)

    try:
        return list(await asyncio.gather(*(guarded(lbl, scn) for lbl, scn in pairs)))
    finally:
        if owned:
            await http.aclose()
