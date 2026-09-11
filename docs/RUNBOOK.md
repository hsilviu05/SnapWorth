# SnapWorth production runbook

Operational reference for the backend at `api.snapworth.eu` (Railway, single
region, Docker).

**Every number in this document is labelled.** `[MEASURED]` comes from this
repository or a benchmark; `[ESTIMATED]` is modelled from public pricing and
stated assumptions; `[DESIGNED]` is implemented but never exercised in
production; `[NOT IMPLEMENTED]` is absent. Nothing here is drawn from
observed production traffic, because none has been observed.

---

## 1. Service topology

```mermaid
flowchart LR
    A[iOS app] -->|HTTPS| B[Railway edge]
    B --> C[uvicorn · 1 worker/container]
    C --> D[(Redis)]
    C --> E[Gemini 2.5 Flash]
    C --> F[Apple DeviceCheck]
    C -.-> G[/metrics/]
    G -.scrape.-> H[Prometheus-compatible collector]
```

| Component | State |
|---|---|
| API container | `backend/Dockerfile`, python 3.13-slim, unprivileged uid 10001 |
| Process model | 1 uvicorn worker per container; scale horizontally |
| Durable state | Redis — quota, entitlements, rate limits, attestation |
| System of record | **None.** Redis is a cache; scan history lives on-device |
| Metrics | `/metrics`, Prometheus text format `[DESIGNED]` |
| Collector | `[NOT IMPLEMENTED]` — and **not planned**; see below |
| Monitoring surface | **The Telegram ops bot.** This is the real one |

---

## 1b. How this service is actually monitored

Read this before anything else in the file, because until 2026-09-09 the file
did not say it. `[NOT IMPLEMENTED]` against the metrics collector was true and
misleading at the same time: it implied nothing was watching, while §8 told the
reader to "Run 🩺 Checkup" without ever saying what that is or where to run it.
The word "Telegram" did not appear in this document.

**The ops bot is the monitoring surface.** It lives in `backend/notify.py`,
posts to the operator's Telegram chat, and is where every operational signal
actually arrives:

| You want | Command | What it does |
|---|---|---|
| Is anything broken right now | `🩺 Checkup` | Probes the model, Redis, DeviceCheck and the App Store build in one message |
| Current state | `/status` | Build, cache backend, auth enforcement, last deploy ping, today's counters |
| What it costs | `/costs` | Gemini spend by window, `$/scan`, free-tier giveaway, and the operator's own bot usage listed separately |
| Subscribers | `/subs` | Active, paid, comped, and MRR |
| Is the free-scan experiment working | `/experiment` | The whole window at once: limit hits against new subscriptions, day by day, with a running total |
| Start or stop the free-scan experiment | `/lever` | Arms or disarms the first-day allowance without a Railway change or a redeploy. Two taps, clamped, and `/experiment` footnotes any day it moved |
| Yesterday | The daily digest | Sent automatically at `TELEGRAM_DIGEST_UTC_HOUR` (default 06:00 UTC); a weekly report on Mondays |

Unprompted alerts arrive the same way: a new subscription, a deploy ping per
commit, a quiet-hours note when nothing has scanned during US daytime, a
budget warning, and a device-paused alert after repeated unanalysable photos.

**The decision on `/metrics` (previously tracked as A-7, open and unrecorded
for five days): accept it as designed-but-unscraped.** One replica and a
single operator do not justify running Prometheus, and the bot already answers
the questions a dashboard would. `/metrics` stays because it costs nothing to
keep and is the right shape if a second replica ever appears. It is not
monitoring today, and this table is what is.

---

## 2. Endpoints and probes

| Path | Purpose | Failure semantics |
|---|---|---|
| `/health/live` | Liveness | Checks nothing external — see below |
| `/health/ready` | Readiness | 503 while starting, draining, or cache-unreachable |
| `/health` | Legacy | Retained for compatibility |
| `/metrics` | Prometheus scrape | Requires `Authorization: Bearer $METRICS_TOKEN`; 404 without it |

**Liveness deliberately checks no dependency.** A liveness probe that fails
during a Redis outage makes the orchestrator restart healthy containers, turning
a recoverable blip into a fleet-wide crash-loop. Dependency health belongs in
readiness, where the consequence is "route elsewhere" rather than "kill it".

---

## 3. Alerts

`[DESIGNED]` — thresholds below are starting points to be tuned against real
baselines. Alerting on an unmeasured system produces noise, so treat the first
fortnight as calibration.

### Page (wake someone)

| Alert | Condition | First action |
|---|---|---|
| **API down** | `up == 0` for 2 min | §5.1 |
| **5xx surge** | 5xx rate > 5% over 5 min | §5.2 |
| **Model unavailable** | `model_calls_total{outcome="exhausted"}` > 10/min | §5.3 |
| **Cache unreachable** | `cache_degraded == 1` for 3 min | §5.4 |
| **Latency collapse** | p95 `/scan` > 20s for 5 min | §5.5 |
| **Readiness flapping** | readiness toggles > 3× in 10 min | §5.1 |

### Ticket (do not page)

| Alert | Condition | Why not a page |
|---|---|---|
| 429 rate elevated | > 2% of requests | Rate limiting working as designed |
| Quota exhaustion spike | 3× 7-day baseline | Expected under growth |
| Entitlement failures | > 1% of `/auth/entitlement` | Often Apple-side, self-heals |
| Confidence collapse | median `confidence_score` drops > 20 pts day-on-day | Signals a model or prompt regression |
| Upload size drift | p50 `upload_bytes` > 1 MB | Client-side downscale regressed |
| Clamp rate rising | `valuation_clamped_total` > 5% of scans | Model producing implausible numbers |

**4xx never pages.** `observability.classify_status` marks `CLIENT`,
`CAPACITY` and `SECURITY` as non-paging: a scraper generating 404s, or rate
limiting doing its job, is the system working correctly. Only `DEPENDENCY` and
`INTERNAL` page.

---

## 4. Dashboards `[DESIGNED]`

**Golden signals**
Request rate by endpoint · error rate by class · p50/p95/p99 latency ·
in-flight requests.

**Model**
Call rate by outcome · duration p50/p95 · retry rate · tokens by kind ·
blocked-content rate.

**Quality** — the panel that catches a bad prompt deploy before the benchmark does
Confidence score distribution · clamp rate · upload size distribution.

**Capacity**
Cache hit ratio · rate-limit rejections · quota exhaustion · dependency errors.

---

## 5. Incident playbooks

### 5.1 API unreachable / restarting

1. `railway logs --service snapworth-backend`
2. Look for `RuntimeError: REQUIRE_APP_ATTEST is on but APPLE_TEAM_ID…` or
   `TOKEN_KEYS must be set in production` — both are deliberate startup refusals
   (`main._lifespan`, `tokens.signer_from_env`). Fix the variable; do not remove
   the guard.
3. Check `/health/ready`. A 503 with `"durable cache configured but unreachable"`
   means Redis, not the API → §5.4.
4. If the container is crash-looping with no startup error, roll back (§7).

### 5.2 Elevated 5xx

1. Split by class in `snapworth_http_requests_total{status_class="5xx"}`.
2. **502s** are almost always the model — check
   `model_calls_total{outcome="exhausted"}` → §5.3.
3. **500s** are ours. Find the request id in the log line and grep it; every log
   line carries one (`observability.RequestContextMiddleware`).
4. If 500s started with a deploy, roll back first and diagnose after.

### 5.3 Gemini unavailable

*Blast radius:* `/scan` and `/listing` fail. Auth, quota and entitlements are
unaffected — users keep their Pro status and their history.

1. **Check `/health` first — it now names this failure.** `model.healthy:
   false` with `last_failure_kind: "quota_exhausted"` means the Gemini
   account's prepaid credits are gone. Nothing in this repo fixes that: top up
   at <https://ai.studio/projects>, on the project whose key Railway holds.
   Service resumes within a minute or two of the balance landing, with no
   redeploy. Retrying and rolling back both do nothing.
2. Confirm at <https://status.cloud.google.com>.
3. Check the split: `outcome="blocked"` is content filtering (not an outage),
   `outcome="quota_exhausted"` is billing (above), `outcome="non_retryable"`
   usually means a bad API key, `outcome="no_price"` means the model answered
   but carried no usable valuation — see §5.9.
4. If the key is the problem, rotate it (§8.2).
5. There is currently **no fallback provider** `[NOT IMPLEMENTED]`. A Gemini
   outage is a full scan outage. This is the largest single-point-of-failure in
   the system — see "Remaining risks".
6. Users see *"The AI service is temporarily unavailable"* — accurate, and the
   client does not burn quota on a failed scan (`consume_quota` runs only after
   success).

### 5.4 Redis unreachable

*Blast radius:* quota and entitlement checks **fail closed** by design — a 503,
never a free scan.

1. `/health/ready` returns 503 and the instance drains itself.
2. Check the Redis provider dashboard.
3. Do **not** "fix" this by unsetting `REDIS_URL`. That flips the service into
   single-instance mode where memory is treated as authoritative, silently
   disabling the quota across every replica (see `cache.ResilientCache`).
4. The client reconnects automatically once Redis returns; no deploy needed.
5. If the outage is prolonged and free-tier revenue leakage is preferable to a
   full outage, that is a **deliberate, logged decision** — set
   `FREE_SCANS_PER_DAY=0` to make everyone Pro-gated rather than erroring.

### 5.5 Latency collapse

1. Check `model_duration_seconds` p95 first — the model dominates scan latency.
2. Check `upload_bytes` p50. A jump above ~1 MB means the client-side downscale
   regressed (`ScanAPIClient.encodeForUpload`), and every scan is paying upload
   time it should not.
3. Check `http_in_flight`. Sustained growth means requests are arriving faster
   than they complete — add containers.

### 5.6 Apple outage (DeviceCheck / StoreKit)

*Blast radius:* smaller than it looks, by design.

- **DeviceCheck down** → reinstall protection degrades open. `quota.note_exhausted`
  and `starting_balance` both swallow failures deliberately: Apple's availability
  must not gate our service. No action needed.
- **App Store server down** → `/auth/entitlement` verification is *offline* (the
  JWS is verified against a pinned Apple root CA locally), so existing Pro users
  are unaffected. Only brand-new purchases are impacted, and the client retries
  on every status refresh.

### 5.7 Confidence collapse

A sudden drop in median `confidence_score` after a deploy means a prompt or
model change degraded identification. It is a **quality** incident, not an
availability one.

1. Compare `confidence_score` and `valuation_clamped_total` before and after.
2. Roll back the prompt without a redeploy: `SCAN_PROMPT_VERSION=v1`.
3. Run the benchmark before shipping a fix (`docs/EVALUATION.md`).

### 5.8 Quota abuse

1. Check `rate_limited_total` and `quota_exhausted_total`.
2. Device id is client-supplied and trivially rotated — the real backstop is the
   per-IP limit (`IP_RATE_MAX_REQUESTS`, default 60/hr).
3. Tighten via env; no deploy needed if the platform supports variable updates
   with a restart.
4. Sustained abuse from one IP range needs a platform-level block; there is no
   application-level IP blocklist `[NOT IMPLEMENTED]`.

---

### 5.9 Valuations are wrong, not missing

*Symptom:* scans succeed but the numbers are nonsense — the signature case was
every item coming back as **$1–5**.

*Blast radius:* worse than an outage. A visible failure costs a scan; a
confident wrong number is the product being wrong, and users act on it.

1. Check `snapworth_model_calls_total{outcome="no_price"}`. Anything above zero
   means the model returned a reply with no usable price. Since 87e62c5 that is
   a 502, not an invented number — if this counter is climbing, the model or the
   prompt has regressed, not the pricing code.
2. Grep for `model response hit max_output_tokens — output truncated`. Truncation
   is the known cause: gemini-2.5-flash spends **reasoning** tokens out of
   `max_output_tokens`, measured at 1138–1777 per scan against a ~700-token
   payload. If the ceiling is squeezed, JSON truncates before the price fields,
   which sit two-thirds down the v2 schema.
3. Do not lower `GEMINI_MAX_OUTPUT_TOKENS` below **4096** — 2048 shipped and
   produced exactly this bug. It is a cap, not a spend: unused headroom is not
   billed, while truncated answers are billed in full and thrown away.
4. Re-check after any model change. A model with a larger reasoning appetite
   needs a larger ceiling, and the failure is silent by default.

## 6. Deployment

**Current:** `railway up --service snapworth-backend --detach` on push to main,
after tests pass (`.github/workflows/backend.yml`).

| Capability | State |
|---|---|
| Rolling deploy | Platform-provided |
| Graceful shutdown | `[DESIGNED]` — implemented, never exercised in production |
| Readiness gating | `[DESIGNED]` — `/health/ready` exists; must be configured as the platform's health path |
| Blue/green | `[NOT IMPLEMENTED]` |
| Canary | `[NOT IMPLEMENTED]` |
| Instant rollback | Railway redeploy of a previous build |
| Migrations | **None exist.** No relational database; Redis is a cache |
| Feature flags | Env-var based: `SCAN_PROMPT_VERSION`, `COMPS_ENABLED`, `COMPS_SHADOW_MODE`, `ALLOWED_STOREKIT_ENVIRONMENTS` |

### Shutdown sequence (implemented in `main._lifespan`)

1. SIGTERM reaches uvicorn as PID 1 — this only works because the Dockerfile
   uses `exec`; without it the shell swallows the signal.
2. Readiness flips to false → the load balancer stops sending new requests.
3. In-flight requests drain, up to `DRAIN_TIMEOUT_SECONDS` (default 15s).
4. DeviceCheck and Redis connections close.
5. `--timeout-graceful-shutdown 20` gives uvicorn room beyond the drain, and
   stays under Railway's 30s SIGKILL.

**Required platform configuration:** set the health-check path to
`/health/ready`. Without it the platform routes traffic to draining and
still-starting instances, and the graceful shutdown achieves nothing.

---

## 7. Rollback checklist

- [ ] Confirm the regression is deploy-correlated (compare against the previous
      release in `snapworth_build_info`)
- [ ] **Prompt-only regression?** Set `SCAN_PROMPT_VERSION=v1` — no redeploy
- [ ] **Comps-related?** Set `COMPS_ENABLED=false` — no redeploy
- [ ] Otherwise redeploy the previous Railway build
- [ ] Verify `/health/ready` returns 200
- [ ] Verify a real scan end-to-end
- [ ] No data migration to reverse — Redis is a cache and the client holds history

---

## 8. Secrets

| Secret | Rotation | Notes |
|---|---|---|
| `GEMINI_API_KEY` | On suspicion | §8.2 |
| `TOKEN_KEYS` | Quarterly | §8.1 — zero-downtime by design |
| `AUDIT_SALT` | Rarely | Rotating breaks historical correlation, deliberately |
| `DEVICECHECK_PRIVATE_KEY` | On suspicion | Apple Developer portal |
| TLS certificate | Automatic | Let's Encrypt, 90 days, platform-managed |

### 8.1 Token key rotation (zero downtime)

`tokens.TokenSigner` accepts every key for verification and signs with one, so
rotation needs no flag day:

1. `TOKEN_KEYS=old:secret1,new:secret2` — both valid, still signing with old
2. Wait one token lifetime (1 hour)
3. `TOKEN_CURRENT_KID=new` — now signing with new
4. Wait another hour, then drop `old`

### 8.2 Gemini key rotation

1. Mint a new key in Google AI Studio
2. Update `GEMINI_API_KEY`, restart
3. Verify `model_calls_total{outcome="success"}` recovers
4. Revoke the old key
5. CI already blocks committed keys (`.github/workflows/backend.yml`)

### 8.3 Provisioning DeviceCheck

DeviceCheck is what stops a reinstall from resetting the free-scan allowance.
Unset, the service runs fine and every reinstall gets a fresh allowance —
`🩺 Checkup` says so.

**In the Apple Developer portal** (Certificates, Identifiers & Profiles → Keys):

1. **+**, name it (e.g. `SnapWorth DeviceCheck`), tick **DeviceCheck**, Continue → Register.
2. **Download the `.p8`. Apple lets you download it once**, and it cannot be
   re-issued — only revoked and replaced.
3. Note the **Key ID** on that page, and the **Team ID** from Membership.

**In Railway** — three variables, not two:

| Variable | Value |
|---|---|
| `APPLE_TEAM_ID` | Team ID (already set if App Attest is enforcing) |
| `DEVICECHECK_KEY_ID` | the Key ID from step 3 |
| `DEVICECHECK_PRIVATE_KEY` | the whole `.p8` file, `BEGIN`/`END` lines included |

The private key may be pasted with literal `\n` instead of newlines; the client
converts them (`devicecheck.py`, `__init__`). All three must be non-empty or
`is_configured` stays False and the checks are skipped.

**The newlines are what usually breaks.** A panel that flattens the `.p8` to one
line produces the same bare `ValueError` from `cryptography` as a truncated or
body-only key, so the checkup names the shape instead:

| Checkup says | Fix |
|---|---|
| `…is on a single line — its newlines were lost` | re-paste with real line breaks, or with a literal `\n` between them |
| `…has no BEGIN/END lines` | paste the whole file, not just the base64 body |
| `private key unreadable — …` | the envelope is right but the contents are not a P-256 key; check it is the unencrypted `.p8` Apple issued |
| `could not reach Apple (…)` | network, not credentials — nothing to change |

**Then verify — do not trust "configured".** `is_configured` only means the
three variables are non-empty, and *every* DeviceCheck failure degrades open
(§5.6), so a typo'd key silently hands every reinstall a fresh allowance.
Run `🩺 Checkup`:

- `DeviceCheck: configured ✅ — credentials accepted by Apple` — Apple signed off.
- `DeviceCheck: configured but REJECTED — key rejected …` — one of the three
  variables is wrong, or the key lacks the DeviceCheck capability.

The probe sends a deliberately fake device token: Apple reads the
Authorization header first, so a `400` about the token proves the key signs
while a `401` proves it does not. No device is involved.

**`DEVICECHECK_SANDBOX`**: leave unset. Device tokens from an Xcode-run debug
build belong to Apple's development environment and will be refused by the
production host — expected, and harmless because the path degrades open. Set it
only if you ever point a build at the sandbox deliberately; a stale `true` would
break DeviceCheck for real App Store users, silently.

### 8.4 DeviceCheck key rotation

**Add, verify, revoke — in that order.** Revoking first leaves DeviceCheck
failing for as long as it takes to paste the replacement, and it fails *open*
(§5.6): every reinstall in that window gets a fresh free allowance, silently.

1. Portal → Keys → **+**, tick **DeviceCheck**, Register, download the `.p8`.
2. Railway: set `DEVICECHECK_KEY_ID` and `DEVICECHECK_PRIVATE_KEY` to the new
   pair **together**. A half-swap — new key id, old key — is the mismatch that
   produces `Unable to verify authorization token`.
3. `🩺 Checkup` → `DeviceCheck: configured ✅ — credentials accepted by Apple`.
   Do not proceed on anything else; the rejection line names the team and key
   it signed with, which is what a half-swap looks like.
4. Only now: portal → the old key → **Revoke**.

**The stored bits survive.** DeviceCheck's two bits are held per device against
the *team*, not the key that wrote them, so a new key under the same
`APPLE_TEAM_ID` reads exactly what the old one wrote. Rotation costs no
reinstall protection — devices already marked stay marked.

Rotate when the key may have been exposed. The blast radius is small by
construction — a DeviceCheck key can read and write two bits per device and
nothing else, no user data and no App Store Connect access — so this is
housekeeping, not an incident, and step 3 matters more than speed.

---

## 9. Disaster recovery

**RPO/RTO are shaped by an unusual property: there is no system of record.**
Scan history lives on-device, and StoreKit transactions are re-verifiable
offline. Redis holds only derived state.

| Failure | Impact | Recovery | RTO |
|---|---|---|---|
| Container loss | None — stateless | Platform restarts | seconds |
| Total Redis loss | Quota resets; Pro users re-sync on next status refresh | Provision new instance, set `REDIS_URL` | ~15 min `[ESTIMATED]` |
| Gemini outage | Scans fail; everything else works | Wait, or add a fallback provider | Provider-dependent |
| Region failure | Full outage | Redeploy to another region | ~1 hour `[ESTIMATED]` |
| Certificate expiry | Full outage | Platform auto-renews; pinning is report-only so a mismatch cannot brick clients | — |
| Key compromise | Sessions invalid | Rotate `TOKEN_KEYS`, drop old immediately | ~10 min |

**Acceptable RPO for Redis is effectively total loss.** Quota resets to today's
allowance (a small revenue leak, not a correctness failure) and entitlements
re-derive from the client's signed transaction. Document this rather than
engineering Redis persistence for it.

---

## 10. Cost model `[ESTIMATED]`

> **Corrected 2026-09-09.** The previous version of this section was ~19×
> too low per scan and its headline conclusion was backwards. Two errors:
> it used $0.075/1M input and $0.30/1M output, against the $0.30/$2.50 this
> codebase actually bills at (`notify.py:88-89`), and it omitted **thinking
> tokens entirely** — which are billed as output and are the single largest
> line item. It also said the free tier was 3/day; it has been 1/day since
> `quota.py:33`. Nothing has been decided on the strength of the old numbers,
> but anyone planning pricing or a free-tier change from them would have been
> badly misled.

**Assumptions — these dominate the result and none is measured:**

- 30% of installs become monthly actives
- 4 scans/active/month (free tier is 1/day; most users scan far less)
- Gemini 2.5 Flash: **~$0.30/1M input, ~$2.50/1M output**, matching
  `GEMINI_PRICE_INPUT_PER_M` / `GEMINI_PRICE_OUTPUT_PER_M` `[2026-09]`
- Per scan: ~260 image + ~700 prompt = ~960 input; ~800 answer **plus
  ~1,450 thinking tokens** (measured 1,138–1,777) = ~2,250 output
- **~$0.0059/scan**, which agrees with `quota.py:33`'s own ~$0.0060 figure and
  with today's observed $0.02–0.06/day across 1–4 scans
- 3% paid conversion at $39.99/yr blended

| Users | MAU | Scans/mo | Gemini/mo | Infra/mo | Total/mo | Revenue/mo | Margin |
|---|---|---|---|---|---|---|---|
| 10k | 3k | 12k | ~$71 | ~$25 | **~$96** | ~$1,000 | 90% |
| 100k | 30k | 120k | ~$710 | ~$120 | **~$830** | ~$10,000 | 92% |
| 1M | 300k | 1.2M | ~$7,100 | ~$800 | **~$7,900** | ~$100,000 | 92% |

**Inference dominates, not infrastructure** — the opposite of what this section
used to say. Gemini is roughly 3× the container bill at 10k users and ~9× at
1M, and **~61% of the model spend is thinking tokens, not the answer** — 64%
of the output tokens, which at $2.50/M output against $0.30/M input is where
the money goes. (This read 75% when the section was rewritten on 09-09. That
figure did not follow from the assumptions directly above it: 1,450 thinking
tokens at $2.50/M is $0.0036 of a $0.0059 scan. Corrected 09-10.)
Optimisation effort belongs in what the model is asked to reason about, not in
container efficiency. Margins stay healthy either way; the ranking of what to
work on does not.

### Optimisations, ranked by value

1. **Result caching by image hash** `[NOT IMPLEMENTED]` — users re-scan the same
   item. A 7-day cache on the image digest would cut both cost and latency, and
   is the single highest-value item here.
2. **Client-side downscale** `[MEASURED]` — already shipped; cut upload ~92%.
3. **Thinking budget** `[KNOB ADDED, UNSET]` — `GEMINI_THINKING_BUDGET` caps
   the reasoning tokens that are ~61% of model spend. Deliberately unset, so
   today's behaviour is unchanged: capping reasoning on a valuation model is a
   quality decision and belongs to `backend/eval/runner.py`, run at a candidate
   budget and compared, not to a number picked here. This is the highest-value
   *cost* lever in the list and the one most able to damage the product.
4. **Prompt length** — v2 is ~700 tokens of the ~960 input. Input is ~5% of
   per-scan cost, so trimming saves ~$85/mo at 1M users; not worth degrading
   output for. (The old model put this at ~$40/mo on prices 4× too low.)
5. **Retry discipline** `[MEASURED]` — non-retryable errors are no longer
   retried, halving the cost of a bad-key incident.
6. **`_retry_as_json` second call** — fires on unparseable output. Constrained
   JSON decoding made it rare; monitor `model_calls_total` before optimising.
   It now goes through `_generate_with_retry` like every other call, so a
   provider outage during it is classified as one rather than reported to the
   user as an unreadable reply.

---

## 11. Scaling audit

| Area | State | Note |
|---|---|---|
| Async correctness | ✅ | No blocking I/O on the event loop |
| Redis pooling | ✅ | `max_connections=50`, bounded timeouts |
| DeviceCheck pooling | ✅ Fixed | Was a new TLS handshake per call |
| Worker count | 1/container | Correct for I/O-bound work; scale by containers |
| Rate limiting | ✅ | Redis-backed, Lua-atomic; degrades to per-process |
| Cold start | ~2-3s `[ESTIMATED]` | Dominated by imports |
| Backpressure | ⚠️ Partial | Rate limits only; no queue-depth shedding |
| Autoscaling | Platform | Scale on **p95 latency, not CPU** — the service is I/O-bound, so CPU stays flat while requests queue |
| Thread safety | ✅ | Metrics under lock; no shared mutable state elsewhere |

---

## 12. Launch checklist

**Blocking**

- [ ] `REDIS_URL` set and reachable
- [ ] `TOKEN_KEYS` + `TOKEN_CURRENT_KID` set
- [ ] `ENVIRONMENT=production` (enables strict startup checks)
- [ ] `AUDIT_SALT` set to a real value
- [x] ~~`TRUSTED_PROXY=true`~~ — **no longer read.** `_client_ip` now always takes the
      rightmost `X-Forwarded-For` hop, so the per-IP limit no longer depends on this
      variable being remembered. The old note here was also wrong about the failure:
      unset did not collapse everyone into one bucket, it gave each caller a bucket of
      their own choosing (uvicorn runs with `--forwarded-allow-ips='*'`, which makes
      `request.client.host` the client-supplied hop). Safe to delete from Railway.
- [ ] `ALLOWED_STOREKIT_ENVIRONMENTS=Production`
- [ ] `LOG_FORMAT=json`
- [ ] Platform health-check path set to `/health/ready`
- [ ] **App Store screenshots corrected** — see `marketing/SCREENSHOT-COMPLIANCE.md`

**Should-have**

- [ ] `METRICS_TOKEN` set in the production environment — **`/metrics` fails
      closed and returns 404 until it is**, so set it before or with the deploy
      that ships the guard, or observability goes dark
- [ ] Metrics collector scraping `/metrics` — must send
      `Authorization: Bearer $METRICS_TOKEN`
- [ ] Alerts configured from §3
- [ ] On-call rota and escalation path
- [ ] Load test at 10× expected peak
- [ ] Gold dataset + recorded baseline (`docs/EVALUATION.md`)

---

## 13. On-call checklist

**Start of shift**
- [ ] `/health/ready` returns 200
- [ ] No firing alerts
- [ ] Last deploy is green

**During an incident**
- [ ] Note the request id from the first failing report
- [ ] Classify: dependency / internal / capacity (§3)
- [ ] If deploy-correlated, roll back before diagnosing
- [ ] Record the timeline as you go

**Security incident**
- [ ] Rotate the suspected credential first (§8)
- [ ] Pull the audit trail: `logger=snapworth.audit`, subjects pseudonymised
- [ ] Check `attest.failed`, `token.rejected`, `entitlement.rejected` rates
- [ ] Preserve logs before they age out
- [ ] Assess whether GDPR notification applies — no PII is stored, which
      substantially narrows the analysis

---

## 14. Changing the introductory offer

The offer lives in **App Store Connect**, not in this repository. Changing it
there takes effect for users immediately, with no build and no deploy — so
anything here that *names* the offer goes stale silently.

Two surfaces read the live offer from StoreKit and need nothing:

- the **paywall** — headline, subheadline, plan card and CTA all branch on
  `IntroOffer.kind`, and an unrecognised payment mode renders nothing rather
  than guessing (`PaywallCopy`, `StoreKitPurchaseService.introOffer`)
- the **"trial ends tomorrow" reminder** — suppressed unless the product's
  offer is genuinely `freeTrial`

Two surfaces deliberately state the *rule* rather than the offer, and are
guarded by tests that fail if a concrete duration reappears:

- in-app Terms of Service (`TermsCopy.subscriptions`)
- `GET /terms` (`backend/main.py`)

Everything below hardcodes today's offer on purpose — marketing copy is
expected to name the current deal — and is **yours to update by hand** the
same day you change it in App Store Connect:

- [ ] `website/index.html` — plan cadence line, Pro feature list, the "Is it
      really free?" FAQ answer, **and the same answer again in the JSON-LD
      `FAQPage` block** (it is duplicated; search for the trial phrase and
      expect more than one hit)
- [ ] `website/support.html` — the cancellation paragraph
- [ ] `marketing/app_store_listing.md` — the yearly plan line
- [ ] App Store Connect listing description itself

Check with:

```
grep -rn "free trial" website/ marketing/
```

If the new offer is **paid** (`payUpFront` or `payAsYouGo`), the word "free"
must not survive anywhere in that grep.
