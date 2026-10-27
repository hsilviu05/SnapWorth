"""How the operator's bot words dates, plans, money and device ids.

Shared by notify (/subs, /users, the digest) and opssupport (/sub, /user),
split out with them (#230) so neither imports the other for a date format.
Pure functions, no state.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Inline-keyboard rows: (label, callback data). The data is fed straight back
# through `handle_command` as "/<data>", so buttons and commands share one path.
Buttons = list[list[tuple[str, str]]]


def date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d %b %Y")


def short_date(epoch: int) -> str:
    """`12Sep26` — seven characters, and it keeps the year.

    The tables used `date(...)[:6]`. `%d` is zero-padded and `%b` is three
    letters, so that slice is always exactly `DD Mon` and always discards the
    year — there is no input for which it keeps any part of it. `renews`
    survived by luck, because a live subscription renews within twelve months,
    but `since` is `original_purchase_at` with no lower bound and `seen` is
    bounded only by the 400-day index TTL. Both could be more than a year old
    and printed identically to today, which is how a 2027 renewal read as
    "23 Jul".
    """
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d%b%y")


def renewal_phrase(expires_at: int, auto_renew: bool | None) -> str:
    """The expiry line: `renews or expires 12 Mar 2027`, or just `expires`.

    The hedge is not sloppiness — for most of this bot's life it was the honest
    answer. A signed transaction carries `expiresDate` and nothing about
    whether the period after it is coming, so the date genuinely meant one of
    two things and the line said so.

    Now that `signedRenewalInfo` is read (`appstorenotify._auto_renew_status`),
    the hedge is only correct where the fact is still unknown. Kept for exactly
    that case: rows written before this existed, and every `/auth/entitlement`
    sync, which sees a transaction and no renewal info. Only a definite False
    narrows the wording — an unknown auto-renew must not be reported as a
    cancellation, which would turn "we did not ask" into "they are leaving".
    """
    if auto_renew is False:
        return f"expires {date(expires_at)}"
    return f"renews or expires {date(expires_at)}"


def sub_is_alive(entry: dict, now: float) -> bool:
    """Whether a subscription row is currently entitled.

    One function, because there were three copies and one of them had drifted:
    `/user` tested expiry alone, so a refunded subscription — which keeps its
    expiry date, the period having been paid for and then unpaid — read as
    "renews 12 Mar 2027" there while `/subs` showed the same row as `refund`
    and `/subs`'s summary excluded it from active revenue.
    """
    if entry.get("revoked") is not None:
        return False
    expires = entry.get("expires")
    return expires is None or float(expires) > now


def device_argument(argument: str | None) -> str:
    """What the operator typed, as an id: trimmed, lower-cased, and without
    the "Device" the in-app support form writes in front of it."""
    parts = (argument or "").strip().lower().split()
    if len(parts) == 2 and parts[0] == "device":
        parts = parts[1:]
    return " ".join(parts)


def plan(product: str | None) -> str:
    return (product or "?").replace("com.snapworth.", "")


def money(amount: float, currency: str | None) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency or "")
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency or ''}".strip()


def usd(amount: float) -> str:
    return f"${amount:,.2f}"


def approx_usd(amounts: dict[str, float]) -> tuple[float, list[str], str | None]:
    """Mixed-currency amounts summed in US dollars at one fixed rate (#397).

    The rate is the last day of the pinned ECB table the eval harness ships
    (`eval/fx.py`), not today's: a fixed rate keeps one month comparable with
    the next, which is what the total is for, and needs no network. Returns
    the total, the currencies the table has no rate for (left out of it), and
    the rate day, or None when the table cannot be read.
    """
    try:
        from eval import fx
        table = fx.load()
        on = max(table.rates)
    except Exception:
        return 0.0, sorted(c for c in amounts if c != "USD"), None
    total, missing = 0.0, []
    for currency, amount in amounts.items():
        try:
            total += table.to_usd(amount, currency, on)
        except fx.FxUnavailable:
            missing.append(currency)
    return total, sorted(missing), on.isoformat()


def money_total(amounts: dict[str, float]) -> str:
    """Per-currency amounts, then their approximate dollar total when there
    is anything to convert: "$9.99 + €29.99 (≈ $43.46)"."""
    if not amounts:
        return "0"
    text = " + ".join(money(v, c) for c, v in sorted(amounts.items()))
    if set(amounts) <= {"USD"}:
        return text
    total, missing, on = approx_usd(amounts)
    if on is None:
        return text
    if len(missing) == len(amounts):
        return text
    left_out = f" excl. {', '.join(missing)}" if missing else ""
    return f"{text} (≈ {usd(total)}{left_out})"


def fx_note() -> str:
    """The footnote a `money_total` reader needs once."""
    _, _, on = approx_usd({"EUR": 1.0})
    return (f"≈ $ totals use ECB rates of {on}, held fixed so months compare."
            if on else "")


def usd_fine(amount: float) -> str:
    """Per-scan money: three decimals below ten cents, or the number lies."""
    return f"${amount:,.3f}" if amount < 0.10 else f"${amount:,.2f}"


def kilo(n: int) -> str:
    return f"{n / 1000:.1f}K" if n >= 1000 else str(n)
