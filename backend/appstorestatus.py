"""App Store Server API — asking Apple what a subscription is doing *now*.

`appstorenotify` is Apple talking to us; this is us talking to Apple. The two
answer different questions and neither replaces the other:

  * A notification arrives once, when something changes. If the endpoint was
    down, if the row was capped out of the index, or if the change predates
    the webhook being configured at all, the operator's view is simply wrong
    and nothing will correct it — there is no redelivery to wait for.
  * This asks about one subscriber, on demand, and gets the current truth.

That makes it the tool for a support mail: someone says they paid and the app
says free, and the index cannot settle it because the index is a cache of
things we happened to be told.

**This module never grants access.** Same rule as `appstorenotify`, for the
same reason, and it matters more here — this one can be pointed at any
transaction id by whoever can type into the operator chat. Entitlement stays
verified per request against the signed transaction the client presents.

Trust anchor: `SignedDataVerifier` is handed `entitlements.APPLE_ROOT_CA_G3_PEM`,
the same pinned root `verify_apple_jws` uses. Apple's library verifies the
chain with its own implementation, which is unavoidable when the verification
happens inside the library — but it verifies against *our* root, so there is
one thing to rotate rather than two.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import entitlements
from entitlements import Entitlement

log = logging.getLogger("snapworth.appstorestatus")

# Apple's own words for what a subscription is doing, mapped to the operator's.
# The raw enum is an integer and the bot prints these to a human, so the
# translation lives here rather than at the call site.
#
# BILLING_RETRY and BILLING_GRACE_PERIOD are kept apart deliberately. Both mean
# "the card failed", but only grace period still entitles the customer — which
# is the difference between "they have lost access" and "they have not noticed
# yet", and the whole reason someone is reading this line during a support
# mail.
STATUS_WORDS = {
    1: "active",
    2: "expired",
    3: "billing retry",
    4: "grace period",
    5: "revoked",
}

# How long to wait on Apple. The operator is watching a chat window; a request
# that hangs for the library's default 30s reads as a broken bot.
TIMEOUT_SECONDS = 15.0


class StatusError(Exception):
    """Base for every failure of a live status lookup.

    Every subclass below carries a message written for the person who typed
    the command, because that is where it is displayed. None of them is caught
    and swallowed — an unexplained "no result" is what sends someone to check
    App Store Connect by hand.
    """


class StatusNotConfigured(StatusError):
    """No In-App Purchase credentials on this deployment."""


class StatusCredentialsRejected(StatusError):
    """Apple refused the key — 401, or an unreadable .p8."""


class SubscriberNotFound(StatusError):
    """Apple has no subscription under that id, in either environment."""


class StatusRateLimited(StatusError):
    """Apple is rate limiting us (429)."""


class StatusUnavailable(StatusError):
    """Apple could not be reached, or answered something unrecognised."""


@dataclass(frozen=True)
class SubscriptionStatus:
    """One subscription, as Apple currently sees it.

    Wraps `Entitlement` rather than restating it: product, expiry, environment,
    offer type, price and revocation are all already modelled there, and a
    second shape for the same facts is how two views of one subscription drift
    apart. What is added is only what a *signed transaction alone cannot say* —
    Apple's status enum and the renewal info, which live in a separate JWS.

    The entitlement is built with the same semantics as the notification path:
    it reports what the transaction says, expired or not. Collapsing an expired
    subscription to FREE would make "expired" and "never existed" the same
    answer, and telling those apart is the entire point of asking.
    """

    entitlement: Entitlement
    # Apple's `status`, already translated by STATUS_WORDS. Falls back to the
    # raw integer's repr if Apple adds a sixth value before we do.
    state: str
    # None when Apple sent no renewal info. That is normal for a subscription
    # that has already ended — there is nothing left to renew — so it must be
    # distinguishable from "auto-renew is off", which is a live subscription
    # someone has cancelled.
    auto_renew: bool | None
    # The offer *code* the customer typed, or the promotional offer id. Apple
    # calls it offerIdentifier; `Entitlement.offer_type` already says which
    # kind of offer it was.
    offer_identifier: str | None = None
    # What it will renew *to*, when that differs from the current product —
    # an upgrade or downgrade takes effect at the period boundary, so this is
    # the only advance warning that the plan is about to change.
    auto_renew_product_id: str | None = None

    @property
    def environment(self) -> str:
        return self.entitlement.environment

    @property
    def product_id(self) -> str | None:
        return self.entitlement.product_id

    @property
    def expires_at(self) -> int | None:
        return self.entitlement.expires_at


@dataclass(frozen=True)
class _Credentials:
    issuer_id: str
    key_id: str
    private_key: bytes
    bundle_id: str
    app_apple_id: int


def _read_private_key() -> bytes | None:
    """The .p8, from the environment or from disk. Contents win.

    Two sources because the two places this runs disagree — see `.env.example`.
    Contents take precedence so that a deployment which sets both (a stale path
    left over from local work, say) uses the one the hosting panel manages,
    rather than a file that may not exist there at all.
    """
    pasted = os.environ.get("APPLE_IAP_PRIVATE_KEY", "").strip()
    if pasted:
        # The same `\n`-escaping DEVICECHECK_PRIVATE_KEY documents: hosting
        # panels store a single line, and the key is useless with its newlines
        # flattened.
        return pasted.replace("\\n", "\n").encode()

    path = os.environ.get("APPLE_IAP_PRIVATE_KEY_PATH", "").strip()
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), "rb") as handle:
            return handle.read()
    except OSError as exc:
        # Named, not swallowed. A wrong path and an absent configuration are
        # very different problems and produced the same silence before.
        raise StatusNotConfigured(
            f"APPLE_IAP_PRIVATE_KEY_PATH is set but unreadable: {exc.strerror}."
        ) from None


def credentials_from_env() -> _Credentials | None:
    """Assemble the credentials, or None when this deployment has none.

    Unconfigured is a supported state, as it is for DeviceCheck: the webhook,
    the index and every other command keep working, and only `/sub` reports
    that it cannot run. A deployment should not fail to boot because an
    optional operator tool has no key.
    """
    issuer_id = os.environ.get("APPLE_IAP_ISSUER_ID", "").strip()
    key_id = os.environ.get("APPLE_IAP_KEY_ID", "").strip()
    app_apple_id = os.environ.get("APPLE_APP_APPLE_ID", "").strip()
    private_key = _read_private_key()

    if not (issuer_id and key_id and private_key and app_apple_id):
        return None

    if not app_apple_id.isdigit():
        # The commonest way to get this wrong is to paste the bundle id, which
        # would otherwise surface as a ValueError from deep inside the library.
        raise StatusNotConfigured(
            "APPLE_APP_APPLE_ID must be the app's numeric App Store id, not "
            f"the bundle id (got {app_apple_id!r}).")

    return _Credentials(
        issuer_id=issuer_id,
        key_id=key_id,
        private_key=private_key,
        bundle_id=os.environ.get("APPLE_BUNDLE_ID", "eu.snapworth.app").strip(),
        app_apple_id=int(app_apple_id),
    )


class AppStoreStatusClient:
    """Looks one subscriber up, in Production, then Sandbox.

    One instance per environment pair, built lazily. Apple's async client opens
    an `httpx.AsyncClient` in its constructor, so building one per lookup would
    leak a connection pool per command.
    """

    def __init__(self, credentials: _Credentials) -> None:
        self._credentials = credentials
        # Keyed by environment. Built on first use so that importing this
        # module never parses a key or opens a socket.
        self._clients: dict[str, object] = {}
        self._verifiers: dict[str, object] = {}
        self._lock = asyncio.Lock()

    # ── Library objects, built on demand ────────────────────────────────────

    def _environments(self):
        """Production first, then Sandbox. Order is the fallback."""
        from appstoreserverlibrary.models.Environment import Environment
        return (("Production", Environment.PRODUCTION),
                ("Sandbox", Environment.SANDBOX))

    def _client_for(self, name: str, environment):
        from appstoreserverlibrary.api_client import AsyncAppStoreServerAPIClient
        if name not in self._clients:
            try:
                self._clients[name] = AsyncAppStoreServerAPIClient(
                    signing_key=self._credentials.private_key,
                    key_id=self._credentials.key_id,
                    issuer_id=self._credentials.issuer_id,
                    bundle_id=self._credentials.bundle_id,
                    environment=environment,
                )
            except Exception as exc:
                # The constructor parses the PEM. A key that cannot be loaded
                # is a credential problem, and saying "could not reach Apple"
                # here would send someone to check the network.
                raise StatusCredentialsRejected(
                    f"The In-App Purchase key could not be read — {type(exc).__name__}. "
                    "It must be the unencrypted P-256 .p8 from App Store Connect, "
                    "with its BEGIN/END lines intact."
                ) from None
        return self._clients[name]

    def _verifier_for(self, name: str, environment):
        """A verifier pinned to this environment and our root certificate.

        The library holds the environment *inside* the verifier and rejects a
        payload that disagrees, which is why there is one per environment
        rather than one shared. That check is worth having: it is the same gate
        `entitlements.ALLOWED_ENVIRONMENTS` exists for, applied to a response
        instead of a request.
        """
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
        if name not in self._verifiers:
            self._verifiers[name] = SignedDataVerifier(
                root_certificates=[entitlements.APPLE_ROOT_CA_G3_PEM],
                # OCSP would put a second network dependency inside what is
                # otherwise an offline signature check against a pinned root,
                # on a path an operator is waiting on. The chain walk itself is
                # not weakened by leaving it off.
                enable_online_checks=False,
                environment=environment,
                bundle_id=self._credentials.bundle_id,
                app_apple_id=self._credentials.app_apple_id,
            )
        return self._verifiers[name]

    async def aclose(self) -> None:
        """Close every client this instance opened. Never raises."""
        for client in self._clients.values():
            try:
                await client.async_close()          # type: ignore[attr-defined]
            except Exception:                        # pragma: no cover - defensive
                log.debug("closing an App Store client failed", exc_info=True)
        self._clients.clear()

    # ── The lookup ──────────────────────────────────────────────────────────

    async def statuses(self, transaction_id: str) -> list[SubscriptionStatus]:
        """Every subscription Apple holds for the customer behind this id.

        `transaction_id` may be any transactionId, originalTransactionId or
        appTransactionId belonging to the customer — Apple resolves all three,
        which is why the caller does not have to know which one it has.

        Tries Production, then Sandbox. Raises `SubscriberNotFound` only when
        *both* have nothing.
        """
        if not transaction_id or not transaction_id.strip():
            raise SubscriberNotFound("No transaction id given.")
        transaction_id = transaction_id.strip()

        last_not_found: SubscriberNotFound | None = None
        async with self._lock:
            for name, environment in self._environments():
                try:
                    return await self._statuses_in(name, environment, transaction_id)
                except SubscriberNotFound as exc:
                    # Not an error yet. A Sandbox subscription is genuinely
                    # absent from Production, and a TestFlight tester asking
                    # why their purchase did not register is exactly the
                    # support case this command is for.
                    log.info("no subscription in %s, falling back", name)
                    last_not_found = exc
                    continue

        raise last_not_found or SubscriberNotFound(
            f"Apple has no subscription under {transaction_id} in Production "
            "or Sandbox.")

    async def _statuses_in(self, name: str, environment,
                           transaction_id: str) -> list[SubscriptionStatus]:
        from appstoreserverlibrary.api_client import APIException

        client = self._client_for(name, environment)
        try:
            response = await asyncio.wait_for(
                client.get_all_subscription_statuses(transaction_id),  # type: ignore[attr-defined]
                timeout=TIMEOUT_SECONDS)
        except TimeoutError:
            raise StatusUnavailable(
                f"Apple did not answer within {TIMEOUT_SECONDS:.0f}s ({name}).") from None
        except APIException as exc:
            raise self._translate(exc, name) from None
        except Exception as exc:
            # DNS, TLS, connection reset. Distinct from an APIException, which
            # means Apple answered and said no.
            raise StatusUnavailable(
                f"Could not reach Apple ({type(exc).__name__}).") from None

        statuses: list[SubscriptionStatus] = []
        for group in (response.data or []):
            for item in (group.lastTransactions or []):
                decoded = self._decode(name, environment, item)
                if decoded is not None:
                    statuses.append(decoded)

        if not statuses:
            # Apple answered 200 with nothing in it. For our purposes that is
            # the same as a 404 — and it is what a customer with only consumable
            # purchases looks like.
            raise SubscriberNotFound(
                f"Apple returned no subscriptions for {transaction_id} in {name}.")
        return statuses

    @staticmethod
    def _translate(exc, name: str) -> StatusError:
        """Apple's error codes, in words, without losing the code itself."""
        from appstoreserverlibrary.api_client import APIError

        code = getattr(exc, "api_error", None)
        http = getattr(exc, "http_status_code", 0)

        not_found = {
            APIError.ORIGINAL_TRANSACTION_ID_NOT_FOUND,
            APIError.ORIGINAL_TRANSACTION_ID_NOT_FOUND_RETRYABLE,
            APIError.TRANSACTION_ID_NOT_FOUND,
            APIError.ACCOUNT_NOT_FOUND,
            APIError.ACCOUNT_NOT_FOUND_RETRYABLE,
        }
        if code in not_found:
            return SubscriberNotFound(f"No such subscription in {name}.")
        if http == 429 or code == APIError.RATE_LIMIT_EXCEEDED:
            # Apple's limit is per key, and the bot shares one with any other
            # caller. Worth saying so — the fix is to wait, not to re-run.
            return StatusRateLimited(
                "Apple is rate limiting this key (429). Wait a minute and retry.")
        if http == 401:
            return StatusCredentialsRejected(
                "Apple refused the key (401). Check APPLE_IAP_KEY_ID and "
                "APPLE_IAP_ISSUER_ID match the .p8, and that it is an "
                "In-App Purchase key rather than an App Store Connect one.")
        detail = getattr(exc, "error_message", None) or ""
        return StatusUnavailable(
            f"Apple returned HTTP {http}"
            + (f" ({code.name})" if code is not None else "")
            + (f": {detail[:140]}" if detail else "."))

    def _decode(self, name: str, environment, item) -> SubscriptionStatus | None:
        """Verify and decode one `lastTransactions` entry.

        Returns None for an entry carrying no signed transaction. Apple has not
        been seen to send one, but the field is Optional in its own model, and
        a missing transaction must not take down a lookup that has other
        subscriptions to report.
        """
        from appstoreserverlibrary.signed_data_verifier import VerificationException

        verifier = self._verifier_for(name, environment)
        if not item.signedTransactionInfo:
            log.warning("subscription entry carried no signed transaction")
            return None

        try:
            transaction = verifier.verify_and_decode_signed_transaction(  # type: ignore[attr-defined]
                item.signedTransactionInfo)
        except VerificationException as exc:
            # Apple's own API served something that does not verify against
            # Apple's own root. That is not a subscriber problem and must not
            # be reported as one.
            raise StatusUnavailable(
                f"A transaction from Apple failed verification ({exc}).") from None

        renewal = None
        if item.signedRenewalInfo:
            try:
                renewal = verifier.verify_and_decode_renewal_info(  # type: ignore[attr-defined]
                    item.signedRenewalInfo)
            except VerificationException as exc:
                raise StatusUnavailable(
                    f"Renewal info from Apple failed verification ({exc}).") from None

        # Fed to the shared mapper in the raw JSON shape Apple signs, so the
        # millisecond and milliunit conversions happen in exactly one place —
        # see `entitlements.entitlement_from_payload`. The `raw*` attributes
        # are used where the library offers them: they are the values as
        # signed, before the library's enums narrow them, so a product or offer
        # type Apple adds tomorrow survives the round trip instead of becoming
        # None.
        ent = entitlements.entitlement_from_payload(
            {
                "productId": transaction.productId,
                "expiresDate": transaction.expiresDate,
                "originalTransactionId": transaction.originalTransactionId,
                "originalPurchaseDate": transaction.originalPurchaseDate,
                "offerType": transaction.rawOfferType,
                "offerDiscountType": transaction.rawOfferDiscountType,
                "price": transaction.price,
                "currency": transaction.currency,
                "revocationDate": transaction.revocationDate,
            },
            # The verifier has already rejected any transaction whose
            # environment disagrees with the one it was built for, so this is
            # the value Apple signed, not a default.
            environment=name,
        )

        raw_status = getattr(item, "rawStatus", None)
        state = STATUS_WORDS.get(raw_status, f"unknown ({raw_status})")

        auto_renew: bool | None = None
        offer_identifier = transaction.offerIdentifier
        auto_renew_product_id = None
        if renewal is not None:
            raw_auto = getattr(renewal, "rawAutoRenewStatus", None)
            # Explicitly against None, not truthiness: 0 is "off", and a
            # falsy test would report a cancelled subscription as unknown.
            auto_renew = bool(raw_auto) if raw_auto is not None else None
            offer_identifier = renewal.offerIdentifier or offer_identifier
            if renewal.autoRenewProductId != transaction.productId:
                auto_renew_product_id = renewal.autoRenewProductId

        return SubscriptionStatus(
            entitlement=ent,
            state=state,
            auto_renew=auto_renew,
            offer_identifier=offer_identifier,
            auto_renew_product_id=auto_renew_product_id,
        )


# ── Module-level client ──────────────────────────────────────────────────────
# One per process, built on first use. `notify` holds no reference of its own,
# so a deployment that never runs /sub never parses the key.

_client: AppStoreStatusClient | None = None
_client_lock: asyncio.Lock | None = None


async def get_client() -> AppStoreStatusClient:
    """The shared client. Raises `StatusNotConfigured` when there is no key."""
    global _client, _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    async with _client_lock:
        if _client is None:
            credentials = credentials_from_env()
            if credentials is None:
                raise StatusNotConfigured(
                    "No App Store Server API credentials on this deployment. "
                    "Set APPLE_IAP_ISSUER_ID, APPLE_IAP_KEY_ID, "
                    "APPLE_IAP_PRIVATE_KEY (or _PATH) and APPLE_APP_APPLE_ID.")
            _client = AppStoreStatusClient(credentials)
        return _client


async def aclose() -> None:
    """Release the shared client. Called from the app lifespan."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def lookup(transaction_id: str) -> list[SubscriptionStatus]:
    """Every subscription Apple holds for this id. The module's whole surface."""
    client = await get_client()
    return await client.statuses(transaction_id)
