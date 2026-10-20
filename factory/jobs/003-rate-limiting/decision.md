APPROVE

## Why
The branch puts `GET /social/tiktok/callback` on the existing `main._enforce_ip_limit` bucket. It does this through an injected `social.limiter`, the pattern `referral` and `outcomes` already use, and the bucket is keyed by `ratelimit.client_ip`. `ratelimit.py`, the `_enforce_*` helpers, the `ip:` key scheme, every limit size and every env var are untouched, so no existing limit changes size or keying. The user's request named the bucket itself ("the standard per-IP bucket"), so the job made no choice of its own about a limit. All four round-1 reviews are PASS with no real problem in their Notes. I re-ran the checks myself, and `git merge-tree --write-tree main factory/003-rate-limiting` is clean (tree `3b3f67c`, exit 0).

## Acceptance criteria
1. Limiter consulted with the resolved client IP: met. `TestCallbackLimit::test_the_limiter_is_consulted_with_the_resolved_client_ip` asserts `seen == ["198.51.100.23"]` and a 400. I re-ran it and it passed.
2. Limiter runs before any link work: met. The test asserts a 418, `token_requests == []`, and then a 200 "linked" once the limiter is `None`. I re-ran it and it passed. In the diff the limiter call is the route's first statement.
3. Standard `ip:` bucket throttles and is shared: met. The responses are `[400, 400, 400, 429]` with `Retry-After` and `code == "rate_limited"`, and the store holds the bare-IP key. Afterwards `/apple/notifications` from the same address answers 429. The store is patched in through monkeypatch, so the shared store is restored after the test. I re-ran it and it passed. build.md also records a live uvicorn run: 60 x 400, then 429.
4. Every caller keys on `client_ip`: met. `TestEveryCallerUsesIt::test_the_tiktok_callback_route` has three parametrised cases through `main.app`, and I re-ran them; they passed.
5. Startup wires it: met. `grep` prints `252:    social.limiter = _enforce_ip_limit`, inside `_lifespan`, and `test_startup_wires_it` passes.
6. Existing behaviour unchanged: met. I ran `pytest tests/` in the worktree: 2405 passed. The diff to `test_social.py` is additions only, so `TestCallbackRoute` is unmodified.
7. No new knobs: met. `git diff main --stat -- backend/security/ratelimit.py backend/.env.example` is empty, and the `main.py` diff is one assignment plus a comment.
8. Types stay clean: met per build.md and review-code (pyright reports 0 errors). I did not re-run pyright.
9. Docs: met. RUNBOOK §5.8 item 3 now names `/social/tiktok/callback`. In `social.py`, the comment above `_social` says the route is on the standard per-IP bucket, and the `limiter` declaration has its own comment.
