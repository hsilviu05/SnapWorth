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
import logging
import os
from enum import Enum

audit_log = logging.getLogger("snapworth.audit")

# Salt keeps subject hashes from being reversible via a precomputed table of
# plausible key ids. Rotating it breaks historical correlation, which is the
# intended trade for privacy.
_SALT = os.environ.get("AUDIT_SALT", "snapworth-audit-v1").encode()


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
