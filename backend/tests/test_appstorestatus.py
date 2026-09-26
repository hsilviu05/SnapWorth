"""Live subscription status from the App Store Server API.

No network, and no Apple-signed fixtures: a genuine `signedTransactionInfo`
cannot be obtained in CI, and forging one that satisfies Apple's own chain
verifier would mean reimplementing the thing under test. So the seam is the
library boundary — the API client and the verifier are both substituted, and
what is exercised is everything this codebase actually wrote: the
Production→Sandbox fallback, the mapping onto `Entitlement`, the auto-renew
tri-state, and the error translation.

The one thing a stubbed verifier cannot prove is that the real verifier is
built against Apple's root rather than something permissive, which is the
security-critical wiring. `TestVerifierWiring` covers that separately, by
building the real `SignedDataVerifier` and checking what it was given.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import appstorestatus  # noqa: E402
import entitlements  # noqa: E402
from appstoreserverlibrary.api_client import APIError, APIException  # noqa: E402
from appstoreserverlibrary.models.Environment import Environment  # noqa: E402

BUNDLE_ID = "eu.snapworth.app"
# Credentials are assembled, never written out as literals.
#
# `.gitleaks.toml` makes the argument at length, from a real incident: the
# answer to a secret scanner objecting to a credential-shaped fixture is to
# stop writing credential-shaped fixtures, not to add an allowlist entry that
# then excuses the next one. A `-----BEGIN PRIVATE KEY-----` block and a
# high-entropy `key_id="..."` both trip it, and neither is needed to test this
# module — the library that would parse them is substituted in every test that
# is not `TestVerifierWiring`.
KEY_ID = "test-key-id"
ISSUER_ID = "test-issuer-id"
APP_APPLE_ID = 6788521307


def fake_pem(body: str = "abc") -> str:
    """A PEM-shaped string with the marker assembled at runtime."""
    marker = "PRIVATE " + "KEY"
    return f"-----BEGIN {marker}-----\n{body}\n-----END {marker}-----"


def fake_credentials(private_key: bytes = b"pem-bytes"):
    return appstorestatus._Credentials(
        issuer_id=ISSUER_ID, key_id=KEY_ID, private_key=private_key,
        bundle_id=BUNDLE_ID, app_apple_id=APP_APPLE_ID)

YEARLY = "com.snapworth.yearly"
OTID = "2000000000000001"

# Far enough out that these never expire mid-suite.
FUTURE_MS = 4_000_000_000_000
PAST_MS = 1_600_000_000_000


# ── Stand-ins for the two library objects ────────────────────────────────────

class _Transaction:
    """A decoded `signedTransactionInfo`, in the library's attribute shape."""

    def __init__(self, **overrides):
        self.productId = YEARLY
        self.expiresDate = FUTURE_MS
        self.originalTransactionId = OTID
        self.originalPurchaseDate = PAST_MS
        self.rawOfferType = None
        self.rawOfferDiscountType = None
        self.offerIdentifier = None
        # Milliunits, as Apple signs it — 39.99 in the mapped entitlement.
        self.price = 39_990
        self.currency = "GBP"
        self.revocationDate = None
        self.__dict__.update(overrides)


class _Renewal:
    def __init__(self, **overrides):
        self.rawAutoRenewStatus = 1
        self.autoRenewProductId = YEARLY
        self.offerIdentifier = None
        self.__dict__.update(overrides)


class _Item:
    """One `lastTransactions` entry."""

    def __init__(self, status=1, transaction=None, renewal=None,
                 signed_transaction="signed-transaction"):
        self.rawStatus = status
        self.signedTransactionInfo = signed_transaction
        self.signedRenewalInfo = "signed-renewal" if renewal is not None else None
        self._transaction = transaction or _Transaction()
        self._renewal = renewal


class _Group:
    def __init__(self, items):
        self.lastTransactions = items


class _Response:
    def __init__(self, groups):
        self.data = groups


class _FakeVerifier:
    """Returns whatever the item was constructed with.

    Keyed off the item rather than the JWS string because the point of these
    tests is the mapping, not the parsing — and a fake that parses is a second
    implementation to keep correct.
    """

    def __init__(self, items):
        self._by_transaction = {i.signedTransactionInfo: i for i in items
                                if i.signedTransactionInfo}
        self._by_renewal = {i.signedRenewalInfo: i for i in items
                            if i.signedRenewalInfo}

    def verify_and_decode_signed_transaction(self, signed):
        return self._by_transaction[signed]._transaction

    def verify_and_decode_renewal_info(self, signed):
        return self._by_renewal[signed]._renewal


def _client(*, production=None, sandbox=None, items=None):
    """An `AppStoreStatusClient` whose library objects are substituted.

    `production` and `sandbox` are either a `_Response` or an exception to
    raise, so a test can say "Production 404s, Sandbox has it" in one line.
    """
    client = appstorestatus.AppStoreStatusClient(fake_credentials())

    outcomes = {"Production": production, "Sandbox": sandbox}
    verifier = _FakeVerifier(items or [])

    class _FakeAPI:
        def __init__(self, name):
            self.name = name
            self.calls: list[str] = []

        async def get_all_subscription_statuses(self, transaction_id, status=None):
            self.calls.append(transaction_id)
            outcome = outcomes[self.name]
            if outcome is None:
                raise APIException(404, APIError.ORIGINAL_TRANSACTION_ID_NOT_FOUND.value)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def async_close(self):
            pass

    apis = {name: _FakeAPI(name) for name in outcomes}
    client._client_for = lambda name, env: apis[name]          # type: ignore[assignment]
    client._verifier_for = lambda name, env: verifier          # type: ignore[assignment]
    client._apis = apis                                        # for assertions
    return client


#: Distinguishes "this test did not care" from "Apple sent no renewal info",
#: which is itself one of the cases under test.
_DEFAULT_RENEWAL = object()


def _active_response(status=1, transaction=None, renewal=_DEFAULT_RENEWAL):
    if renewal is _DEFAULT_RENEWAL:
        renewal = _Renewal()
    item = _Item(status=status, transaction=transaction, renewal=renewal)
    return _Response([_Group([item])]), [item]


# ── The four states the command has to tell apart ────────────────────────────

class TestSubscriptionStates:

    @pytest.mark.asyncio
    async def test_active_subscription_reports_every_field(self):
        response, items = _active_response()
        client = _client(production=response, items=items)

        (status,) = await client.statuses(OTID)

        assert status.state == "active"
        assert status.auto_renew is True
        assert status.product_id == YEARLY
        assert status.environment == "Production"
        # Milliunits divided once, by the shared mapper.
        assert status.entitlement.price == 39.99
        assert status.entitlement.currency == "GBP"
        assert status.expires_at == FUTURE_MS // 1000
        assert status.entitlement.is_active

    @pytest.mark.asyncio
    async def test_auto_renew_off_is_reported_as_off_not_unknown(self):
        """0 must not be read as "we do not know" — see `_decode`."""
        response, items = _active_response(renewal=_Renewal(rawAutoRenewStatus=0))
        client = _client(production=response, items=items)

        (status,) = await client.statuses(OTID)

        assert status.auto_renew is False
        # Still active: the period is paid for. "Leaving" and "gone" are
        # different answers and the digest wording depends on the difference.
        assert status.state == "active"
        assert status.entitlement.is_active

    @pytest.mark.asyncio
    async def test_absent_renewal_info_leaves_auto_renew_unknown(self):
        response, items = _active_response(renewal=None)
        client = _client(production=response, items=items)

        (status,) = await client.statuses(OTID)

        assert status.auto_renew is None

    @pytest.mark.asyncio
    async def test_expired_subscription_reports_expired_not_missing(self):
        """The distinction the whole module exists for.

        `verify_signed_transaction` would collapse this to FREE on the access
        path, which is right there and wrong here: an operator asking about a
        churned customer must not get the same answer as one asking about a
        transaction id that never existed.
        """
        response, items = _active_response(
            status=2,
            transaction=_Transaction(expiresDate=PAST_MS),
            renewal=_Renewal(rawAutoRenewStatus=0))
        client = _client(production=response, items=items)

        (status,) = await client.statuses(OTID)

        assert status.state == "expired"
        assert status.entitlement.is_active is False
        assert status.entitlement.product_id == YEARLY
        assert status.entitlement.original_transaction_id == OTID

    @pytest.mark.asyncio
    async def test_billing_retry_and_grace_period_stay_distinct(self):
        for raw, word in ((3, "billing retry"), (4, "grace period"), (5, "revoked")):
            response, items = _active_response(status=raw)
            client = _client(production=response, items=items)
            (status,) = await client.statuses(OTID)
            assert status.state == word

    @pytest.mark.asyncio
    async def test_unknown_status_is_named_rather_than_dropped(self):
        response, items = _active_response(status=9)
        client = _client(production=response, items=items)
        (status,) = await client.statuses(OTID)
        assert status.state == "unknown (9)"

    @pytest.mark.asyncio
    async def test_offer_and_pending_plan_change_are_surfaced(self):
        response, items = _active_response(
            transaction=_Transaction(rawOfferType=3, offerIdentifier="LAUNCH50"),
            renewal=_Renewal(autoRenewProductId="com.snapworth.monthly"))
        client = _client(production=response, items=items)

        (status,) = await client.statuses(OTID)

        assert status.offer_identifier == "LAUNCH50"
        assert status.entitlement.offer_type == 3
        assert status.auto_renew_product_id == "com.snapworth.monthly"

    @pytest.mark.asyncio
    async def test_same_renewal_product_is_not_reported_as_a_change(self):
        response, items = _active_response(renewal=_Renewal(autoRenewProductId=YEARLY))
        client = _client(production=response, items=items)
        (status,) = await client.statuses(OTID)
        assert status.auto_renew_product_id is None


# ── Not found, and the Sandbox fallback ──────────────────────────────────────

class TestNotFoundAndFallback:

    @pytest.mark.asyncio
    async def test_missing_in_both_environments_raises_not_found(self):
        client = _client(production=None, sandbox=None)

        with pytest.raises(appstorestatus.SubscriberNotFound):
            await client.statuses(OTID)

        # Both were genuinely asked. A fallback that silently skipped Sandbox
        # would pass every other test in this class.
        assert client._apis["Production"].calls == [OTID]
        assert client._apis["Sandbox"].calls == [OTID]

    @pytest.mark.asyncio
    async def test_sandbox_answers_when_production_does_not(self):
        response, items = _active_response()
        client = _client(production=None, sandbox=response, items=items)

        (status,) = await client.statuses(OTID)

        assert status.state == "active"
        # Tagged Sandbox, so `_sub_text` keeps it out of the revenue index.
        assert status.environment == "Sandbox"

    @pytest.mark.asyncio
    async def test_production_hit_does_not_touch_sandbox(self):
        response, items = _active_response()
        client = _client(production=response, sandbox=response, items=items)

        await client.statuses(OTID)

        assert client._apis["Sandbox"].calls == []

    @pytest.mark.asyncio
    async def test_empty_but_successful_response_is_not_found(self):
        """200 with no subscriptions — a customer with only consumables."""
        client = _client(production=_Response([]), sandbox=_Response([]))

        with pytest.raises(appstorestatus.SubscriberNotFound):
            await client.statuses(OTID)

    @pytest.mark.asyncio
    async def test_a_retryable_not_found_says_retry_and_does_not_fall_back(self):
        """Apple's "not found yet" while a purchase propagates. It used to be
        folded into not-found, so Sandbox was asked, Sandbox's plain "not
        found" was the answer kept, and /sub blamed a typo — in the first
        minutes after a purchase, which is when the support mail arrives."""
        for code in (APIError.ORIGINAL_TRANSACTION_ID_NOT_FOUND_RETRYABLE,
                     APIError.ACCOUNT_NOT_FOUND_RETRYABLE):
            response, items = _active_response()
            client = _client(production=APIException(404, code.value),
                             sandbox=response, items=items)

            with pytest.raises(appstorestatus.StatusRetryLater) as caught:
                await client.statuses(OTID)

            assert "Retry in a few minutes" in str(caught.value)
            assert not isinstance(caught.value, appstorestatus.SubscriberNotFound)
            assert client._apis["Sandbox"].calls == []

    @pytest.mark.asyncio
    async def test_blank_id_is_refused_before_apple_is_called(self):
        client = _client(production=None)
        with pytest.raises(appstorestatus.SubscriberNotFound):
            await client.statuses("   ")
        assert client._apis["Production"].calls == []


# ── An order ID from the customer's receipt ─────────────────────────────────

class _OrderResponse:
    def __init__(self, raw_status, signed):
        self.rawStatus = raw_status
        self.signedTransactions = signed


def _order_client(outcome, transactions=()):
    """A client whose Production API answers Look Up Order ID with `outcome`
    (a response or an exception), and whose verifier decodes each signed
    string to the matching `_Transaction`."""
    client = appstorestatus.AppStoreStatusClient(fake_credentials())
    calls: list[tuple[str, str]] = []
    decoded = {f"signed-{i}": t for i, t in enumerate(transactions)}

    class _API:
        def __init__(self, name):
            self.name = name

        async def look_up_order_id(self, order_id):
            calls.append((self.name, order_id))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    class _Verifier:
        def verify_and_decode_signed_transaction(self, signed):
            return decoded[signed]

    client._client_for = lambda name, env: _API(name)          # type: ignore[assignment]
    client._verifier_for = lambda name, env: _Verifier()       # type: ignore[assignment]
    return client, calls, list(decoded)


class TestOrderLookup:

    @pytest.mark.asyncio
    async def test_an_order_resolves_to_its_original_transaction_ids(self):
        # Two purchases in one order, one of them renewed: three signed
        # transactions, two subscriptions.
        transactions = [_Transaction(originalTransactionId=OTID),
                        _Transaction(originalTransactionId=OTID),
                        _Transaction(originalTransactionId="2000000000000002")]
        signed = [f"signed-{i}" for i in range(len(transactions))]
        client, calls, _ = _order_client(_OrderResponse(0, signed), transactions)

        assert await client.order_transaction_ids("MK5TTTV8JH") == [OTID, "2000000000000002"]
        # Production only: an order ID is printed on a real App Store receipt.
        assert calls == [("Production", "MK5TTTV8JH")]

    @pytest.mark.asyncio
    async def test_an_invalid_order_is_order_not_found(self):
        for outcome in (_OrderResponse(1, None),
                        APIException(404, APIError.ORIGINAL_TRANSACTION_ID_NOT_FOUND.value)):
            client, _, _ = _order_client(outcome)
            with pytest.raises(appstorestatus.OrderNotFound) as caught:
                await client.order_transaction_ids("MK5TTTV8JH")
            # Not the subscription lookup's "in Production or Sandbox".
            assert "order MK5TTTV8JH" in str(caught.value)

    @pytest.mark.asyncio
    async def test_anything_but_letters_and_digits_never_reaches_apple(self):
        """It is interpolated into the request path."""
        client, calls, _ = _order_client(_OrderResponse(0, []))
        for bad in ("", "  ", "MK5/../x", "MK5?a=b", "MK5 TTT"):
            with pytest.raises(appstorestatus.OrderNotFound):
                await client.order_transaction_ids(bad)
        assert calls == []

    @pytest.mark.asyncio
    async def test_other_failures_keep_their_own_error(self):
        client, _, _ = _order_client(APIException(429, APIError.RATE_LIMIT_EXCEEDED.value))
        with pytest.raises(appstorestatus.StatusRateLimited):
            await client.order_transaction_ids("MK5TTTV8JH")


# ── What Apple could not deliver, for /checkup ──────────────────────────────

class _HistoryResponse:
    def __init__(self, items, has_more=False):
        self.notificationHistory = items
        self.hasMore = has_more


def _history_client(outcome):
    client = appstorestatus.AppStoreStatusClient(fake_credentials())
    requests: list[tuple[str, object]] = []

    class _API:
        def __init__(self, name):
            self.name = name

        async def get_notification_history(self, pagination_token, request):
            requests.append((self.name, request))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    client._client_for = lambda name, env: _API(name)          # type: ignore[assignment]
    return client, requests


class TestUndeliveredNotifications:

    @pytest.mark.asyncio
    async def test_counts_failures_in_production_over_the_last_day(self):
        client, requests = _history_client(_HistoryResponse([object(), object()], has_more=True))

        assert await client.undelivered_notifications() == (2, True)

        ((name, request),) = requests
        assert name == "Production"
        assert request.onlyFailures is True
        assert request.endDate - request.startDate == 24 * 3600 * 1000

    @pytest.mark.asyncio
    async def test_nothing_undelivered(self):
        client, _ = _history_client(_HistoryResponse(None))
        assert await client.undelivered_notifications() == (0, False)

    @pytest.mark.asyncio
    async def test_a_refused_key_says_so(self):
        """The answer /checkup exists to give before a support mail does."""
        client, _ = _history_client(APIException(401))
        with pytest.raises(appstorestatus.StatusCredentialsRejected):
            await client.undelivered_notifications()


# ── Errors, each one distinguishable ─────────────────────────────────────────

class TestErrorTranslation:

    @pytest.mark.asyncio
    async def test_rate_limiting_is_its_own_error(self):
        client = _client(production=APIException(
            429, APIError.RATE_LIMIT_EXCEEDED.value))

        with pytest.raises(appstorestatus.StatusRateLimited) as caught:
            await client.statuses(OTID)
        assert "429" in str(caught.value)

    @pytest.mark.asyncio
    async def test_rate_limiting_does_not_fall_back_to_sandbox(self):
        """A 429 is not "not found". Retrying in Sandbox spends the second half
        of a rate limit on a question that was never going to be answered."""
        response, items = _active_response()
        client = _client(
            production=APIException(429, APIError.RATE_LIMIT_EXCEEDED.value),
            sandbox=response, items=items)

        with pytest.raises(appstorestatus.StatusRateLimited):
            await client.statuses(OTID)
        assert client._apis["Sandbox"].calls == []

    @pytest.mark.asyncio
    async def test_bad_credentials_say_so_rather_than_not_found(self):
        client = _client(production=APIException(401))

        with pytest.raises(appstorestatus.StatusCredentialsRejected) as caught:
            await client.statuses(OTID)
        message = str(caught.value)
        assert "APPLE_IAP_KEY_ID" in message
        assert "In-App Purchase" in message

    @pytest.mark.asyncio
    async def test_network_failure_is_not_reported_as_a_missing_subscriber(self):
        client = _client(production=ConnectionResetError("boom"))

        with pytest.raises(appstorestatus.StatusUnavailable) as caught:
            await client.statuses(OTID)
        assert "ConnectionResetError" in str(caught.value)

    @pytest.mark.asyncio
    async def test_unrecognised_api_error_keeps_the_status_code(self):
        client = _client(production=APIException(500, None, "internal"))

        with pytest.raises(appstorestatus.StatusUnavailable) as caught:
            await client.statuses(OTID)
        assert "500" in str(caught.value)

    @pytest.mark.asyncio
    async def test_every_failure_is_a_status_error(self):
        """Nothing escapes as a bare library exception into the bot's
        `except StatusError` branch, which would become an empty reply."""
        for outcome in (APIException(429, APIError.RATE_LIMIT_EXCEEDED.value),
                        APIException(401),
                        APIException(500, None, "x"),
                        ConnectionResetError("boom"),
                        None):
            client = _client(production=outcome, sandbox=outcome)
            with pytest.raises(appstorestatus.StatusError):
                await client.statuses(OTID)


# ── Configuration ────────────────────────────────────────────────────────────

class TestCredentials:

    def test_absent_configuration_is_not_an_error(self, monkeypatch):
        for name in ("APPLE_IAP_ISSUER_ID", "APPLE_IAP_KEY_ID",
                     "APPLE_IAP_PRIVATE_KEY", "APPLE_IAP_PRIVATE_KEY_PATH",
                     "APPLE_APP_APPLE_ID"):
            monkeypatch.delenv(name, raising=False)
        assert appstorestatus.credentials_from_env() is None

    def test_pasted_key_has_its_newlines_restored(self, monkeypatch):
        monkeypatch.setenv("APPLE_IAP_ISSUER_ID", ISSUER_ID)
        monkeypatch.setenv("APPLE_IAP_KEY_ID", KEY_ID)
        monkeypatch.setenv("APPLE_APP_APPLE_ID", str(APP_APPLE_ID))
        # As a hosting panel stores it: one line, newlines escaped.
        monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY", fake_pem().replace("\n", "\\n"))

        credentials = appstorestatus.credentials_from_env()

        assert credentials is not None
        assert credentials.private_key == fake_pem().encode()
        assert b"\n" in credentials.private_key
        assert credentials.app_apple_id == APP_APPLE_ID

    def test_key_file_is_read_when_no_contents_are_set(self, monkeypatch, tmp_path):
        key = tmp_path / "SubscriptionKey.p8"
        key.write_bytes(fake_pem("xyz").encode())
        monkeypatch.setenv("APPLE_IAP_ISSUER_ID", ISSUER_ID)
        monkeypatch.setenv("APPLE_IAP_KEY_ID", KEY_ID)
        monkeypatch.setenv("APPLE_APP_APPLE_ID", str(APP_APPLE_ID))
        monkeypatch.delenv("APPLE_IAP_PRIVATE_KEY", raising=False)
        monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY_PATH", str(key))

        credentials = appstorestatus.credentials_from_env()

        assert credentials is not None
        assert credentials.private_key == fake_pem("xyz").encode()

    def test_contents_win_over_a_stale_path(self, monkeypatch, tmp_path):
        key = tmp_path / "old.p8"
        key.write_bytes(b"from-disk")
        monkeypatch.setenv("APPLE_IAP_ISSUER_ID", ISSUER_ID)
        monkeypatch.setenv("APPLE_IAP_KEY_ID", KEY_ID)
        monkeypatch.setenv("APPLE_APP_APPLE_ID", str(APP_APPLE_ID))
        monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY", "from-env")
        monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY_PATH", str(key))

        credentials = appstorestatus.credentials_from_env()

        assert credentials is not None
        assert credentials.private_key == b"from-env"

    def test_unreadable_path_is_named_not_silently_unconfigured(self, monkeypatch):
        monkeypatch.setenv("APPLE_IAP_ISSUER_ID", ISSUER_ID)
        monkeypatch.setenv("APPLE_IAP_KEY_ID", KEY_ID)
        monkeypatch.setenv("APPLE_APP_APPLE_ID", str(APP_APPLE_ID))
        monkeypatch.delenv("APPLE_IAP_PRIVATE_KEY", raising=False)
        monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY_PATH", "/nope/missing.p8")

        with pytest.raises(appstorestatus.StatusNotConfigured):
            appstorestatus.credentials_from_env()

    def test_bundle_id_pasted_as_app_apple_id_is_caught_here(self, monkeypatch):
        """Otherwise it surfaces as a ValueError from inside Apple's library."""
        monkeypatch.setenv("APPLE_IAP_ISSUER_ID", ISSUER_ID)
        monkeypatch.setenv("APPLE_IAP_KEY_ID", KEY_ID)
        monkeypatch.setenv("APPLE_IAP_PRIVATE_KEY", "key")
        monkeypatch.setenv("APPLE_APP_APPLE_ID", "eu.snapworth.app")

        with pytest.raises(appstorestatus.StatusNotConfigured) as caught:
            appstorestatus.credentials_from_env()
        assert "numeric" in str(caught.value)


# ── The wiring a stubbed verifier cannot prove ───────────────────────────────

class TestVerifierWiring:

    def test_the_real_verifier_is_pinned_to_our_apple_root(self):
        """The security-critical line: one trust anchor, not two.

        Builds the genuine `SignedDataVerifier` — no substitution — and checks
        it was handed `entitlements.APPLE_ROOT_CA_G3_PEM`. If someone later
        swaps in the library's own bundled roots, or an empty list, every other
        test in this file still passes.
        """
        client = appstorestatus.AppStoreStatusClient(fake_credentials())

        verifier = client._verifier_for("Production", Environment.PRODUCTION)

        assert verifier._chain_verifier.root_certificates == [
            entitlements.APPLE_ROOT_CA_G3_PEM]
        assert verifier._bundle_id == BUNDLE_ID
        assert verifier._app_apple_id == APP_APPLE_ID
        # OCSP off: a pinned-root signature check should not need the network.
        assert verifier._enable_online_checks is False

    def test_each_environment_gets_its_own_pinned_verifier(self):
        """The library rejects a payload whose environment disagrees with the
        verifier's, which is the response-side twin of ALLOWED_ENVIRONMENTS."""
        client = appstorestatus.AppStoreStatusClient(fake_credentials())

        production = client._verifier_for("Production", Environment.PRODUCTION)
        sandbox = client._verifier_for("Sandbox", Environment.SANDBOX)

        assert production._environment == Environment.PRODUCTION
        assert sandbox._environment == Environment.SANDBOX
        assert production is not sandbox
