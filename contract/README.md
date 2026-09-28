# The `/scan` response contract

Responses the server really sends, read by both test suites.

| File | What it is |
|---|---|
| `scan-response.json` | A `/scan` 200 for a Pro user: every v1 field plus the full v2 valuation payload |
| `scan-response-free.json` | The same scan for a free user — the common case. The Pro-only detail is blanked (`null` or `[]`), never removed, and `free_scans_remaining` is what is left after the day's scan |
| `errors/scan-402-quota.json` | The free allowance is spent (`quota_exhausted`). Carries `X-Quota-Resets-At`, which the app reads for the spent state's "Next free scan at …" |
| `errors/listing-402-pro.json` | `/listing` refusing a free caller (`pro_required`) |
| `errors/scan-422-unusable-photo.json` | A safety block: the server looked at the photo and could not use it (`photo_unusable`) |
| `errors/scan-422-not-resalable.json` | The model priced the photo at zero on purpose; `detail` carries its reason (`not_resalable`) |
| `errors/scan-426-update-required.json` | Below the operator's minimum build, for a build that sent `X-SnapWorth-Build` (`update_required`) |
| `errors/scan-429-rate-limited.json` | The per-device limit (`rate_limited`). Carries `Retry-After` in seconds |
| `errors/scan-502-ai-unavailable.json` | The model could not be reached (`ai_unavailable`) |
| `error-codes.json` | Every specific error `code` the server sends (`backend/apierrors.py`) |
| `confidence-reason-codes.json` | Every code `confidence_reason_codes` can carry (`confidence.REASON_CODES`) |

An error fixture records the status, the headers the server sends that a
client may rely on, and the body. Of those headers the app reads
`Retry-After` and `X-Quota-Resets-At`. Header values are whatever the server
sent when the file was generated; what is fixed is that they are plain
integers.

An error body is `{"detail": …, "code": …}` for every `HTTPException` (the
routes' own, and the router's 404 and 405), every request that fails
validation, and the body-size limit's 400 and 413. `detail` is what it always
was — a sentence, or FastAPI's validation list — and it is the text installed
builds show. `code` is a stable token beside it (`backend/apierrors.py` lists
them all) that a client can route on and translate. An error raised without a
specific code gets one from its status: generic for most (`not_found`,
`bad_request`…), which no client routes on, and the specific
`payload_too_large`, `update_required` and `rate_limited` for 413, 426 and
429.

An exception nothing handles is not one of those. Starlette answers it with a
plain-text `Internal Server Error` 500 and no body code, and a client must
treat it as unknown.

`/scan` bodies also carry `confidence_reason_codes`: one token per entry of
`confidence_reasons`, same order (`backend/confidence.py`). Pro detail, like
the reasons: blanked to `[]` on a free scan.

The two lists are not responses. They are what a client checks its own
tables against: the app must word every reason code, and must not wait for an
error code the server never sends.

## Why it exists

The contract used to be asserted twice, independently and in two languages:

- `backend/tests/test_ai_pipeline.py` — `TestScanResponseContract`, against a
  Python dict.
- `ios/SnapWorthTests/ProductionHardeningTests.swift` — against a hand-typed
  JSON string literal.

Nothing compared them. And the two CI workflows have mutually exclusive path
filters (`backend/**` and `ios/**`), so eight non-merge commits since 2026-08-20
changed `main.py`, `valuation.py` or `prompts.py` and deployed to production
with **zero** client-decode verification. A renamed or dropped field would have
been caught by neither suite: the backend's own test would have been updated
alongside the change, and the iOS literal would have gone on asserting a shape
the server no longer sent.

The single file that replaced them was then hand-written too, and drifted: it
carried a `model` field the server never sends, lacked six it does, covered
only the Pro body and no error at all, and was compared with the server on the
nine v1 fields alone. Every file here is now generated from the server's own
output by `backend/tests/test_contract.py`, which compares every key and the
JSON type of every value on each run.

## The rules

1. **These files are the source of truth.** Both suites load them. Neither may
   re-type a payload inline, and nobody edits them by hand: after an intended
   change, regenerate them and read the diff —

   ```sh
   cd backend && REGENERATE_CONTRACT=1 pytest tests/test_contract.py
   ```

2. **Removing, renaming or changing the type of a field is a breaking change**
   for every installed client, which cannot be updated in step with a backend
   deploy. "Type" includes a string becoming an object, a list of strings
   becoming a list of objects, and a whole number becoming a fractional one.
   This holds for the v2 fields as much as the v1 ones: since #87 the client
   decodes them with `decodeIfPresent`, which accepts `null` or absence but
   throws on a wrong type, and one throw fails the whole scan — after the free
   allowance was charged, so the user's retry is the paywall. Every build up
   to and including 1.5.1 (build 21) decodes that way, reading only
   `valuation_source` and, from the build that added it,
   `confidence_reason_codes` leniently. **1.5.2 is the first lenient build**
   (#222): it reads each optional field on its own, so a wrong type costs
   that field — nil, or `[]` for a list — and is logged and reported as
   `scan_field_undecodable` by key. The rule does not relax with it: the
   strict builds stay installed for good, and a required v1 field is strict
   in every build. **New structure goes in a new field**; adding an optional
   field is safe.
3. **A `code` is contract; so is the wording an installed build routes on.**
   A client routes on `code` and words it in its own language, so renaming a
   code breaks every build that knows it — a new failure gets a new one, and
   the value is asserted in `test_contract.py`, which the type comparison
   alone would not. Builds before the codes show `detail` as written, read
   `Retry-After` on a 429, and send a 402 to "Pro required" when `detail`
   contains "pro feature" and to "allowance spent" otherwise; newer builds
   still do that for a body without a code. The two 402 fixtures pin both.
4. Changing these files runs **both** suites, whatever else the commit
   touched. There is no separate workflow: `backend.yml` and `ios.yml` each
   carry `contract/**` in their own `paths` filter, which is what makes a
   commit that only edits these files run the Python *and* the Swift decode
   test. (This rule used to name a `contract-check` job in
   `.github/workflows/contract.yml`, which has never existed — the path
   filters are and always were the mechanism.)

The Swift suite decodes the two 200 bodies (`ScanContractTests`), breaks
each optional key of both in turn and requires that only that field is lost
(`LenientScanFieldTests`), reads the
quota 402's reset header (`QuotaResetTests`), runs every error fixture through
`ScanAPIError.from` and `AppError.from` (`ErrorContractTests`), and checks its
`ServerErrorCode` and `ConfidenceReason` against the two code lists, so a code
the client routes on or words cannot change without a Swift test failing.
Nothing on the client tests the free body against the check that shows the
"Why this price" teaser yet.
