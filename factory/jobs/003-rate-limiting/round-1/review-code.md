VERDICT: PASS

## Checks
cd backend && GEMINI_API_KEY=ci-placeholder-not-real .venv/bin/python -m pytest tests/ -q: pass (2405 passed)
backend/.venv/bin/pyright --pythonpath backend/.venv/bin/python $(git ls-files 'backend/*.py' ':!:backend/tests/*'): pass (0 errors, 0 warnings)
ios/ and website/ checks: not applicable (no changes there)

## Findings
None.

## Notes
- Matches the spec point for point: `social.limiter` is declared like `referral.limiter`, `tiktok_callback` awaits `limiter(ratelimit.client_ip(request))` as its first statement, and `_lifespan` sets `social.limiter = _enforce_ip_limit`. There is no new bucket, constant, env var or `_enforce_*` function. `ratelimit.py` and `.env.example` are untouched.
- The tests would fail without the change, and the builder's mutation check in build.md covers that. Each test sets limiter and `main` state through `monkeypatch`, and `TestCallbackRoute` is unmodified because the diff to `test_social.py` is additions only.
- The throttle test patches a fresh store onto both `main._ip_memory.store` and `main._ip_rate_store`. This leaves the shared store intact for later tests, and it is a sound reading of "clear via monkeypatch".
- Commit: one commit, the subject follows the `<Area>: <what changed>` form, the body explains why, and nothing stray is committed. The worktree is clean.
- Cosmetic: the reflowed RUNBOOK item 3 leaves a short ragged line ("they stop together. The"). It reads fine in rendered Markdown.
