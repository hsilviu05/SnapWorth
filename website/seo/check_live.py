#!/usr/bin/env python3
"""Does the live site do what vercel.json says?

vercel.json declares the /i/<code> invite rewrite, four security headers, a
year's caching for /fonts, clean URLs, no trailing slashes and (through
404.html) a branded not-found page. None of that is visible in the files
themselves, and on 2026-09-26 production applied none of it: /i/<code> was a
plain-text 404, no security header was sent, fonts went out with max-age=0,
and /support.html was a 404 instead of a redirect. The clean paths themselves
worked, because `vercel build` bakes those into the file layout; everything that
is a route did not, and older deployments show it never had. Two audits had
recorded the headers and the 404 page as done. Nothing had ever asked the live
site.

This asks it. The expected headers are read from vercel.json, so the check
follows the config instead of restating it.

Run: python3 website/seo/check_live.py [base-url]   (default www.snapworth.eu)
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
# website/vercel.json, not the repo root's: the Vercel project's Root Directory
# is `website`, so that is the only copy the platform reads.
CONFIG = json.loads((HERE.parent / "vercel.json").read_text(encoding="utf-8"))
BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://www.snapworth.eu").rstrip("/")

# Invented, so it can never be a real person's code; the page only displays it.
PROBE_CODE = "TEST1"
ATTEMPTS, PAUSE = 3, 20   # a new production alias can take a moment to settle


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(path: str) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(BASE + path, headers={
        "User-Agent": "snapworth-site-check (+https://github.com/hsilviu05/SnapWorth)"})
    try:
        with OPENER.open(request, timeout=20) as response:
            status, headers, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, headers, raw = error.code, error.headers, error.read()
    lowered = {k.lower(): v for k, v in headers.items()}
    return status, lowered, raw.decode("utf-8", errors="replace")


def headers_for(source: str) -> dict[str, str]:
    for rule in CONFIG.get("headers", []):
        if rule["source"] == source:
            return {h["key"].lower(): h["value"] for h in rule["headers"]}
    return {}


def run() -> list[str]:
    problems: list[str] = []

    def expect(ok: bool, message: str) -> None:
        if not ok:
            problems.append(message)

    # The invite link the app shares (referral.py `share_base` + code).
    status, headers, body = fetch(f"/i/{PROBE_CODE}")
    expect(status == 200, f"/i/{PROBE_CODE}: {status}, expected the invite page (200)")
    expect("text/html" in headers.get("content-type", ""),
           f"/i/{PROBE_CODE}: content-type {headers.get('content-type')!r}")
    expect("free week of" in body, f"/i/{PROBE_CODE}: not the invite page")
    expect("noindex" in headers.get("x-robots-tag", ""),
           f"/i/{PROBE_CODE}: no X-Robots-Tag: noindex")

    # Security headers, on every path.
    status, headers, _ = fetch("/")
    expect(status == 200, f"/: {status}")
    for key, value in headers_for("/(.*)").items():
        expect(headers.get(key) == value,
               f"/: {key} is {headers.get(key)!r}, vercel.json says {value!r}")

    # Font caching.
    fonts = headers_for("/fonts/(.*)").get("cache-control")
    if fonts:
        _, headers, _ = fetch("/fonts/fraunces-latin.woff2")
        expect(headers.get("cache-control") == fonts,
               f"/fonts: cache-control {headers.get('cache-control')!r}, "
               f"vercel.json says {fonts!r}")

    # The branded 404, not the platform's plain-text one.
    status, headers, body = fetch(f"/no-such-page-{int(time.time())}")
    expect(status == 404, f"unknown path: {status}, expected 404")
    expect("text/html" in headers.get("content-type", "") and "Page not found" in body,
           "unknown path: not the site's 404 page "
           f"(content-type {headers.get('content-type')!r})")

    # Clean URLs and no trailing slash: both redirect to the canonical path.
    if CONFIG.get("cleanUrls"):
        status, headers, _ = fetch("/support.html")
        expect(status in (301, 308) and headers.get("location", "").endswith("/support"),
               f"/support.html: {status} {headers.get('location')!r}, "
               "expected a redirect to /support")
    if CONFIG.get("trailingSlash") is False:
        status, headers, _ = fetch("/support/")
        expect(status in (301, 308) and headers.get("location", "").endswith("/support"),
               f"/support/: {status} {headers.get('location')!r}, "
               "expected a redirect to /support")

    return problems


def main() -> int:
    problems = run()
    for attempt in range(1, ATTEMPTS):
        if not problems:
            break
        print(f"attempt {attempt}: {len(problems)} problem(s), retrying in {PAUSE}s")
        time.sleep(PAUSE)
        problems = run()
    for problem in problems:
        print(problem)
    print(f"{BASE}: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
