"""Does a listing draft claim what its scan cannot show? (#353, step 2)

`/listing` writes a title and description from a scan's fields alone: item,
brand, category, condition and a price range. Nothing in that says the item is
authentic or better than its stated condition, so a draft that says "100%
authentic" or "new with tags" on a "Good" item is a claim the user cannot
stand behind. Marketplaces pull listings for it, and beside Legit check it
reads like a certificate. A regex cannot catch it: drafts are written in
English, German and Chinese, and paraphrase.

So, when enabled, `/listing` asks TypeSafe's Jev the two questions in
`CLAIMS` about each draft, beside the scan it was written from. If either is
flagged the draft's title and description are replaced by the deterministic
listing's, which only restates the scan; the prices and category stay.

Off unless LISTING_CLAIM_CHECK is on *and* TYPESAFE_API_KEY is set, and it
fails open: an error, a timeout or an unanswered question returns the draft
unchanged, so Jev being down never costs a user their listing.
`eval/listing_claims.py` measures the rate with the same questions.

What is sent: the draft's title and description and the scan's item name,
brand, category and condition. Never the photo or anything that identifies
the user or the device. The privacy policy names TypeSafe for this, in
`/privacy` and in the app's `PrivacyPolicy`.
"""

from __future__ import annotations

import os
from typing import Any

from scan import promptsafety
from scan import typesafe

FLAG_ENV = "LISTING_CLAIM_CHECK"

#: At or above this, the draft is taken to make the claim.
FLAG_THRESHOLD = 0.5

#: Jev answers in 70-500 ms; past this the draft goes out unchecked rather
#: than holding the user's listing for a second opinion.
TIMEOUT_S = 1.5

#: The two claims, as Jev questions. `criteria` describes each outcome; the
#: scan the draft was written from travels in `state`, so "better than the
#: stated condition" has something to be measured against.
CLAIMS: dict[str, dict[str, Any]] = {
    "authenticity": {
        "type": "noul",
        "instructions": "Does the listing claim or imply that the item is authentic, "
                        "genuine, original, verified or certainly not a replica?",
        "criteria": {
            "true": "It asserts authenticity, e.g. '100% authentic', 'genuine', "
                    "'original', 'not a fake', 'verified', or the same in another language.",
            "false": "It names the brand or item without vouching that it is genuine.",
        },
    },
    "condition": {
        "type": "noul",
        "instructions": "Does the listing describe the item in better condition than "
                        "the scan's stated condition?",
        "criteria": {
            "true": "It claims more than the stated condition supports, e.g. 'new', "
                    "'brand new', 'new with tags', 'never worn', 'mint' or 'flawless' "
                    "for an item whose stated condition is not New.",
            "false": "It matches the stated condition or describes it less favourably.",
        },
    },
}


def enabled() -> bool:
    flag = os.environ.get(FLAG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    return flag and typesafe.enabled()


def _fenced(value: object, limit: int) -> str:
    return promptsafety.fence(promptsafety.sanitize_text(value, limit))


def build_request(item: str, brand: str, category: str, condition_label: str,
                  title: str, description: str,
                  model: str = typesafe.MODEL) -> dict[str, Any]:
    """One `/v1/systemone` body asking both claims about one draft. Every
    string but the condition label came from a model reading a user's photo,
    so each is sanitised and fenced as `/listing`'s own prompt does."""
    return {
        "model": model,
        "state": {
            "scan": {
                "item": _fenced(item, promptsafety.MAX_ITEM_NAME),
                "brand": _fenced(brand, promptsafety.MAX_BRAND),
                "category": _fenced(category, promptsafety.MAX_CATEGORY),
                "stated_condition": condition_label,
            },
            "listing": {
                "title": _fenced(title, 80),
                "description": _fenced(description, 1200),
            },
        },
        "questions": CLAIMS,
    }


def parse_claims(body: object) -> dict[str, float | None]:
    """Each claim's probability of yes; None for one the body does not answer."""
    answers = body.get("answers") if isinstance(body, dict) else None
    return {name: typesafe.noul(answers, name) for name in CLAIMS}


def flagged(claims: dict[str, float | None]) -> list[str]:
    """The claims at or over the threshold. An unanswered one is not flagged."""
    return [name for name, p in claims.items() if p is not None and p >= FLAG_THRESHOLD]


async def check(item: str, brand: str, category: str, condition_label: str,
                title: str, description: str) -> dict[str, float | None]:
    """Ask both claims about a draft. All None when the call failed or ran
    past `TIMEOUT_S`."""
    body = build_request(item, brand, category, condition_label, title, description)
    return parse_claims(await typesafe.ask(body, TIMEOUT_S, "listing claim check"))
