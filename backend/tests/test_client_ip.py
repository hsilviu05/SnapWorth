"""The per-IP rate-limit key behind Railway's edge.

Railway sends `X-Forwarded-For` in two shapes: `client` for a request it
does not route through its Fastly CDN, and `client, fastly-edge` for one it
does. Until 2026-09-27 `ratelimit.client_ip` took the rightmost hop, which on
the CDN path is the Fastly edge, so everyone routed through one Fastly POP
shared one 60/h bucket. It now walks from the right past known proxies, and
these pin that on both paths, with the forged, malformed and all-proxy cases
around them.
"""

import asyncio
import ipaddress
import logging
import re

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import auth
import main
import ratelimit
from tests.test_main import client

# The app as the container serves it: uvicorn runs with --proxy-headers and
# --forwarded-allow-ips='*', so its middleware rewrites `request.client.host`
# to the leftmost X-Forwarded-For entry before the app sees the request. A
# fake request with a socket-peer host hides that.
deployed = TestClient(ProxyHeadersMiddleware(main.app, trusted_hosts="*"))


async def _echo_key(request: Request) -> PlainTextResponse:
    return PlainTextResponse(ratelimit.client_ip(request))

_echo = TestClient(ProxyHeadersMiddleware(
    Starlette(routes=[Route("/", _echo_key)]), trusted_hosts="*"))


def deployed_key(*lines: str) -> str:
    """`client_ip` behind uvicorn's proxy-header middleware, as deployed."""
    return _echo.get("/", headers=[("x-forwarded-for", line) for line in lines]).text

UNPARSEABLE = "unparseable"

CLIENT = "198.51.100.23"
FORGED = "203.0.113.7"
FASTLY_V4 = "151.101.1.1"           # in 151.101.0.0/16
FASTLY_V6 = "2a04:4e42:200::313"    # in 2a04:4e42::/32
CGNAT = "100.64.0.2"


class Req:
    """What `client_ip` reads of a Starlette request. `xff` may be a list, for
    a header that arrived as several lines."""

    def __init__(self, xff: str | list[str] | None = None,
                 host: str | None = "10.0.0.1"):
        lines = [] if xff is None else [xff] if isinstance(xff, str) else xff
        self.headers = Headers(raw=[(b"x-forwarded-for", line.encode("latin-1"))
                                    for line in lines])
        self.client = None if host is None else type("C", (), {"host": host})()


def key(xff: str | list[str] | None = None, host: str | None = "10.0.0.1") -> str:
    return ratelimit.client_ip(Req(xff, host))


@pytest.fixture(autouse=True)
def fresh_notes(monkeypatch):
    monkeypatch.setattr(ratelimit, "_HOP_COUNTS_SEEN", set())


class TestBothRoutingPaths:
    def test_one_hop_is_the_client(self):
        """The path Railway does not route through Fastly."""
        assert key(CLIENT) == CLIENT

    def test_client_then_a_fastly_ipv4_edge_is_the_client(self):
        """The path it does. This returned the edge until 2026-09-27."""
        assert key(f"{CLIENT}, {FASTLY_V4}") == CLIENT

    def test_client_then_a_fastly_ipv6_edge_is_the_client(self):
        assert key(f"{CLIENT}, {FASTLY_V6}") == CLIENT
        assert key(f"2001:db8::23, {FASTLY_V6}") == "2001:db8::/64"
        # The same edge written as an IPv4-mapped IPv6 address.
        assert key(f"{CLIENT}, ::ffff:{FASTLY_V4}") == CLIENT


class TestIPv6Keys:
    """An IPv6 caller is keyed on its /64, not its address.

    A /64 is what one line is given, a home router's LAN or a phone on
    cellular or a VPS, and every address in it is the holder's to use: iOS
    rotates temporary addresses inside it on its own. Keyed per address, one
    line was 2^64 fresh buckets on both paths, and on the CDN path that was
    new with the walk, which had keyed such traffic on the Fastly edge."""

    NET = ipaddress.ip_network("2001:db8:1234:5678::/64")

    def addresses(self, n):
        return [str(self.NET[i * 7919 + 1]) for i in range(n)]

    def test_rotating_inside_one_64_is_one_key_on_both_paths(self):
        direct = {key(a) for a in self.addresses(500)}
        cdn = {key(f"{a}, {FASTLY_V4}") for a in self.addresses(500)}
        cdn_v6 = {key(f"{a}, {FASTLY_V6}") for a in self.addresses(500)}
        assert direct == cdn == cdn_v6 == {"2001:db8:1234:5678::/64"}

    def test_two_64s_are_two_keys(self):
        assert key("2001:db8:1234:5678::1") != key("2001:db8:1234:5679::1")

    def test_however_the_hop_is_written(self):
        assert key(f"2001:DB8:0:0::23, {FASTLY_V6}") == "2001:db8::/64"
        assert key("2001:db8:0:0:ffff:ffff:ffff:ffff") == "2001:db8::/64"

    def test_a_scope_id_does_not_rotate_the_key(self):
        keys = {key(f"2001:db8::1%{n}, {FASTLY_V4}") for n in range(20)}
        assert keys == {"2001:db8::/64"}

    def test_an_ipv4_mapped_client_keys_with_its_ipv4_address(self):
        assert key(f"::ffff:{CLIENT}") == CLIENT
        assert key(f"::ffff:c633:6417, {FASTLY_V4}") == CLIENT
        assert key(CLIENT) == CLIENT

    def test_ipv4_is_still_one_bucket_per_address(self):
        assert key("198.51.100.23") != key("198.51.100.24")

    def test_every_published_fastly_range_is_skipped(self):
        for published in ratelimit._FASTLY_EDGE_RANGES:
            network = ipaddress.ip_network(published)
            for edge in (network[0], network[-1]):
                assert key(f"{CLIENT}, {edge}") == CLIENT, published


class TestForgedHops:
    """Railway's edge dropped a client-supplied header when probed, but the
    walk must not depend on that: whatever a caller writes is on the left of
    the address Railway appends. Except where that address is one the walk
    skips (`TestAFastlySourceChoosesItsKey`)."""

    @pytest.mark.parametrize("forged", [FORGED, "10.9.9.9", "151.101.9.9", "evil"])
    def test_forged_then_client_then_fastly_is_the_client(self, forged):
        assert key(f"{forged}, {CLIENT}, {FASTLY_V4}") == CLIENT

    @pytest.mark.parametrize("forged", [FORGED, "10.9.9.9", "151.101.9.9", "evil"])
    def test_forged_then_client_on_the_non_cdn_path_is_the_client(self, forged):
        assert key(f"{forged}, {CLIENT}") == CLIENT

    def test_rotating_the_forged_hop_does_not_rotate_the_key(self):
        keys = {key(f"203.0.113.{n}, {CLIENT}, {FASTLY_V4}") for n in range(1, 50)}
        assert keys == {CLIENT}


class TestRepeatedHeaderLines:
    """A header may arrive as several lines, which mean their join in order
    (RFC 9110 §5.3), and uvicorn's own proxy-header middleware reads it that
    way. `client_ip` read only the first line, so where a proxy adds its hop
    as a line of its own, a caller's line in front of it was the whole
    header: the key was the caller's choice, a fresh bucket per request."""

    def test_the_lines_are_one_header(self):
        assert key([FORGED, f"{CLIENT}, {FASTLY_V4}"]) == CLIENT
        assert key([FORGED, CLIENT]) == CLIENT
        assert key([f"{FORGED}, {CLIENT}", FASTLY_V4]) == CLIENT

    def test_rotating_the_first_line_does_not_rotate_the_key(self):
        keys = {key([f"203.0.113.{n}", f"{CLIENT}, {FASTLY_V4}"]) for n in range(1, 50)}
        assert keys == {CLIENT}

    def test_through_the_route(self, monkeypatch):
        seen = []

        async def recording(ip):
            seen.append(ip)
        monkeypatch.setattr(auth.deps, "ip_limiter", recording)
        for n in range(1, 4):
            client.post("/auth/challenge", headers=[
                ("x-forwarded-for", f"203.0.113.{n}"),
                ("x-forwarded-for", f"{CLIENT}, {FASTLY_V4}")])
        assert seen == [CLIENT] * 3


class TestInternalHops:
    @pytest.mark.parametrize("internal", [
        CGNAT, "100.127.255.254", "10.1.2.3", "172.16.0.9", "192.168.1.1",
        "127.0.0.1", "169.254.1.1", "::1", "fd00::1", "fe80::1",
    ])
    def test_client_then_an_internal_hop_is_the_client(self, internal):
        assert key(f"{CLIENT}, {internal}") == CLIENT

    def test_client_fastly_and_an_internal_hop_is_the_client(self):
        assert key(f"{CLIENT}, {FASTLY_V4}, {CGNAT}") == CLIENT

    def test_documentation_ranges_are_not_internal(self):
        """`is_global` is false for these too, which is why the internal
        ranges are listed rather than read off it."""
        for doc in ("192.0.2.4", "198.51.100.4", "203.0.113.4"):
            assert key(f"{doc}, {CGNAT}") == doc
        assert key(f"2001:db8::4, {CGNAT}") == "2001:db8::/64"


class TestFallbacks:
    def test_every_hop_a_known_proxy_is_the_leftmost(self):
        assert key(f"{FASTLY_V4}, {CGNAT}") == FASTLY_V4
        assert key(f"{CGNAT}, {FASTLY_V4}") == CGNAT
        assert key(FASTLY_V4) == FASTLY_V4

    @pytest.mark.parametrize("xff", [
        f" , {CLIENT} ,, {FASTLY_V4}",
        f"{CLIENT}, ",
        f"{CLIENT},",
        f",{CLIENT}",
    ])
    def test_empty_entries_are_not_hops(self, xff):
        assert key(xff) == CLIENT

    def test_no_entry_at_all_is_request_client_host(self):
        """Nothing in the header to choose from, so nothing a caller chose:
        uvicorn leaves the socket peer in place for an empty header."""
        for xff in (",", " , ,", "   "):
            assert key(xff, host="192.0.2.4") == "192.0.2.4"
            assert key(xff, host=None) == "unknown"
            assert deployed_key(xff) == "testclient"

    def test_no_header_is_request_client_host(self):
        assert key(None, host="192.0.2.4") == "192.0.2.4"
        assert key(None, host=None) == "unknown"
        assert key(None, host="") == "unknown"

    def test_the_key_is_truncated_on_every_path(self):
        """It reaches a cache key and is attacker-influenced. A parsed address
        is short once keyed: IPv6 allows a scope id of any length, and the
        /64 drops it."""
        scoped = "2001:db8::1%" + "z" * 200
        assert key(f"{scoped}, {FASTLY_V4}") == "2001:db8::/64"
        assert key("fe80::1%" + "z" * 200) == "fe80::/64"
        assert key("x" * 500, host="y" * 500) == UNPARSEABLE
        assert key(None, host="x" * 500) == "x" * 64


class TestAHopThatIsNotAnAddress:
    """The walk stops at the nearest hop it cannot vouch for, and keys every
    request that stops there on one fixed value.

    It used to skip such a hop and walk on to the left, and key on
    `request.client.host` when nothing parsed. Deployed, that host is the
    leftmost entry, written by the caller (`deployed`, above), so both ways
    it reached the caller's end of the header: "evil-a, evil-b" was keyed on
    evil-a where the rightmost rule had keyed evil-b, and were Railway ever
    to append something that is not a bare address — an address with a port
    — the key would be the caller's choice. The limit fails closed instead:
    one bucket for everything that reads so, which no caller can multiply."""

    def test_behind_uvicorn_no_address_is_one_bucket(self):
        assert deployed_key("evil-a") == deployed_key("evil-b") == UNPARSEABLE
        assert deployed_key("evil-a, evil-b") == UNPARSEABLE
        assert deployed_key("r1, r2, junk") == UNPARSEABLE

    def test_rotating_left_of_a_non_address_does_not_rotate_the_key(self):
        keys = {deployed_key(f"evil-{n}, railway-token") for n in range(30)}
        assert keys == {UNPARSEABLE}
        keys = {deployed_key(f"203.0.113.{n}, {CLIENT}:5678") for n in range(1, 30)}
        assert keys == {UNPARSEABLE}

    @pytest.mark.parametrize("xff", [
        f"{CLIENT}, not-an-ip, {FASTLY_V4}",
        f"{CLIENT}, 999.1.1.1",
        f"{CLIENT}:5678, {FASTLY_V4}",
        f"junk, {CGNAT}, {FASTLY_V4}",
        "evil", "999.1.1.1, x",
    ])
    def test_the_nearest_non_proxy_hop_decides(self, xff):
        assert key(xff) == UNPARSEABLE
        assert key(xff, host=None) == UNPARSEABLE

    def test_a_non_address_left_of_the_client_is_never_reached(self):
        assert key(f"evil, {CLIENT}, {FASTLY_V4}") == CLIENT
        assert key(f"evil, {CLIENT}") == CLIENT


class TestTheNote:
    """The production evidence: counts only, once per pair, a few lines."""

    CDN_LINE = ("x-forwarded-for carried 2 hop(s), skipped 1 known proxy hop(s) "
                "(Fastly edge or internal); the per-IP key is the nearest hop "
                "that is not one")

    def lines(self, caplog, headers):
        with caplog.at_level(logging.INFO, logger="snapworth.ratelimit"):
            for xff in headers:
                ratelimit.client_ip(Req(xff))
        return [r.getMessage() for r in caplog.records
                if r.getMessage().startswith("x-forwarded-for carried")]

    def test_once_per_distinct_pair_and_never_an_address(self, caplog):
        headers = [
            CLIENT,                                     # (1, 0)
            "198.51.100.24",                            # (1, 0) again
            f"{CLIENT}, {FASTLY_V4}",                   # (2, 1)
            f"2001:db8::23, {FASTLY_V6}",               # (2, 1) again
            f"{FASTLY_V4}, {CGNAT}",                    # (2, 2), the leftmost
            f"{FORGED}, {CLIENT}, {FASTLY_V4}",         # (3, 1)
        ]
        lines = self.lines(caplog, headers)
        pairs = [re.findall(r"carried (\S+) hop\(s\), skipped (\S+) ", line)[0]
                 for line in lines]
        assert pairs == [("1", "0"), ("2", "1"), ("2", "2"), ("3", "1")]
        for line in lines:
            assert not any(a in line for a in (CLIENT, FORGED, FASTLY_V4, CGNAT))
            # Nothing finer than the key hop's /16 or /32, and only when
            # entries sit left of it (the (3, 1) line).
            coarse = re.sub(r" is in \S+/(16|32) ", " ", line)
            assert not re.search(r"\d+\.\d+\.\d+\.\d+|:", coarse), line

    def test_the_cdn_line_reads_as_the_runbook_quotes_it(self, caplog):
        """RUNBOOK §5.8 tells the operator to look for this after a deploy."""
        assert self.lines(caplog, [f"{CLIENT}, {FASTLY_V4}"]) == [self.CDN_LINE]

    def test_the_fallbacks_say_which_they_were(self, caplog):
        lines = self.lines(caplog, [f"{FASTLY_V4}, {CGNAT}", "evil, junk"])
        assert lines[0].endswith("the leftmost address, as every address is one")
        assert lines[1].endswith(
            "a fixed one, as the nearest hop that is not one is not an address")

    LEFT = ("; entries sit left of the key's hop, which is in {} ({}) — a "
            "proxy missing from the list, or a header Railway passed through "
            "(RUNBOOK §5.8)")

    @pytest.mark.parametrize("xff, prefix, scope", [
        (f"{CLIENT}, 66.33.22.11", "66.33.0.0/16", "global"),
        (f"{CLIENT}, 66.33.22.11, {CGNAT}", "66.33.0.0/16", "global"),
        (f"{CLIENT}, ::ffff:66.33.22.11", "66.33.0.0/16", "global"),
        (f"{FORGED}, {CLIENT}", "198.51.0.0/16", "not global"),
        (f"{CLIENT}, 2001:db8:aa::1, {FASTLY_V4}", "2001:db8::/32", "not global"),
    ])
    def test_a_key_that_is_not_the_leftmost_says_where_its_hop_is(
            self, caplog, xff, prefix, scope):
        """Entries left of the key mean a proxy the list does not know, or a
        client header Railway passed through: "skipped 0" alone could not
        say which. So the line gives the key hop's /16 or /32 and whether it
        is global, to check against Fastly's list and Railway's ranges; that
        is coarse enough to name no one."""
        [line] = self.lines(caplog, [xff])
        assert line.endswith(self.LEFT.format(prefix, scope))
        assert "66.33.22.11" not in line and CLIENT not in line

    @pytest.mark.parametrize("xff", [
        CLIENT, f"{CLIENT}, {FASTLY_V4}", f"{CLIENT}, {FASTLY_V4}, {CGNAT}",
        f"{FASTLY_V4}, {CGNAT}", "evil, junk", f"{CLIENT}, junk",
    ])
    def test_otherwise_no_prefix(self, caplog, xff):
        [line] = self.lines(caplog, [xff])
        assert "sit left" not in line and "/16" not in line and "/32" not in line

    def test_lines_are_capped_per_process(self, caplog):
        """Eleven distinct pairs, at most `_HOP_NOTE_LIMIT` lines."""
        headers = [", ".join([FORGED] * forged + [CLIENT] + [FASTLY_V4] * edges)
                   for forged in range(4) for edges in range(5)]
        pairs = {(min(forged + 1 + edges, 4), min(edges, 4))
                 for forged in range(4) for edges in range(5)}
        assert len(pairs) == 11
        lines = self.lines(caplog, headers)
        assert len(lines) == ratelimit._HOP_NOTE_LIMIT == 6

    def test_four_or_more_reads_four_plus(self, caplog):
        lines = self.lines(caplog, [", ".join([CLIENT] + [FASTLY_V4] * 5)])
        assert len(lines) == 1
        assert lines[0].startswith(
            "x-forwarded-for carried 4+ hop(s), skipped 4+ known proxy")


class TestEveryCallerUsesTheWalk:
    """One resolver: `auth` and `main` must both key on the client on the CDN
    path, or the two drift apart again (B-14)."""

    XFF = f"{FORGED}, {CLIENT}, {FASTLY_V4}"

    def test_main_client_ip(self):
        assert main._client_ip(Req(self.XFF)) == CLIENT  # type: ignore[arg-type]

    def test_auth_unauthenticated_limiter(self, monkeypatch):
        seen = []

        async def recording(ip):
            seen.append(ip)
        monkeypatch.setattr(auth.deps, "ip_limiter", recording)
        asyncio.run(auth._limit_unauthenticated(Req(self.XFF)))  # type: ignore[arg-type]
        assert seen == [CLIENT]

    def test_the_auth_challenge_route(self, monkeypatch):
        seen = []

        async def recording(ip):
            seen.append(ip)
        monkeypatch.setattr(auth.deps, "ip_limiter", recording)
        r = client.post("/auth/challenge", headers={"x-forwarded-for": self.XFF})
        assert r.status_code == 200
        assert seen == [CLIENT]

    def test_mains_apple_notifications_route(self, monkeypatch):
        seen = []

        async def recording(ip):
            seen.append(ip)
            raise HTTPException(status_code=418)
        monkeypatch.setattr(main, "_enforce_ip_limit", recording)
        r = client.post("/apple/notifications", json={},
                        headers={"x-forwarded-for": self.XFF})
        assert r.status_code == 418
        assert seen == [CLIENT]


class TestTheRealLimiter:
    """End to end: POST /auth/challenge behind uvicorn's proxy-header
    middleware, as deployed, through main's in-process IP bucket shrunk to
    `LIMIT`, so a bypass shows as no 429 at all."""

    LIMIT = 5

    @pytest.fixture(autouse=True)
    def real_limiter(self, monkeypatch):
        monkeypatch.setattr(auth.deps, "ip_limiter", main._enforce_ip_limit)
        monkeypatch.setattr(main, "_ip_limiter", None)
        monkeypatch.setattr(main, "IP_RATE_MAX_REQUESTS", self.LIMIT)
        main._ip_rate_store.clear()
        yield
        main._ip_rate_store.clear()

    def codes(self, xffs):
        return [deployed.post("/auth/challenge", headers={"x-forwarded-for": x}).status_code
                for x in xffs]

    def test_a_fixed_client_behind_fastly_is_refused_at_the_limit(self):
        assert self.codes([f"{CLIENT}, {FASTLY_V4}"] * 8) == [200] * 5 + [429] * 3

    def test_a_rotating_first_header_line_is_refused_at_the_limit(self):
        codes = [deployed.post("/auth/challenge", headers=[
                     ("x-forwarded-for", f"203.0.113.{n}"),
                     ("x-forwarded-for", f"{CLIENT}, {FASTLY_V4}")]).status_code
                 for n in range(1, 9)]
        assert codes == [200] * 5 + [429] * 3

    def test_rotating_inside_one_ipv6_64_is_refused_at_the_limit(self):
        net = ipaddress.ip_network("2001:db8:1234:5678::/64")
        assert self.codes([f"{net[n]}, {FASTLY_V4}" for n in range(1, 9)]) \
            == [200] * 5 + [429] * 3
        main._ip_rate_store.clear()
        assert self.codes([str(net[n * 7919]) for n in range(1, 9)]) == [200] * 5 + [429] * 3

    def test_rotating_a_header_that_is_not_an_address_is_refused_at_the_limit(self):
        assert self.codes([f"evil-{n}" for n in range(8)]) == [200] * 5 + [429] * 3
        main._ip_rate_store.clear()
        assert self.codes([f"evil-{n}, railway-token" for n in range(8)]) \
            == [200] * 5 + [429] * 3

    def test_callers_behind_one_edge_do_not_share_a_bucket(self):
        """Nothing is keyed on the Fastly edge. A bucket per edge was tried,
        at 100 times the IP bucket, and one caller with 100 keys filled it
        and locked everyone behind that edge out for an hour. Here 30 callers
        each spend all of theirs through one edge, and the next is refused
        only by its own bucket."""
        for n in range(1, 31):
            assert self.codes([f"203.0.113.{n}, {FASTLY_V4}"] * self.LIMIT) \
                == [200] * self.LIMIT
        assert self.codes([f"{CLIENT}, {FASTLY_V4}"] * 6) == [200] * 5 + [429]
        assert set(main._ip_rate_store) \
            == {f"203.0.113.{n}" for n in range(1, 31)} | {CLIENT}


class TestAFastlySourceChoosesItsKey:
    """Known, unprobed, and pinned so the assumption stays visible.

    Fastly's addresses are every Fastly customer's. Anyone can put a Fastly
    service of their own in front of this API, or call it from Fastly
    Compute, and the request reaches Railway's edge from a Fastly address
    carrying whatever X-Forwarded-For that service wrote. Railway's edge must
    keep the header its own Fastly service writes — that is how "client,
    edge" arrives — and whether it keeps a Fastly customer's has never been
    probed. If it does, "R, E" is keyed on R, which the caller picks, where
    the rightmost rule keyed E, which it cannot, and nothing else bounds it.
    RUNBOOK §5.8 has the probe, and what to build if it shows the header is
    kept."""

    def test_a_rotating_hop_left_of_a_fastly_address_is_a_new_key(self):
        keys = {key(f"203.0.113.{n}, {FASTLY_V4}") for n in range(1, 50)}
        assert len(keys) == 49
