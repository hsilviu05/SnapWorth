# Rate limit the TikTok link callback

> Feature: Rate limit the TikTok link callback with the standard per-IP bucket, like every other unauthenticated route

## Context

**The route.** `GET /social/tiktok/callback` is `social.tiktok_callback`
(`backend/social.py:269-281`), on `social.router` (`APIRouter(prefix="/social")`,
`backend/social.py:261`), mounted by `app.include_router(social.router)`
(`backend/main.py:609`). It is the OAuth redirect target of the operator's
one-time TikTok link (`TikTokClient.begin_link` / `complete_link`,
`backend/social.py:121-135`). It takes no credentials. On every call that has a
`code` it runs `complete_link`, which reads and deletes a cache key
(`opssocial:tiktok:state:<state>`) and, when the state is live, makes an
outbound token request to TikTok. Today nothing limits it: the comment above
`_social` (`backend/social.py:258-259`) calls it "harmless without a live state
nonce", but every request still costs a cache round trip.

**The standard per-IP bucket.** `main._enforce_ip_limit(ip)`
(`backend/main.py:957-968`) is the IP-only limit "for routes that have no device
or subject yet". It checks key `ip:<ip>` on `main._ip_limiter` (Redis, via
`ratelimit.ResilientRateLimiter`) at `ratelimit.IP_RATE_MAX_REQUESTS` (60/h,
`backend/ratelimit.py:333-336`). Before startup it falls back to the in-process
`main._ip_memory`, keyed by the bare IP. Over the limit it raises
`apierrors.APIError(429, RATE_LIMITED, ...)` with a `Retry-After` header. This
is the same `ip:` bucket that `/scan`, `/listing`, `/legit`, `/rack`, `/trends`
and `/auth/entitlement` share through `_enforce_limits` /
`_enforce_entitlement_limit`. The key comes from `ratelimit.client_ip(request)`
(`backend/ratelimit.py:33-120`), the one resolver every caller must use
(`main._client_ip` wraps it).

**How the other unauthenticated routes use it.**
- `/apple/notifications` and `/apple/notifications/sandbox` call
  `await _enforce_ip_limit(_client_ip(request))` as their first statement
  (`backend/main.py:1864`, `backend/main.py:2026`).
- `/auth/challenge`, `/auth/attest` and `/auth/refresh` are in `auth`, which
  cannot import `main`. So `main._lifespan` injects the function:
  `auth.deps.ip_limiter = _enforce_ip_limit` (`backend/main.py:240-241`).
  `auth._limit_unauthenticated` then calls
  `await deps.ip_limiter(ratelimit.client_ip(request))`, and returns early when
  it is `None` (`backend/auth.py:307-329`).
- `referral` and `outcomes` follow the same injection pattern with a
  module-level `limiter: Callable[...] | None = None`
  (`backend/referral.py:113-119`, `backend/outcomes.py:119`), set in
  `main._lifespan` (`backend/main.py:244-247`). The route calls it only
  `if limiter is not None`.

`social` is imported by `main` (`backend/main.py:58`) and is configured in the
same lifespan (`social.configure(social_readers)`, `backend/main.py:262-263`),
so it cannot import `main` either. It *can* import `ratelimit`, which has no
in-repo imports.

**Tests that set the pattern.**
- `backend/tests/test_social.py::TestCallbackRoute` mounts `social.router` on a
  bare `FastAPI()` and checks link / replay / denied.
- `backend/tests/test_referral.py::TestLimiter::test_startup_wires_it` asserts
  the lifespan wiring via `inspect.getsource(main._lifespan)`.
- `backend/tests/test_client_ip.py::TestEveryCallerUsesIt` checks that each
  limited route keys on `ratelimit.client_ip` over its `CASES`. Its
  `TestTheRealLimiter` shows how to drive `main._enforce_ip_limit` end to end
  by monkeypatching `main._ip_limiter = None` and `main.IP_RATE_MAX_REQUESTS`.
- `backend/tests/test_main.py` has
  `test_the_endpoint_is_rate_limited_like_every_other_open_route` for
  `/apple/notifications`.

**Docs.** `docs/RUNBOOK.md` §5.8, item 3 (around line 490), lists every route
on the shared IP bucket: "`/scan`, `/rack`, `/listing`, `/legit`, `/trends`,
`/auth/entitlement`, and the unauthenticated `/auth` routes and
`/apple/notifications`". `metrics.KNOWN_ENDPOINTS` already contains
`/social/tiktok/callback` (`backend/metrics.py:430-436`), so no metrics change
is needed.

## Requirements

1. `backend/social.py` declares a module-level injected limiter, matching
   `referral.limiter` / `outcomes.limiter`:
   `limiter: Callable[[str | None], Awaitable[None]] | None = None`. It has a
   comment saying that `main._lifespan` injects it, why (`social` cannot import
   `main`), and that it is `None` only before startup and in tests.
2. `tiktok_callback` takes the `Request` and, **as its first statement**,
   before the `error` / `code` / `_social is None` checks and before
   `complete_link`, awaits `limiter(ratelimit.client_ip(request))` when
   `limiter` is not `None`. The key comes from `ratelimit.client_ip`, never
   from `request.client.host` or a header read of its own.
3. `main._lifespan` injects the existing standard limiter:
   `social.limiter = _enforce_ip_limit`. No new limiter function, bucket, key
   prefix, constant or environment variable. The callback draws on the same
   `ip:<client_ip>` bucket at `IP_RATE_MAX_REQUESTS` as every other limited
   route.
4. Over the limit, the callback answers what `_enforce_ip_limit` raises: HTTP
   429, with a `Retry-After` header and the `apierrors` JSON body
   (`code: "rate_limited"`). Under the limit, the response for each input
   (200 linked, 400 failed, 400 denied, 400 when unconfigured) is unchanged.
5. When `social.limiter` is `None`, the route behaves exactly as today. The
   existing `TestCallbackRoute` passes unchanged.
6. Update the comment above `_social` in `backend/social.py` (lines 258-259) to
   say that the route is limited by the standard per-IP bucket.
7. `docs/RUNBOOK.md` §5.8, item 3, names `/social/tiktok/callback` among the
   routes on the shared IP bucket.
8. Tests (pytest, in `backend/tests/`) cover the behaviour. Every test that
   sets `social.limiter` or `main` limiter state does so with `monkeypatch`, so
   nothing leaks into other tests:
   - in `test_social.py`: the route calls the limiter once with the
     `client_ip` key; the limiter runs before `complete_link`; the standard
     bucket returns 429 once it is spent;
   - in `test_client_ip.py::TestEveryCallerUsesIt`: a parametrised case for the
     callback over `CASES`, like `test_mains_apple_notifications_route`;
   - a wiring test like `test_referral.py::TestLimiter::test_startup_wires_it`.

## Acceptance criteria

1. **The limiter is consulted with the resolved client IP.** There is a test in
   `backend/tests/test_social.py` that monkeypatches `social.limiter` with a
   recording coroutine and calls
   `GET /social/tiktok/callback?error=access_denied` with
   `x-forwarded-for: 198.51.100.23, 95.173.10.20`. It asserts that the
   recorded keys equal `["198.51.100.23"]` and the status is 400.
   Verify: `cd backend && pytest tests/test_social.py -v` passes, with that
   test listed.
2. **The limiter runs before any link work.** There is a test in
   `backend/tests/test_social.py` that mints a live state with `begin_link()`,
   then monkeypatches `social.limiter` with one that raises
   `HTTPException(status_code=418)`, then calls
   `/social/tiktok/callback?code=abc&state=<live state>`. It asserts:
   - the status is 418;
   - `platforms.token_requests == []`;
   - the state is still usable: with the limiter reset to `None`, the same URL
     then returns 200 "linked".

   Verify: `cd backend && pytest tests/test_social.py -v` passes.
3. **It is the standard `ip:` bucket and it throttles.** There is a test that
   sets `social.limiter = main._enforce_ip_limit`, `main._ip_limiter = None`
   and `main.IP_RATE_MAX_REQUESTS` to a small N (for example 3), and clears
   `main._ip_rate_store`, all via `monkeypatch`. It then calls the callback
   N + 1 times with `x-forwarded-for: 198.51.100.7`. It asserts:
   - the first N answer 400 and the last answers 429;
   - the 429 has a `Retry-After` header;
   - the 429's JSON `code` is `"rate_limited"`;
   - the in-process store holds the bare key `198.51.100.7`, which is
     `_enforce_ip_limit`'s in-memory key, not a new prefix.

   It must also show that the bucket is shared. After the callbacks spend the
   bucket, `POST /apple/notifications` from the same address through
   `main.app` answers 429. Verify:
   `cd backend && pytest tests/ -v -k "tiktok or social"` passes, with this
   test listed.
4. **Every caller keys on `client_ip`.** In `backend/tests/test_client_ip.py`,
   `TestEveryCallerUsesIt` has a test parametrised over `CASES` (for example
   `test_the_tiktok_callback_route`). It records the key the callback passes to
   `social.limiter` through `main.app` (the module's `client`), and asserts
   that key equals `expected` for each case. Verify:
   `cd backend && pytest tests/test_client_ip.py -v` passes, with three new
   parametrised cases listed.
5. **Startup wires it.** A test asserts
   `"social.limiter = _enforce_ip_limit" in inspect.getsource(main._lifespan)`.
   Verify:
   `grep -n "social.limiter = _enforce_ip_limit" backend/main.py` prints one
   line inside `_lifespan`, and the test passes under `pytest tests/`.
6. **The existing behaviour is unchanged.** Run `cd backend && pytest tests/`.
   The whole suite passes, including the unmodified
   `tests/test_social.py::TestCallbackRoute::test_route_completes_or_refuses`
   and the existing check that `metrics.KNOWN_ENDPOINTS` matches
   `main.app.routes`.
   `git diff main -- backend/tests/test_social.py` shows only additions inside
   the existing test module, not edits to `test_route_completes_or_refuses`.
7. **No new knobs.** `git diff main -- backend/ratelimit.py backend/.env.example`
   is empty. `git diff main -- backend/main.py` adds no new
   `os.environ.get(...)` and no new `async def _enforce_*` function.
8. **Types stay clean.** From the repo root,
   `pyright --pythonpath "$(command -v python)" $(git ls-files 'backend/*.py' ':!:backend/tests/*')`
   reports `0 errors`.
9. **Docs.** `grep -n "/social/tiktok/callback" docs/RUNBOOK.md` prints a match
   in §5.8, item 3 (the "The IP bucket is one for every route with a limit"
   sentence). `grep -n -i "limit" backend/social.py` shows the updated comment
   above `_social` and the `limiter` declaration's comment.

## Out of scope

- Limiting `/health`, `/health/live`, `/health/ready`, `/privacy`, `/terms` or
  `/metrics`. They are unauthenticated and unlimited today, but the request
  names the TikTok callback only. Health probes from Railway and CI must not
  start drawing on a 60/h bucket as a side effect.
- A bucket, key prefix, size or environment variable of the callback's own
  (for example `social-ip:` or `SOCIAL_RATE_MAX_REQUESTS`). The request says
  "the standard per-IP bucket".
- Rendering the 429 as an HTML page in the callback's style. Every other route
  answers a 429 with the `apierrors` JSON body. The only legitimate visitor is
  the operator, about once a year.
- Changing `ratelimit.client_ip`, `_enforce_ip_limit`, the state-nonce logic,
  `TikTokClient`, or the bot's `/social` flow.
- Adding `/apple/notifications/sandbox` to the RUNBOOK list, or other RUNBOOK
  tidying. Only the one route this job limits is added.
- Moving the callback, or the limiter, into `main.py`.

## Assumptions

- **"Standard per-IP bucket" means `main._enforce_ip_limit`.** That is the
  shared `ip:` key at `IP_RATE_MAX_REQUESTS` (60/h), injected the way
  `auth.deps.ip_limiter` is. It is the exact function the other
  unauthenticated routes (`/auth/*`, `/apple/notifications*`) use.
- **The route is limited through an injected `social.limiter`, not by
  importing `main`.** `main` imports `social`, so `social` cannot import
  `main`. `referral` and `outcomes` already solve the same problem this way.
- **The limiter runs first, even for the `error=` and unconfigured
  branches.** This matches `/apple/notifications`, and the point is to bound
  requests per source, not only the expensive branch.
- **Sharing the bucket with the operator's own app traffic is acceptable.** The
  callback is reached by the operator's browser after TikTok's OAuth redirect,
  not by TikTok's servers, so it is keyed on the operator's address. The
  `_social` comment's "TikTok's servers call it" is imprecise; the builder may
  correct that wording in requirement 6. A link happens about once a year, so
  one extra request on a 60/h bucket is negligible. The opposite risk is real:
  an operator who has spent the bucket from the same network would see a 429
  on the link. They retry within the hour, and the state nonce lives 10
  minutes, so they may need a fresh link from the bot.
- **"Like every other unauthenticated route" is read as the routes that already
  have a limit.** `/health*`, `/privacy`, `/terms` and `/metrics` have none, and
  this job does not change that (see Out of scope). If the requester meant
  every route, that is a separate product decision about health probes.
