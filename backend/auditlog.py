"""Security audit trail.

Distinct from access logs. Access logs answer "what traffic did we serve";
the audit trail answers "who was granted or denied what, and why" — the
questions asked during an incident.

Two rules shape it:

* **Subjects are pseudonymised.** Full App Attest key ids are never written;
  a truncated salted hash is enough to correlate events without the log
  becoming a device registry.
* **Emitted on a dedicated logger** (`snapworth.audit`) so it can be routed to
  separate retention from application logs.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from enum import Enum

audit_log = logging.getLogger("snapworth.audit")

# Salt keeps subject hashes from being reversible via a precomputed table of
# plausible key ids. Rotating it breaks historical correlation, which is the
# intended trade for privacy.
_DEFAULT_SALT = "snapworth-audit-v1"
_SALT = os.environ.get("AUDIT_SALT", _DEFAULT_SALT).encode()

# Salts that are no secret: the default above, used when AUDIT_SALT is unset,
# and the value `.env.example` suggests. Both are in this public repository,
# so with either, anyone holding a device's key id can recompute its pseudonym
# and its /trends tag, which is what the salt is there to prevent.
_PUBLIC_SALTS = frozenset({_DEFAULT_SALT.encode(), b"change-me-in-production"})


def salt_is_placeholder() -> bool:
    """Whether the salt in use is public: AUDIT_SALT unset, blank, or one of
    the two placeholders this repository publishes.

    A yes or no, and nothing more. The callers are a startup log line and the
    ops bot's Checkup, and a hash, prefix or length of a real salt would
    narrow it down, so neither is given anything to print but the verdict."""
    salt = _SALT.strip()
    return not salt or salt in _PUBLIC_SALTS


class AuditEvent(str, Enum):
    ATTEST_CHALLENGE_ISSUED = "attest.challenge_issued"
    ATTEST_SUCCEEDED = "attest.succeeded"
    ATTEST_FAILED = "attest.failed"
    TOKEN_ISSUED = "token.issued"
    TOKEN_REJECTED = "token.rejected"
    TOKEN_REPLAYED = "token.replayed"
    ENTITLEMENT_RECORDED = "entitlement.recorded"
    ENTITLEMENT_REJECTED = "entitlement.rejected"
    QUOTA_EXCEEDED = "quota.exceeded"
    QUOTA_CONSUMED = "quota.consumed"
    RATE_LIMITED = "rate.limited"
    SCAN_AUTHORISED = "scan.authorised"
    SCAN_BLOCKED = "scan.blocked"
    LISTING_AUTHORISED = "listing.authorised"
    LISTING_DENIED = "listing.denied"
    UPLOAD_REJECTED = "upload.rejected"
    INJECTION_NEUTRALISED = "injection.neutralised"


def pseudonymise(subject: str | None) -> str:
    if not subject:
        return "-"
    return hashlib.sha256(_SALT + subject.encode()).hexdigest()[:16]


def keyed_tag(purpose: str, subject: str, length: int = 8) -> str:
    """A tag for `subject` that means something only within `purpose`.

    An HMAC keyed with the salt, over the subject itself. Unlike a hash of
    the pseudonym, it cannot be recomputed from what is stored beside it: the
    cache holds full pseudonyms (the operator's /users and /subs indexes) and
    raw key ids (quota and entitlement keys), but never the salt. Without the
    salt a tag joins to nothing; with it and a device's key id, it does. That
    holds only while AUDIT_SALT is set to a real secret, which the pseudonyms
    already require."""
    purpose_and_subject = f"{purpose}:{subject}".encode()
    return hmac.new(_SALT, purpose_and_subject, hashlib.sha256).hexdigest()[:length]


def record(
    event: AuditEvent,
    subject: str | None = None,
    *,
    outcome: str = "success",
    reason: str | None = None,
    **fields,
) -> None:
    """Write one audit entry.

    Never raises: an audit failure must not take down the request path.
    """
    try:
        payload = {
            "audit": True,
            "event": event.value,
            "subject": pseudonymise(subject),
            "outcome": outcome,
        }
        if reason:
            payload["reason"] = reason
        payload.update(fields)
        level = logging.WARNING if outcome != "success" else logging.INFO
        audit_log.log(level, event.value, extra=payload)
    except Exception:  # pragma: no cover - defensive
        pass
