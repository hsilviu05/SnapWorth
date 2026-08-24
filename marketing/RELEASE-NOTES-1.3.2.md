# What's New — SnapWorth 1.3.2

**Version:** 1.3.2 (build 9) · **Previous release:** 1.3.1 (build 8) · **Status:** unreleased

> **The number depends on 1.3.1's review outcome.** If 1.3.1 is approved, this
> is 1.3.2 / build 9. If it is *rejected*, that version string is still open —
> resubmit as **1.3.1 / build 9** instead and reuse this copy. Do not assume;
> check App Store Connect. (1.3.0 was mistaken for unshipped once already this
> cycle, which cost an upload.)

## Scope

Exactly one commit since 1.3.1: `9df1b7d`. One user-visible behaviour change,
one copy change, one invisible correctness fix.

| Change | Visible? |
|---|---|
| A rejected session now reconnects by itself instead of erroring | **yes** |
| Wording when reconnection genuinely fails | **yes** |
| `AppError.sessionExpired` did not equal itself | no |

Nothing else moved. The valuation and quota fixes people associate with this
week were server-side and have been live since before 1.3.1.

---

## Primary — paste into App Store Connect

```
Fewer interruptions.

When your session needed reconnecting, SnapWorth used to stop and ask you
to try again — and trying again usually hit the same wall. It now
reconnects on its own and gets on with your scan.
```

**Character count:** well within the 4,000 limit.

### Alternative, if you prefer the shortest possible

```
SnapWorth now reconnects on its own when a session expires, instead of
asking you to retry something that wouldn't have worked.
```

---

## Copy constraints this text respects

**"usually hit the same wall" is literally accurate**, not self-deprecation for
effect. `accessToken()` returned the cached token whenever it was more than a
minute from expiry, so a retry re-sent the *same rejected credential*. The old
alert — "pull to retry, it should reconnect automatically" — described a
recovery the code did not perform.

**No claim that sessions expire less often.** They do not. The change is what
happens next.

**Nothing about valuations, credits or the backend outage.** Those were fixed
server-side days earlier; claiming them here would be false for every reader.

---

## What went wrong, for the record

A 401 means the token the client attached is not acceptable. Nothing cleared it
on rejection, so `accessToken()` kept returning it until it expired — up to an
hour of identical failures. Only a 401 from `/refresh` triggered
re-attestation; a 401 from `/scan` or `/listing` did not.

This surfaced when the backend was found to be running without `TOKEN_KEYS`:
every deploy generated a fresh signing key and invalidated every outstanding
token. The server side is fixed (`TOKEN_KEYS` set, `ENVIRONMENT=production` so
the ephemeral fallback now refuses to boot rather than rotating keys silently).
This release fixes the client half, so the same class of rejection recovers on
its own rather than depending on the server never rotating.

Worth recording because it shaped the fix: **deleting and reinstalling the app
does not clear the credential.** The token lives in the Keychain, which
survives app deletion, and no UI path calls `AttestationService.reset()`. On
1.3.1 and earlier the only remedy is waiting out the hour.

## Before submitting

Nothing outstanding. No new data collection, so the App Privacy labels settled
for 1.3.0 are unchanged.
