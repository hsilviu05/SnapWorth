"""A stable machine `code` beside an error's `detail`.

Every error body was `{"detail": "<English sentence>"}`, and the sentence was
all a client had. So the app routed on the words: a 402 whose `detail`
contained "pro feature" went to "Pro required", any other 402 to "allowance
spent" (`AppError.from`), and rewording either message on the server would
have silently changed which screen a user saw. And it showed the sentence as
written, in English, inside an app translated into four other languages.

A code is what a client can match on and translate. `detail` stays exactly
what it was — the text installed builds show — and `code` is added beside it,
which an installed build ignores.

**A code, once sent, is contract.** Rewording `detail` is free; renaming a
code is a breaking change for every build that knows it, the same rule as a
response field (`contract/README.md`). A new failure gets a new code.

Only what a client might act on or say differently gets its own code. Two
sentences that mean the same thing to the user share one: `IMAGE_TOO_LARGE`
is both the 10 MB upload cap and the pixel-count check. An `HTTPException`
raised without one — FastAPI's own 404 and 405, and routes outside the app's
four — gets a code from its status (`_BY_STATUS`).

So every `HTTPException`, every request-validation error and the body-size
middleware's 400 and 413 carry a code. What does not is an exception nothing
handles: `ServerErrorMiddleware` answers it with Starlette's plain-text
"Internal Server Error" 500, with no code, which a client has to treat as
unknown.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

# ── /scan, /listing, /trends ─────────────────────────────────────────────────

#: Below the operator's minimum build (`notify.minimum_build`). 426 for a build
#: that says so in `X-SnapWorth-Build`, 422 for one read from the User-Agent.
UPDATE_REQUIRED = "update_required"
#: The day's free allowance is spent (402).
QUOTA_EXHAUSTED = "quota_exhausted"
#: A Pro-only endpoint refused a free caller (402).
PRO_REQUIRED = "pro_required"
#: A per-device or per-IP rate limit (429, with `Retry-After`).
RATE_LIMITED = "rate_limited"
#: The model's safety filter refused the photo (422).
PHOTO_UNUSABLE = "photo_unusable"
#: The model priced the photo at zero on purpose: not something that resells
#: (422). `detail` carries the model's own reason, so it is the one message
#: here that no fixed translation can say in full.
NOT_RESALABLE = "not_resalable"
#: Scanning is paused for a day after repeated safety blocks (422).
DEVICE_PAUSED = "device_paused"
#: The model could not be reached, or ran out of time (502).
AI_UNAVAILABLE = "ai_unavailable"
#: The model answered with something that could not be parsed (502).
AI_UNREADABLE = "ai_unreadable"
#: The model answered without a usable price (502).
AI_NO_PRICE = "ai_no_price"
#: The quota store could not be read, so the scan is refused (503).
QUOTA_UNAVAILABLE = "quota_unavailable"
#: `/listing` was asked for a marketplace it does not know (400).
UNSUPPORTED_MARKETPLACE = "unsupported_marketplace"

# The upload itself (400). `imagevalidation.ImageValidationError.code`.
IMAGE_EMPTY = "image_empty"
IMAGE_TYPE_UNSUPPORTED = "image_type_unsupported"
IMAGE_UNREADABLE = "image_unreadable"
IMAGE_TOO_LARGE = "image_too_large"
IMAGE_TOO_SMALL = "image_too_small"

# ── /auth ────────────────────────────────────────────────────────────────────

#: No bearer token, and the server requires one (401).
AUTH_REQUIRED = "auth_required"
#: The bearer token did not verify: expired, wrong key, malformed (401).
TOKEN_INVALID = "token_invalid"
#: The single-use challenge was unknown, spent or expired (400).
CHALLENGE_INVALID = "challenge_invalid"
#: App Attest is not configured on this deployment (503).
ATTESTATION_NOT_CONFIGURED = "attestation_not_configured"
ATTESTATION_MALFORMED = "attestation_malformed"   # 400
ATTESTATION_REJECTED = "attestation_rejected"     # 401
ASSERTION_MALFORMED = "assertion_malformed"       # 400
ASSERTION_REJECTED = "assertion_rejected"         # 401
#: `/auth/refresh` for a key the server does not hold: attest again (401).
KEY_UNKNOWN = "key_unknown"
#: The attestation store could not be written or read (503).
SIGN_IN_UNAVAILABLE = "sign_in_unavailable"
#: The entitlement store could not be read (503).
SUBSCRIPTION_STATUS_UNAVAILABLE = "subscription_status_unavailable"
#: `/auth/entitlement` refused the signed transaction (400).
ENTITLEMENT_REJECTED = "entitlement_rejected"

# ── Any route ────────────────────────────────────────────────────────────────

#: FastAPI's request validation: `detail` is its list of problems (422).
INVALID_REQUEST = "invalid_request"
#: The body passed its route's cap (413).
PAYLOAD_TOO_LARGE = "payload_too_large"
#: A `content-length` that is not a number (400).
BAD_CONTENT_LENGTH = "bad_content_length"

#: For an error raised without a code. Mostly generic on purpose: a client
#: matches the specific codes above and treats these as "unknown", which is
#: what they are. Three statuses have only one meaning here, so they map to
#: that specific code, which a client may route on: 413 to
#: `PAYLOAD_TOO_LARGE`, 426 to `UPDATE_REQUIRED` and 429 to `RATE_LIMITED`.
_BY_STATUS: Mapping[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: PAYLOAD_TOO_LARGE,
    422: "unprocessable",
    426: UPDATE_REQUIRED,
    429: RATE_LIMITED,
    500: "internal_error",
    502: "bad_gateway",
    503: "unavailable",
    504: "gateway_timeout",
}


class APIError(HTTPException):
    """An `HTTPException` that says which failure it is.

    Raised exactly where an `HTTPException` was, with the same status,
    `detail` and headers; `http_error` below puts `code` beside `detail`.
    """

    def __init__(self, status_code: int, code: str, detail: str,
                 headers: Mapping[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail,
                         headers=dict(headers) if headers else None)
        self.code = code


def code_for(exc: StarletteHTTPException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return _BY_STATUS.get(exc.status_code, "error")


def body(code: str, detail: object) -> dict:
    """An error body. `detail` first, as it always was."""
    return {"detail": detail, "code": code}


async def http_error(_request: Request, exc: Exception) -> Response:
    """FastAPI's own handler for `HTTPException`, plus `code`.

    Registered for Starlette's base class, so the router's own 404 and 405
    get one too. Typed `Exception` because that is what
    `add_exception_handler` accepts; it is only ever registered for
    `HTTPException`.
    """
    assert isinstance(exc, StarletteHTTPException)
    headers = getattr(exc, "headers", None)
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)
    return JSONResponse(body(code_for(exc), exc.detail),
                        status_code=exc.status_code, headers=headers)


async def validation_error(_request: Request, exc: Exception) -> Response:
    """FastAPI's own 422 for a request that does not fit its model, plus
    `code`. `detail` stays the list of problems every build already parses."""
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(body(INVALID_REQUEST, jsonable_encoder(exc.errors())),
                        status_code=422)
