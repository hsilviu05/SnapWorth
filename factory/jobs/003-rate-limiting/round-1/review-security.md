VERDICT: PASS

## Findings
None.

## Notes
- The change does what the spec asks and adds no new attack surface.
  `tiktok_callback` (`backend/insights/social.py:282-287`) awaits the injected limiter
  as its first statement, before the `error` / unconfigured refusals and
  before `complete_link`. A throttled call therefore never touches the
  `opssocial:tiktok:state:*` cache key and never sends the outbound token
  request. `test_the_limiter_runs_before_any_link_work` pins this: 418,
  `token_requests == []`, and the nonce is still usable afterwards.
- The key comes from `ratelimit.client_ip(request)`, the one resolver, and
  not from `request.client.host` or a header the route reads itself. The new
  `TestEveryCallerUsesIt::test_the_tiktok_callback_route` cases over `CASES`
  hold it to the Railway first-entry rule. A client cannot pick its bucket
  with a forged `X-Forwarded-For`, because Railway rewrites the header. So an
  attacker cannot spend the operator's bucket from another address.
- `main._lifespan` injects `_enforce_ip_limit` itself (`backend/main.py:252`).
  It is the same `ip:<ip>` bucket at `IP_RATE_MAX_REQUESTS`, with no new
  prefix, constant or env var. `ratelimit.py` has no in-repo imports, so
  `social` importing it adds no cycle. Over the limit the route answers the
  standard `APIError` 429 JSON with `Retry-After`. The body gives only the
  limit and the code, with no internal detail.
- `client_ip` is typed `-> str` and falls back to the fixed `unparseable`
  key, so the `ip is None` early return in `_enforce_ip_limit` should not
  open a path around the limit here. If it ever did, every other caller would
  have the same gap, so it is not specific to this branch.
- `social.limiter` is `None` until the lifespan runs. That is the same window
  `referral.limiter` / `outcomes.limiter` / `auth.deps.ip_limiter` have, and
  there is no way to serve requests before startup. No regression.
- Pre-existing, unchanged: the callback's HTML is built from fixed strings.
  `error`, `code` and `state` are never reflected into the page, so it has no
  XSS surface.
- No secrets in the diff (checked by hand; `gitleaks` is not installed in
  this environment). No dependency, iOS, entitlement or website changes.
