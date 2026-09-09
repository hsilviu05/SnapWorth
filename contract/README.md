# The `/scan` response contract

One file, two readers. `scan-response.json` is a complete, representative
`/scan` 200 body: every v1 field the iOS client requires, plus the v2
valuation payload it decodes when present.

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

## The rules

1. **This file is the source of truth.** Both suites load it. Neither may
   re-type the payload inline.
2. **Removing or renaming a field here is a breaking change** for every
   installed client, which cannot be updated in step with a backend deploy.
   Adding an optional field is not.
3. Changing this file makes the `contract-check` job run **both** suites,
   whatever else the commit touched — see `.github/workflows/contract.yml`.
