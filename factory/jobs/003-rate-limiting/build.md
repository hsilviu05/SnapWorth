STATUS: READY

# Build: Rate limit the TikTok link callback

## Summary
`GET /social/tiktok/callback` now draws on the standard per-IP bucket,
`main._enforce_ip_limit` (`ip:<client_ip>` at `IP_RATE_MAX_REQUESTS`, 60/h), the
same one `/scan`, the open `/auth` routes and `/apple/notifications` use. `social`
declares an injected `limiter: Callable[[str | None], Awaitable[None]] | None = None`
(the `referral.limiter` / `outcomes.limiter` pattern, because `social` cannot
import `main`), `main._lifespan` sets `social.limiter = _enforce_ip_limit`, and the
route awaits `limiter(ratelimit.client_ip(request))` as its first statement when it
is set. Over the limit it answers what `_enforce_ip_limit` raises: 429, `Retry-After`,
apierrors JSON `code: "rate_limited"`. No new bucket, prefix, constant or env var.

The comment above `_social` now says the route is on the standard per-IP bucket,
and corrects "TikTok's servers call it" to the operator's browser after TikTok's
redirect (the spec's assumption allowed this). RUNBOOK §5.8 item 3 names the route.

Commit (branch `factory/003-rate-limiting`, on `main` 22f26b3):
- `0de1ee9` Social, backend: rate limit /social/tiktok/callback with the standard IP bucket

Files changed: `backend/insights/social.py`, `backend/main.py`, `backend/tests/test_social.py`,
`backend/tests/test_client_ip.py`, `docs/RUNBOOK.md`.

## Acceptance criteria
1. [x] **The limiter is consulted with the resolved client IP.**
   `tests/test_social.py::TestCallbackLimit::test_the_limiter_is_consulted_with_the_resolved_client_ip`
   monkeypatches `social.limiter` with a recorder, calls
   `?error=access_denied` with `x-forwarded-for: 198.51.100.23, 95.173.10.20`, asserts
   `seen == ["198.51.100.23"]` and status 400. `pytest tests/test_social.py -v`: PASSED.
2. [x] **The limiter runs before any link work.**
   `TestCallbackLimit::test_the_limiter_runs_before_any_link_work` mints a state with
   `begin_link()`, sets a limiter raising `HTTPException(418)`, asserts 418 and
   `platforms.token_requests == []`, then sets `social.limiter` to `None` (monkeypatch)
   and the same URL answers 200 "linked". PASSED.
3. [x] **It is the standard `ip:` bucket and it throttles.**
   `TestCallbackLimit::test_the_standard_bucket_throttles_it_and_is_shared` sets, via
   monkeypatch, `social.limiter = main._enforce_ip_limit`, `main._ip_limiter = None`,
   `main.IP_RATE_MAX_REQUESTS = 3`, and an empty in-process store (monkeypatched onto
   both `main._ip_memory.store` and `main._ip_rate_store`, so the shared store is
   restored afterwards rather than cleared under other tests). Four calls with
   `x-forwarded-for: 198.51.100.7` through `main.app` give `[400, 400, 400, 429]`; the
   429 has `Retry-After` and `code == "rate_limited"`; the store's keys are exactly
   `{"198.51.100.7"}`; then `POST /apple/notifications` from the same address answers
   429. `pytest tests/ -v -k "tiktok or social"`: 19 passed, this test listed.
4. [x] **Every caller keys on `client_ip`.**
   `tests/test_client_ip.py::TestEveryCallerUsesIt::test_the_tiktok_callback_route`,
   parametrised over `CASES`, records the key passed to `social.limiter` through
   `main.app` (the module's `client`) and asserts it equals `expected` and status 400.
   `pytest tests/test_client_ip.py -v`: the three new cases
   `[198.51.100.23, 95.173.10.20-198.51.100.23]`,
   `[203.0.113.7, 198.51.100.23, 151.101.1.1-198.51.100.23]`,
   `[203.0.113.7, 198.51.100.23, 95.173.10.20-95.173.10.20]` PASSED.
5. [x] **Startup wires it.** `grep -n "social.limiter = _enforce_ip_limit" backend/main.py`
   prints `252:    social.limiter = _enforce_ip_limit`, inside `_lifespan` next to the
   `referral` / `outcomes` injections. `TestCallbackLimit::test_startup_wires_it` asserts
   it in `inspect.getsource(main._lifespan)`; PASSED under `pytest tests/`.
   Also checked live (below): a running server throttles the route, so the lifespan
   wiring takes effect, not only the source text.
6. [x] **The existing behaviour is unchanged.** `pytest tests/`: 2405 passed, including
   the unmodified `TestCallbackRoute::test_route_completes_or_refuses` and the
   `metrics.KNOWN_ENDPOINTS` vs `main.app.routes` check.
   `git diff main -- backend/tests/test_social.py | grep -E "^-[^-]" | wc -l` prints `0`:
   additions only, one new class `TestCallbackLimit` after `TestCallbackRoute`.
7. [x] **No new knobs.** `git diff main --stat -- backend/security/ratelimit.py backend/.env.example`
   is empty. `git diff main -- backend/main.py | grep -E "^\+.*(os.environ.get|async def _enforce_)" | wc -l`
   prints `0`; the `main.py` diff is the one assignment plus a two-line comment.
8. [x] **Types stay clean.** `backend/.venv/bin/pyright --pythonpath backend/.venv/bin/python $(git ls-files 'backend/*.py' ':!:backend/tests/*')`
   from the worktree root: `0 errors, 0 warnings, 0 informations`.
9. [x] **Docs.** `grep -n "/social/tiktok/callback" docs/RUNBOOK.md` prints line 492, in
   §5.8 item 3's "The IP bucket is one for every route with a limit" sentence.
   `grep -n -i "limit" backend/insights/social.py` shows line 264 (the comment above `_social`:
   "limited by the standard per-IP bucket") and lines 269-273 (the `limiter`
   declaration and its comment), plus the call at 286-287.

## Validation
- `cd backend && python3.13 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt`: installed fine (package index reachable).
- `cd backend && GEMINI_API_KEY=ci-placeholder-not-real .venv/bin/python -m pytest tests/ -q`: 2405 passed (run on the committed tree).
- `pytest tests/test_social.py tests/test_client_ip.py -v`: 105 passed.
- `pytest tests/ -v -k "tiktok or social"`: 19 passed.
- Mutation check: with the route's `await limiter(...)` line replaced by `pass`, all four
  new `test_social.py` tests that exercise the route and all three new
  `test_client_ip.py` cases failed (6 failed); file restored afterwards and re-verified.
- pyright over production modules (CI's command): 0 errors.
- Live: `GEMINI_API_KEY=ci-placeholder-not-real .venv/bin/uvicorn main:app --port 5100`,
  61 GETs of `/social/tiktok/callback?error=access_denied` with
  `x-forwarded-for: 198.51.100.9` gave 60 x 400 then 429; the next one was
  `HTTP/1.1 429`, `retry-after: 3599`,
  `{"detail":"Rate limit: 60 requests/hour.","code":"rate_limited"}`; then
  `POST /apple/notifications` from the same address answered 429 (shared bucket).
  Server stopped afterwards.
- No `ios/` or `website/` changes, so those checks do not apply.
- `git status --porcelain` in the worktree: empty. Not pushed.

## Log
### Round 1
Implemented requirements 1-8 in one commit (`0de1ee9`). Notes for review:
- The new limit-tests live in their own class `TestCallbackLimit` in `test_social.py`,
  using inline imports (as `TestCallbackRoute` does) so the diff to that file is
  pure additions. Every one sets `social.limiter`, `social._social` and `main` limiter
  state through `monkeypatch`. For the in-process store, AC3 asks it to be "cleared via
  monkeypatch": I monkeypatched a fresh empty store onto `main._ip_memory.store` and
  `main._ip_rate_store` rather than calling `.clear()` on the shared dict, so the
  original store and any entries in it come back after the test.
- `test_client_ip.py` also gets `import social` and its `TestEveryCallerUsesIt`
  docstring now names `social` among the callers (one changed line).
- The `_social` comment's "TikTok's servers call it" was corrected to the operator's
  browser, as the spec's assumptions permit. I did not say "every other unauthenticated
  route" there, since `/health*`, `/privacy`, `/terms` and `/metrics` remain unlimited
  by design; it names the open `/auth` routes and `/apple/notifications` instead.
