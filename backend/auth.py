"""Authentication: challenge → attest → token → authorised request.

Wires App Attest, DeviceCheck, tokens, entitlements and quota into a single
FastAPI dependency plus the three endpoints the client needs.

Flow
----
1. ``POST /auth/challenge`` — server mints a single-use nonce (cached, TTL'd).
2. ``POST /auth/attest`` — client returns the App Attest object over that nonce.
   Server verifies it, stores the public key and counter, issues a bearer token.
3. ``POST /auth/refresh`` — client proves possession with an *assertion* over a
   fresh challenge; server checks the counter advanced and re-issues.

Every protected route then depends on ``require_auth``.

Why a token rather than an assertion per request: assertion verification is a
signature check plus a monotonic-counter write, which serialises concurrent
requests from one device. A short-lived token keeps the hot path stateless.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import base64
import json
import logging
import os
import secrets
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

import appattest
import auditlog
import notify
import ratelimit
from cache import KeyValueStore
from devicecheck import DeviceCheckClient
from auditlog import AuditEvent
from entitlements import EntitlementError, EntitlementService
from entitlements import EntitlementsUnavailable
from quota import QuotaExceeded, QuotaStatus, QuotaUnavailable, ScanQuota
from tokens import TokenError, TokenSigner

log = logging.getLogger("snapworth.auth")

CHALLENGE_TTL = 300           # 5 min: long enough for a slow device, short
                              # enough that a captured nonce is near-useless.
ATTEST_STATE_TTL = 60 * 60 * 24 * 400     # key material outlives any token


class AuthConfig:
    """Resolved once at startup."""

    def __init__(self) -> None:
        self.team_id = os.environ.get("APPLE_TEAM_ID", "").strip()
        self.bundle_id = os.environ.get("APPLE_BUNDLE_ID", "eu.snapworth.app").strip()
        self.allow_development = os.environ.get(
            "APP_ATTEST_ALLOW_DEV", "").lower() in {"1", "true", "yes"}
        # The single switch that turns enforcement on. Defaults to *off* so a
        # deploy without Apple credentials degrades to today's behaviour rather
        # than locking every user out.
        self.enforce = os.environ.get("REQUIRE_APP_ATTEST", "").lower() in {"1", "true", "yes"}

    @property
    def app_id(self) -> str:
        return f"{self.team_id}.{self.bundle_id}"

    @property
    def is_configured(self) -> bool:
        return bool(self.team_id and self.bundle_id)


@dataclass
class Principal:
    """The authenticated caller for one request."""
    subject: str                  # App Attest key id (hex), or a device-id fallback
    tier: str                     # "free" | "pro"
    authenticated: bool           # True only when a verified token was presented
    device_token: str | None = None

    @property
    def is_pro(self) -> bool:
        return self.tier == "pro"


# ── Container, populated at startup ──────────────────────────────────────────

class AuthDeps:
    # Every field except `config` is populated in `main._lifespan` before the
    # app accepts traffic. They stay Optional because two of them genuinely can
    # be None after startup: `signer` is branched on below, and the tests wire
    # `device_check = None` deliberately.
    #
    # `cache` and `device_check` previously had no annotation at all, so they
    # inferred as the type `None` and every assignment in _lifespan and in the
    # test fixtures was reported as an error.
    # Split deliberately by whether None is a real state after startup.
    #
    # These three are always populated by `main._lifespan` before the app
    # accepts traffic, and nothing branches on them being None. Declared
    # without a default so that is the type: reading one before startup now
    # raises AttributeError naming the field, instead of returning None and
    # failing later as "NoneType has no attribute" inside a handler.
    cache: KeyValueStore
    entitlements: EntitlementService
    quota: ScanQuota

    # These two genuinely can be None after startup — `signer` is branched on
    # below, and the test fixtures wire `device_check = None` on purpose.
    signer: TokenSigner | None = None
    device_check: DeviceCheckClient | None = None

    # Per-IP limiter for the three unauthenticated routes below. Injected by
    # `main._lifespan` because the limiter lives in `main`, which imports this
    # module — a direct import would be circular. `None` means "not wired
    # yet", which only happens before startup and in tests that do not care.
    ip_limiter: Callable[[str | None], Awaitable[None]] | None = None

    # Per-subject limiter for `/entitlement`, injected the same way and for the
    # same reason. That route is authenticated so it never reached
    # `ip_limiter`, and it had no limit of its own — a valid device could ask
    # for an unbounded number of certificate-chain verifications.
    entitlement_limiter: Callable[[str, str | None], Awaitable[None]] | None = None

    config: AuthConfig = AuthConfig()


deps = AuthDeps()


# ── Request/response models ──────────────────────────────────────────────────

class ChallengeResponse(BaseModel):
    challenge: str
    expires_in: int


class AttestRequest(BaseModel):
    key_id: str = Field(min_length=1, max_length=256)          # base64
    attestation: str = Field(min_length=1, max_length=32_768)  # base64
    challenge: str = Field(min_length=1, max_length=256)
    device_token: str | None = Field(default=None, max_length=4096)


class AssertRequest(BaseModel):
    key_id: str = Field(min_length=1, max_length=256)
    assertion: str = Field(min_length=1, max_length=8_192)
    challenge: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    tier: str
    free_scans_remaining: int
    # The device's own pseudonym — `auditlog.pseudonymise(subject)`, the same
    # value the `/users` and `/subs` indexes are keyed on and the argument
    # `/user <id>` takes. The client cannot derive it: the hash is salted
    # server-side, deliberately. That left the operator's only support-facing
    # command unusable from a support email, because the email carried no id
    # of any kind. Sending it here lets the app quote it in a bug report.
    #
    # Not a secret and not a credential: a salted truncated hash of a subject
    # the client already holds, which authenticates nothing on its own.
    support_id: str


class EntitlementRequest(BaseModel):
    signed_transaction: str = Field(min_length=1, max_length=16_384)
    # A stable per-device identifier — Keychain-backed on iOS 1.3.4+, so it
    # survives reinstall where the attestation subject does not. Optional:
    # older clients omit it and are bound by subject, as before.
    device_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class EntitlementResponse(BaseModel):
    tier: str
    expires_at: int | None = None
    access_token: str | None = None


router = APIRouter(prefix="/auth", tags=["auth"])


def _challenge_key(value: str) -> str:
    return f"chal:{value}"


def _state_key(key_id: str) -> str:
    return f"attest:{key_id}"


def _device_token_key(subject: str) -> str:
    """DeviceCheck token for a subject, stored separately from attest state.

    Kept out of the attestation blob deliberately: `require_auth` runs on every
    request and must not pay an extra cache round-trip for a value that is only
    needed on the rare quota-exhaustion path. The lookup happens there instead.
    """
    return f"dct:{key_id_safe(subject)}"


def key_id_safe(subject: str) -> str:
    return subject[:128]


async def _issue_token(subject: str, device_token: str | None) -> TokenResponse:
    # `signer` is legitimately Optional — the callers below guard on it — but
    # reaching here without one means the service is misconfigured rather than
    # the request being bad, so fail loudly rather than 500 on a None attribute.
    signer = deps.signer
    if signer is None:
        raise RuntimeError("auth.deps.signer is not configured")

    try:
        ent = await deps.entitlements.current(subject)
    except EntitlementsUnavailable:
        # There is no prior token to fall back on here — this call *creates*
        # one — so minting "free" would bake a wrong tier in for the token's
        # whole lifetime. Refuse instead; the client already retries a mint.
        log.error("entitlement store unavailable while issuing a token")
        raise HTTPException(
            status_code=503,
            detail="Subscription status is temporarily unavailable. Please try again shortly.",
        ) from None
    token, claims = signer.mint(subject, tier=ent.tier)

    remaining = 0
    try:
        await deps.quota.starting_balance(subject, device_token)
        status = await deps.quota.status(subject, ent.tier == "pro")
        remaining = status.remaining
    except QuotaUnavailable:
        # Surfaced to the client as zero; the scan endpoint fails closed too.
        log.error("quota backend unavailable while issuing token")

    auditlog.record(AuditEvent.TOKEN_ISSUED, subject, tier=ent.tier, kid=claims["kid"])
    return TokenResponse(
        access_token=token,
        expires_in=claims["exp"] - claims["iat"],
        tier=ent.tier,
        free_scans_remaining=min(remaining, 10_000),
        support_id=auditlog.pseudonymise(subject),
    )


async def _limit_unauthenticated(request: Request) -> None:
    """Rate-limit the three routes that run before there is a principal.

    `_enforce_limits` is only reachable once a caller is authenticated, so
    these three had no limiter at all: `/challenge` wrote a Redis key per
    call with no principal behind it, and `/attest` ran a full x.509 chain
    walk unthrottled. Both are cheap individually — the point is that nothing
    bounded how many of them one source could ask for.
    """
    if deps.ip_limiter is None:
        return
    # `ratelimit.client_ip`, not `request.client.host`.
    #
    # uvicorn runs with `--forwarded-allow-ips='*'`, so `request.client.host`
    # is the *leftmost* `X-Forwarded-For` hop — entirely client-supplied. These
    # three routes were therefore keyed on a value the caller picks per
    # request, which is a fresh bucket on demand rather than a limit, while
    # `/scan`, `/trends` and `/listing` were keyed on the rightmost hop all
    # along. `main._client_ip`'s own docstring describes this exact trap; the
    # routes added later just did not get it, because the helper lived in a
    # module `auth` cannot import. It is in `ratelimit` now.
    await deps.ip_limiter(ratelimit.client_ip(request))


@router.post("/challenge", response_model=ChallengeResponse)
async def challenge(request: Request) -> ChallengeResponse:
    """Mint a single-use nonce for attestation or assertion."""
    await _limit_unauthenticated(request)
    value = secrets.token_urlsafe(32)
    await deps.cache.set(_challenge_key(value), "1", CHALLENGE_TTL)
    auditlog.record(AuditEvent.ATTEST_CHALLENGE_ISSUED)
    return ChallengeResponse(challenge=value, expires_in=CHALLENGE_TTL)


async def _consume_challenge(value: str) -> None:
    """Single-use: a nonce is deleted the moment it is accepted."""
    key = _challenge_key(value)
    present = await deps.cache.get(key)
    if not present:
        raise HTTPException(status_code=400, detail="Challenge is unknown or expired.")
    await deps.cache.delete(key)


@router.post("/attest", response_model=TokenResponse)
async def attest(req: AttestRequest, request: Request) -> TokenResponse:
    await _limit_unauthenticated(request)
    cfg = deps.config
    if not cfg.is_configured:
        raise HTTPException(status_code=503, detail="Attestation is not configured.")

    await _consume_challenge(req.challenge)

    try:
        key_id = base64.b64decode(req.key_id, validate=True)
        attestation = base64.b64decode(req.attestation, validate=True)
    except Exception:
        auditlog.record(AuditEvent.ATTEST_FAILED, outcome="failure", reason="bad_encoding")
        raise HTTPException(status_code=400, detail="Malformed attestation payload.") from None

    try:
        result = appattest.verify_attestation(
            attestation=attestation,
            challenge=req.challenge.encode(),
            key_id=key_id,
            app_id=cfg.app_id,
            allow_development=cfg.allow_development,
        )
    except appattest.AttestationError as exc:
        auditlog.record(AuditEvent.ATTEST_FAILED, outcome="failure", reason=str(exc))
        raise HTTPException(status_code=401, detail=str(exc)) from None

    subject = key_id.hex()
    await deps.cache.set(_state_key(subject), json.dumps({
        "public_key": result.public_key_pem.decode(),
        "counter": result.counter,
        "environment": result.environment,
    }), ATTEST_STATE_TTL)

    # Retained so the quota layer can mark the *hardware* when the free
    # allowance runs out — the per-install counter cannot survive a reinstall,
    # the DeviceCheck bit can. Best-effort: a failure here must not fail attest.
    if req.device_token:
        try:
            await deps.cache.set(_device_token_key(subject), req.device_token,
                                 ATTEST_STATE_TTL)
        except Exception as exc:
            log.warning("could not persist devicecheck token: %s", exc)

    auditlog.record(AuditEvent.ATTEST_SUCCEEDED, subject, environment=result.environment)
    return await _issue_token(subject, req.device_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: AssertRequest, request: Request) -> TokenResponse:
    """Re-issue a token by proving possession of the attested key."""
    await _limit_unauthenticated(request)
    cfg = deps.config
    await _consume_challenge(req.challenge)

    try:
        key_id = base64.b64decode(req.key_id, validate=True)
        assertion = base64.b64decode(req.assertion, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed assertion payload.") from None

    subject = key_id.hex()
    raw_state = await deps.cache.get(_state_key(subject))
    if not raw_state:
        # Unknown key: the client must attest again.
        raise HTTPException(status_code=401, detail="Unknown key. Re-attestation required.")
    state = json.loads(raw_state)

    try:
        new_counter = appattest.verify_assertion(
            assertion=assertion,
            challenge=req.challenge.encode(),
            public_key_pem=state["public_key"].encode(),
            app_id=cfg.app_id,
            previous_counter=int(state.get("counter", 0)),
        )
    except appattest.AttestationError as exc:
        auditlog.record(AuditEvent.ATTEST_FAILED, subject, outcome="failure", reason=str(exc))
        raise HTTPException(status_code=401, detail=str(exc)) from None

    state["counter"] = new_counter
    await deps.cache.set(_state_key(subject), json.dumps(state), ATTEST_STATE_TTL)
    return await _issue_token(subject, None)


async def require_auth(
    request: Request,
    authorization: str = Header(default=""),
    x_device_id: str = Header(default="anonymous", alias="x-device-id"),
) -> Principal:
    """FastAPI dependency enforcing authentication on protected routes.

    When `REQUIRE_APP_ATTEST` is off the request is allowed through as an
    unauthenticated free-tier principal keyed on the legacy device id, so
    existing installs keep working during rollout. When it is on, a valid token
    is mandatory.
    """
    cfg = deps.config
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token and deps.signer is not None:
        try:
            claims = deps.signer.verify(token)
        except TokenError as exc:
            auditlog.record(AuditEvent.TOKEN_REJECTED, outcome="failure", reason=str(exc))
            raise HTTPException(status_code=401, detail=str(exc)) from None

        subject = claims["sub"]
        # Entitlement is re-read from the cache rather than trusted from the
        # token, so an expired or refunded subscription stops working within
        # one cache TTL instead of one token lifetime.
        try:
            ent_tier = (await deps.entitlements.current(subject)).tier
        except EntitlementsUnavailable as exc:
            # The store is down, not empty. Reading that as "free" took Pro
            # away from paying subscribers mid-outage. This token was minted
            # with a tier this service verified, and it expires — so trusting
            # it for the rest of its own lifetime is bounded, and strictly
            # better than guessing "free".
            ent_tier = claims.get("tier") or "free"
            log.warning("entitlement store unavailable, honouring the token's tier: %s",
                        exc, extra={"tier": ent_tier})
        notify.saw_user(subject, tier=ent_tier)
        return Principal(subject=subject, tier=ent_tier, authenticated=True)

    if cfg.enforce:
        auditlog.record(AuditEvent.TOKEN_REJECTED, outcome="failure", reason="missing_token")
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Legacy, pre-enforcement path.
    subject = f"legacy:{x_device_id[:64]}"
    notify.saw_user(subject)
    return Principal(subject=subject, tier="free", authenticated=False)


@router.post("/entitlement", response_model=EntitlementResponse)
async def record_entitlement(
    req: EntitlementRequest,
    request: Request,
    principal: Principal = Depends(require_auth),
) -> EntitlementResponse:
    """Verify a StoreKit signed transaction and upgrade the caller to Pro.

    Re-issues a token so the new tier takes effect immediately rather than at
    the next refresh.
    """
    # `/scan` and `/trends` are limited; this was not, despite doing more work
    # per call than either — a three-certificate chain walk with an ECDSA
    # verification per link. See `main._enforce_entitlement_limit` for why it
    # gets its own generous bucket rather than the scan one.
    if deps.entitlement_limiter is not None:
        await deps.entitlement_limiter(principal.subject,
                                       ratelimit.client_ip(request))
    # No 409 branch: the device cap now evicts the least-recently-seen binding
    # instead of refusing. It refused for as long as it existed, and because an
    # App Attest key is per install rather than per device, reinstalling burned
    # a slot permanently — six of them locked a paying subscriber out of their
    # own subscription, silently, because the client swallows this response.
    try:
        ent = await deps.entitlements.record(
            principal.subject, req.signed_transaction, device_id=req.device_id)
    except EntitlementError as exc:
        auditlog.record(AuditEvent.ENTITLEMENT_REJECTED, principal.subject,
                        outcome="failure", reason=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from None

    auditlog.record(AuditEvent.ENTITLEMENT_RECORDED, principal.subject,
                    tier=ent.tier, product_id=ent.product_id)
    # Operator ping: first sighting of a subscription, or a proven downgrade.
    # Deduped and throttled inside; never raises, so it cannot fail the sync.
    await notify.entitlement_recorded(principal.subject, ent)

    access_token = None
    if deps.signer is not None and principal.authenticated:
        access_token, _ = deps.signer.mint(principal.subject, tier=ent.tier)

    return EntitlementResponse(
        tier=ent.tier, expires_at=ent.expires_at, access_token=access_token)


async def _device_token_for(subject: str) -> str | None:
    """Best-effort lookup of the DeviceCheck token captured at attestation."""
    try:
        return await deps.cache.get(_device_token_key(subject))
    except Exception as exc:
        log.warning("devicecheck token lookup failed: %s", exc)
        return None


async def reserve_quota(principal: Principal) -> QuotaStatus | None:
    """Claim one free scan, or raise 402. Fails closed.

    This used to be `enforce_quota`, which only *read* the counter and left
    the increment to `consume_quota` after the model call — so concurrent
    scans from one device all read the same pre-scan count and all passed.
    The claim now happens up front and is handed back by `refund_quota` if the
    work fails, which is the same guarantee from the user's side and a real
    one from the counter's.

    Returns the post-reservation status so the caller can report the true
    remaining allowance; `None` only for Pro, which has no count.
    """
    if principal.is_pro:
        return None
    try:
        return await deps.quota.reserve(principal.subject, principal.is_pro)
    except QuotaExceeded as exc:
        auditlog.record(AuditEvent.QUOTA_EXCEEDED, principal.subject, outcome="denied")
        # Countable, not just audited. The audit log is per-event and nothing
        # aggregates it, so the server half of the free-scan funnel did not
        # exist — the FREE_SCANS_FIRST_DAY experiment was being measured by
        # the client alone, with no way to cross-check it.
        notify.count_limit_hit()
        # Mark the physical device as having spent its allowance. Without this
        # the reinstall defence in `ScanQuota.starting_balance` reads a bit that
        # nothing ever sets, so delete-and-reinstall mints a fresh allowance
        # indefinitely. Swallows its own failures by design.
        token = principal.device_token or await _device_token_for(principal.subject)
        await deps.quota.note_exhausted(token)
        raise HTTPException(
            status_code=402,
            detail=exc.message,
            headers={"X-Quota-Resets-At": str(exc.resets_at)},
        ) from None
    except QuotaUnavailable:
        # Durable state is the only source of truth for a paid resource.
        raise HTTPException(
            status_code=503,
            detail="Scan quota is temporarily unavailable. Please try again shortly.",
        ) from None


async def refund_quota(principal: Principal,
                       status: QuotaStatus | None = None) -> None:
    """Hand back a reservation whose work produced nothing.

    Pass the `QuotaStatus` that `reserve_quota` returned. It carries the UTC
    day the reservation was counted against, and without it the refund lands
    on whichever day the failure happened in — which across midnight is the
    wrong counter. Optional only so a caller with no reservation in hand still
    compiles; every real one has it.

    Never raises: the request has already failed, and a failed refund is
    exactly the outcome the previous check-then-consume code produced on
    *every* failure. Not worth a second error on top of the first.
    """
    try:
        await deps.quota.refund(principal.subject, principal.is_pro,
                                day=status.day if status else None)
    except Exception as exc:                                # pragma: no cover
        log.error("quota refund failed — user charged for a failed scan: %s", exc,
                  extra={"subject": auditlog.pseudonymise(principal.subject)})


def record_quota_consumed(principal: Principal) -> None:
    """Audit-log a reservation that went on to produce a real result."""
    auditlog.record(AuditEvent.QUOTA_CONSUMED, principal.subject)
