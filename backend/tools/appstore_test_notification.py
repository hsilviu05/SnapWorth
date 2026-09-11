#!/usr/bin/env python3
"""Ask Apple to deliver a test App Store Server Notification, and report back.

This is the only end-to-end proof that the notification pipe works. Everything
else — the endpoint answering 400 to an unsigned body, the URL saved in App
Store Connect — shows that each half is plausible. This shows Apple reaching
*us*.

Usage:

    python3 backend/tools/appstore_test_notification.py \\
        --key-id ABC123DEFG \\
        --issuer-id 12345678-1234-1234-1234-123456789012 \\
        --key ~/Downloads/SubscriptionKey_ABC123DEFG.p8

Add --sandbox to target the sandbox endpoint instead of production.

The key is read from disk and used to sign a short-lived JWT. It is never
printed, logged, or sent anywhere but Apple.

Needs an *In-App Purchase* key: App Store Connect → Users and Access →
Integrations → In-App Purchase. The Issuer ID is shown at the top of that same
page. An App Store Connect API key from the other tab will authenticate and
then fail with 401 on these endpoints — they are different key families.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx
import jwt

PRODUCTION = "https://api.storekit.itunes.apple.com"
SANDBOX = "https://api.storekit-sandbox.itunes.apple.com"

# Apple rejects anything longer than 60 minutes. Short is fine: the token is
# used twice, seconds apart.
TOKEN_LIFETIME_SECONDS = 20 * 60

# What Apple reports back per delivery attempt, and what each one means for us.
RESULT_HELP = {
    "SUCCESS": "Apple reached the endpoint and got a 2xx.",
    "UNSUCCESSFUL_HTTP_RESPONSE_CODE":
        "Reached, but answered non-2xx. Check the server logs for "
        "'rejected App Store notification' or a Version 1 warning.",
    "NO_RESPONSE": "Nothing answered. Is the URL right, and the service up?",
    "TIMED_OUT": "Too slow to answer.",
    "TLS_ISSUE": "TLS handshake failed — certificate or protocol problem.",
    "SOCKET_ISSUE": "Connection could not be established.",
    "CIRCULAR_REDIRECT": "The URL redirects in a loop.",
    "PREMATURE_CLOSE": "The connection closed before the response finished.",
    "INVALID_RESPONSE": "The response could not be understood.",
    "UNSUPPORTED_CHARSET": "The response charset is not supported.",
    "OTHER": "Apple did not classify the failure.",
}


def build_token(key_pem: str, key_id: str, issuer_id: str, bundle_id: str) -> str:
    """Sign the ES256 JWT the App Store Server API expects.

    `bid` is what separates this from an App Store Connect API token. Omitting
    it authenticates and then fails 401 on these endpoints, which reads as a
    bad key rather than a missing claim.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "iss": issuer_id,
            "iat": now,
            "exp": now + TOKEN_LIFETIME_SECONDS,
            "aud": "appstoreconnect-v1",
            "bid": bundle_id,
        },
        key_pem,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key-id", required=True, help="the In-App Purchase key ID")
    ap.add_argument("--issuer-id", required=True, help="shown above the key list")
    ap.add_argument("--key", required=True, help="path to the .p8 file")
    ap.add_argument("--bundle-id", default="eu.snapworth.app")
    ap.add_argument("--sandbox", action="store_true",
                    help="target the sandbox endpoint")
    ap.add_argument("--attempts", type=int, default=10,
                    help="how many times to poll for the delivery result")
    args = ap.parse_args()

    try:
        with open(args.key, "r") as fh:
            key_pem = fh.read()
    except OSError as exc:
        print(f"could not read the key: {exc}", file=sys.stderr)
        return 2

    base = SANDBOX if args.sandbox else PRODUCTION
    token = build_token(key_pem, args.key_id, args.issuer_id, args.bundle_id)
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=base, timeout=30) as client:
        r = client.post("/inApps/v1/notifications/test", headers=headers)
        if r.status_code == 401:
            print("401 from Apple. Usually one of: an App Store Connect key "
                  "rather than an In-App Purchase key, the wrong Issuer ID, or "
                  "a key ID that does not match the .p8.", file=sys.stderr)
            return 1
        if r.status_code != 200:
            print(f"{r.status_code} asking for a test notification: {r.text}",
                  file=sys.stderr)
            return 1

        test_token = r.json().get("testNotificationToken")
        if not test_token:
            print(f"no testNotificationToken in {r.text}", file=sys.stderr)
            return 1
        print(f"requested — Apple is delivering to your production URL"
              f"{' (sandbox)' if args.sandbox else ''}")

        # The delivery attempt is not recorded instantly.
        for attempt in range(args.attempts):
            time.sleep(2 if attempt else 3)
            check = client.get(f"/inApps/v1/notifications/test/{test_token}",
                               headers=headers)
            if check.status_code == 404:
                continue            # not recorded yet
            if check.status_code != 200:
                print(f"{check.status_code} checking the result: {check.text}",
                      file=sys.stderr)
                return 1
            attempts = check.json().get("sendAttempts") or []
            if not attempts:
                continue
            last = attempts[-1]
            result = last.get("sendAttemptResult", "?")
            print(f"\nApple reports: {result}")
            print(f"  {RESULT_HELP.get(result, 'Unrecognised result.')}")
            if result == "SUCCESS":
                print("\nCheck Telegram — the bot should have said App Store "
                      "Server Notifications are connected.")
                return 0
            return 1

    print("\nApple did not record a delivery attempt in time. Re-run to check "
          "again; the notification may still land.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
