#!/usr/bin/env python3
"""Load Apple one-time offer codes into a referral pool (#97).

Usage:

    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py friend codes.csv
    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py reward codes.csv
    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py --status
    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py --retire friend

With Railway, `railway run python3 backend/tools/load_referral_codes.py …`
supplies production's REDIS_URL without it ever touching a file.

Two pools, two App Store Connect offers, both 7 days free on the yearly
product with one-time-use codes:

* `friend`: handed to a friend who enters an invite code. Its offer's
  *reference name* must equal REFERRAL_FRIEND_OFFER, because that name is
  all Apple reports when a code is redeemed. Set eligibility to new
  subscribers, so one Apple ID cannot take the week twice.
* `reward`: parked for the referrer once their friend redeems.

The file is App Store Connect's code download: one code per line. Anything
that is not a code (a header, blank lines) is skipped. Loading appends, and a
code already loaded into either pool is skipped, so re-running the same file
against the same Redis is harmless. Codes are never printed.

**A batch is burned by any loss of Redis's data** — a fresh instance, and a
restore from a snapshot just the same. The ledger of loaded codes, the pools,
and the cursor that records which codes were handed out all live in that one
Redis, so once any of it is lost or rewound nothing can say which codes of a
batch are still unissued. Re-running a CSV after a loss re-issues codes that
friends were already given: Apple refuses them, and a device can claim only
once, so those friends never get their week. After a restore, `--retire` each
pool, which marks everything loaded so far as handed out; after a fresh
instance there is nothing to retire. Either way, generate a new batch in App
Store Connect and load only that. Earned reward codes not yet redeemed are in
Redis alone and are gone with it (RUNBOOK §9).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

import redis

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from referral import POOLS, pool_cursor_key, pool_item_key, pool_size_key  # noqa: E402

CODE = re.compile(r"^[A-Z0-9]{6,32}$")


def read_codes(path: str) -> list[str]:
    codes = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            token = line.strip().split(",")[0].strip().upper()
            if CODE.match(token):
                codes.append(token)
    return codes


def _count(value: Any) -> int:
    """A counter read back from Redis. redis-py types a sync `get` as its
    generic response type, which pyright will not pass to `int()`."""
    return int(value or 0)


def status(r: redis.Redis) -> None:
    for pool in POOLS:
        size = _count(r.get(pool_size_key(pool)))
        used = min(size, _count(r.get(pool_cursor_key(pool))))
        print(f"{pool:7s} slots {size:6d}  handed out {used:6d}  remaining {size - used:6d}")


def retire(r: redis.Redis, pool: str) -> int:
    """Mark every code loaded into `pool` so far as handed out. Returns how
    many unissued codes that burned.

    For after a restore: the cursor may have been rewound past codes that were
    already given away, and nothing left can say which. A new batch loaded
    afterwards is appended after this point and handed out normally.
    """
    size = _count(r.get(pool_size_key(pool)))
    used = _count(r.get(pool_cursor_key(pool)))
    r.set(pool_cursor_key(pool), max(size, used))
    return max(0, size - used)


def load(r: redis.Redis, pool: str, codes: list[str]) -> int:
    added = 0
    # After the cursor, not after `size`, when the cursor is further on. Two
    # requests racing for a pool's last code carry it past `size` (and every
    # refused claim did, before `take_code` read the cursor first), and a
    # batch appended at `size + 1` then had its first codes skipped. The
    # slots in between stay empty and are never read.
    size = max(_count(r.get(pool_size_key(pool))), _count(r.get(pool_cursor_key(pool))))
    for code in codes:
        # One ledger across both pools: a code must never be both a friend's
        # week and a referrer's reward.
        if not r.set(f"refpool:seen:{code}", pool, nx=True):
            continue
        size += 1
        r.set(pool_item_key(pool, size), code)
        added += 1
    # Size last: `take_code` only hands out indices at or below it, so a
    # half-finished load never exposes an empty slot.
    r.set(pool_size_key(pool), size)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pool", nargs="?", choices=POOLS)
    parser.add_argument("file", nargs="?")
    parser.add_argument("--status", action="store_true", help="show pool sizes and exit")
    parser.add_argument("--retire", choices=POOLS,
                        help="mark every code in this pool as handed out (after a Redis restore)")
    args = parser.parse_args()

    url = os.environ.get("REDIS_URL")
    if not url:
        print("REDIS_URL is not set.", file=sys.stderr)
        return 2
    r = redis.Redis.from_url(url, decode_responses=True)

    if args.status:
        status(r)
        return 0
    if args.retire:
        burned = retire(r, args.retire)
        print(f"{args.retire}: retired, {burned} unissued codes burned. Load a new batch.")
        status(r)
        return 0
    if not args.pool or not args.file:
        parser.error("give a pool (friend|reward) and a file, or --status")
    codes = read_codes(args.file)
    if not codes:
        print(f"No codes found in {args.file}.", file=sys.stderr)
        return 1
    added = load(r, args.pool, codes)
    print(f"{args.pool}: {added} of {len(codes)} codes added ({len(codes) - added} already loaded).")
    status(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
