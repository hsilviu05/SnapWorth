# The `/scan` response contract

Responses the server really sends, read by both test suites.

| File | What it is |
|---|---|
| `scan-response.json` | A `/scan` 200 for a Pro user: every v1 field plus the full v2 valuation payload |
| `scan-response-free.json` | The same scan for a free user — the common case. The Pro-only detail is blanked (`null` or `[]`), never removed, and `free_scans_remaining` is what is left after the day's scan |
| `errors/scan-402-quota.json` | The free allowance is spent. Carries `X-Quota-Resets-At`, which the app does not read yet |
| `errors/listing-402-pro.json` | `/listing` refusing a free caller |
| `errors/scan-422-unusable-photo.json` | A safety block: the server looked at the photo and could not use it |
| `errors/scan-429-rate-limited.json` | The per-device limit. Carries `Retry-After` in seconds |

An error fixture records the status, the headers the server sends that a
client may rely on, and the body. Of those headers the app reads only
`Retry-After` today. Header values are whatever the server sent when the file
was generated; what is fixed is that they are plain integers.

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
   allowance was charged, so the user's retry is the paywall. Only
   `valuation_source` is read leniently. **New structure goes in a new
   field**; adding an optional field is safe.
3. **The error wording is contract where the client routes on it.** The client
   shows `detail` as written (a string, or FastAPI's validation list), reads
   `Retry-After` on a 429, and sends a 402 to "Pro required" when `detail`
   contains "pro feature" and to "allowance spent" otherwise
   (`AppError.from`). The two 402 fixtures pin that distinction.
4. Changing these files runs **both** suites, whatever else the commit
   touched. There is no separate workflow: `backend.yml` and `ios.yml` each
   carry `contract/**` in their own `paths` filter, which is what makes a
   commit that only edits these files run the Python *and* the Swift decode
   test. (This rule used to name a `contract-check` job in
   `.github/workflows/contract.yml`, which has never existed — the path
   filters are and always were the mechanism.)

The Swift suite decodes the two 200 bodies (`ScanContractTests`). It does not
read the error fixtures yet, and nothing on the client tests the free body
against the check that shows the "Why this price" teaser; both are the next
iOS change to make here.
