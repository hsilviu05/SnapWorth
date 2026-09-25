#!/usr/bin/env python3
"""Load Apple one-time offer codes into a referral pool (#97).

Usage:

    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py friend codes.csv
    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py reward codes.csv
    REDIS_URL=redis://… python3 backend/tools/load_referral_codes.py --status

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
is harmless. Codes are never printed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

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


def status(r: redis.Redis) -> None:
    for pool in POOLS:
        size = int(r.get(pool_size_key(pool)) or 0)
        used = min(size, int(r.get(pool_cursor_key(pool)) or 0))
        print(f"{pool:7s} loaded {size:6d}  handed out {used:6d}  remaining {size - used:6d}")


def load(r: redis.Redis, pool: str, codes: list[str]) -> int:
    added = 0
    size = int(r.get(pool_size_key(pool)) or 0)
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
    args = parser.parse_args()

    url = os.environ.get("REDIS_URL")
    if not url:
        print("REDIS_URL is not set.", file=sys.stderr)
        return 2
    r = redis.Redis.from_url(url, decode_responses=True)

    if args.status:
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
