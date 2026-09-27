"""The per-IP rate-limit key behind Railway's edge.

Railway's edge writes the whole `X-Forwarded-For` header: it drops whatever
the client sent and writes `client` or `client, edge`. Every production
request on 2026-09-27 carried two entries, and forged headers never reached
the left. So `ratelimit.client_ip` keys on the first entry. Until that day
it took the rightmost entry, and then, from #250, the nearest one that is
not a Fastly or internal address; the second entry is Railway's own edge,
in 95.173.0.0/16, which is neither, so both keyed everyone behind one edge
on one 60/h bucket. A header of three or more entries means Railway's shape
changed, and falls back to #250's walk with a warning. These pin both, with
the normalisation, malformed and absent-header cases around them.
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
import referral
from tests.test_main import client

# The app as the container serves it: uvicorn runs with --proxy-headers and
# --forwarded-allow-ips='*', so its middleware rewrites `request.client.host`
# to the leftmost X-Forwarded-For entry before the app sees the request. A
# fake request with a socket-peer host hides that.
deployed = TestClient(ProxyHeadersMiddleware(main.app, trusted_hosts="*"))


async def _echo_key(request: Request) -> PlainTextResponse:
    return PlainTextResponse(ratelimit.client_ip(request))


async def _echo_host(request: Request) -> PlainTextResponse:
    """What uvicorn's access log prints as the client: `request.client.host`."""
    return PlainTextResponse(request.client.host if request.client else "")

_echo = TestClient(ProxyHeadersMiddleware(
    Starlette(routes=[Route("/", _echo_key), Route("/host", _echo_host)]),
    trusted_hosts="*"))


def deployed_key(*lines: str) -> str:
    """`client_ip` behind uvicorn's proxy-header middleware, as deployed."""
    return _echo.get("/", headers=[("x-forwarded-for", line) for line in lines]).text


def access_log_host(*lines: str) -> str:
    return _echo.get("/host", headers=[("x-forwarded-for", line) for line in lines]).text

UNPARSEABLE = "unparseable"

CLIENT = "198.51.100.23"
FORGED = "203.0.113.7"
# In 95.173.0.0/16, the /16 production logged for the second entry on
# 2026-09-27: Railway's edge. Not a documentation address, because the
# point is that it is global and on neither of the walk's lists.
EDGE = "95.173.10.20"
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
    monkeypatch.setattr(ratelimit, "_EXTRA_HOPS_WARNED", False)


def records(caplog, headers):
    """Every log record `client_ip` writes for `headers`, in order."""
    with caplog.at_level(logging.INFO, logger="snapworth.ratelimit"):
        for xff in headers:
            ratelimit.client_ip(Req(xff))
    return [r for r in caplog.records if r.name == "snapworth.ratelimit"]


class TestTheFirstEntry:
    def test_client_then_railways_edge_is_the_client(self):
        """What every production request carried on 2026-09-27. #250's walk
        keyed this on the edge, which is on neither of its lists, and so did
        the rightmost rule before it: one bucket for everyone behind it."""
        assert key(f"{CLIENT}, {EDGE}") == CLIENT

    def test_one_entry_is_the_client(self):
        assert key(CLIENT) == CLIENT

    @pytest.mark.parametrize("second", [
        EDGE, FASTLY_V4, FASTLY_V6, CGNAT, "2001:db8::1", "junk",
        f"{CLIENT}:5678", "999.1.1.1",
    ])
    def test_the_second_entry_never_decides(self, second):
        """Railway writes it. Whatever it holds, the first entry is the key."""
        assert key(f"{CLIENT}, {second}") == CLIENT

    def test_the_key_is_what_uvicorns_access_log_prints(self):
        """RUNBOOK §5.8's owner probe reads the leftmost entry off uvicorn's
        access log, which prints `request.client.host`. For an IPv4 caller
        that is the key itself."""
        for xff in (CLIENT, f"{CLIENT}, {EDGE}"):
            assert access_log_host(xff) == deployed_key(xff) == CLIENT


class TestIPv6Keys:
    """An IPv6 caller is keyed on its /64, not its address.

    A /64 is what one line is given, a home router's LAN or a phone on
    cellular or a VPS, and every address in it is the holder's to use: iOS
    rotates temporary addresses inside it on its own. Keyed per address, one
    line would be 2^64 fresh buckets."""

    NET = ipaddress.ip_network("2001:db8:1234:5678::/64")

    def addresses(self, n):
        return [str(self.NET[i * 7919 + 1]) for i in range(n)]

    def test_rotating_inside_one_64_is_one_key(self):
        alone = {key(a) for a in self.addresses(500)}
        behind_the_edge = {key(f"{a}, {EDGE}") for a in self.addresses(500)}
        assert alone == behind_the_edge == {"2001:db8:1234:5678::/64"}

    def test_two_64s_are_two_keys(self):
        assert key("2001:db8:1234:5678::1") != key("2001:db8:1234:5679::1")

    def test_however_the_entry_is_written(self):
        assert key(f"2001:DB8:0:0::23, {EDGE}") == "2001:db8::/64"
        assert key("2001:db8:0:0:ffff:ffff:ffff:ffff") == "2001:db8::/64"

    def test_a_zone_id_does_not_rotate_the_key(self):
        keys = {key(f"2001:db8::1%{n}, {EDGE}") for n in range(20)}
        assert keys == {"2001:db8::/64"}

    def test_an_ipv4_mapped_client_keys_with_its_ipv4_address(self):
        assert key(f"::ffff:{CLIENT}") == CLIENT
        assert key(f"::ffff:c633:6417, {EDGE}") == CLIENT
        assert key(CLIENT) == CLIENT

    def test_ipv4_is_still_one_bucket_per_address(self):
        assert key("198.51.100.23") != key("198.51.100.24")


class TestRepeatedHeaderLines:
    """A header may arrive as several lines, which mean their join in order
    (RFC 9110 §5.3), and uvicorn's own proxy-header middleware reads it that
    way. The first line alone has the same first entry, but not the same
    count, and the count is what says whether Railway's shape still holds."""

    def test_two_lines_of_one_entry_each_are_two_entries(self, caplog):
        assert key([CLIENT, EDGE]) == CLIENT
        [note] = records(caplog, [[CLIENT, EDGE]])
        assert note.getMessage().startswith("x-forwarded-for carried 2 hop(s);")

    def test_a_third_entry_on_a_line_of_its_own_is_counted(self):
        """Read as its first line alone, each of these is one or two entries
        and keys on its first. Joined, each is three, which Railway never
        writes, so the walk decides."""
        assert key([FORGED, f"{CLIENT}, {FASTLY_V4}"]) == CLIENT
        assert key([f"{FORGED}, {CLIENT}", EDGE]) == EDGE
        assert key([FORGED, CLIENT, EDGE]) == EDGE

    def test_rotating_the_first_line_of_three_entries_does_not_rotate_the_key(self):
        keys = {key([f"203.0.113.{n}", f"{CLIENT}, {EDGE}"]) for n in range(1, 50)}
        assert keys == {EDGE}

    def test_through_the_route(self, monkeypatch):
        seen = []

        async def recording(ip):
            seen.append(ip)
        monkeypatch.setattr(auth.deps, "ip_limiter", recording)
        client.post("/auth/challenge", headers=[
            ("x-forwarded-for", CLIENT), ("x-forwarded-for", EDGE)])
        for n in range(1, 4):
            client.post("/auth/challenge", headers=[
                ("x-forwarded-for", f"203.0.113.{n}"),
                ("x-forwarded-for", f"{CLIENT}, {EDGE}")])
        assert seen == [CLIENT] + [EDGE] * 3


class TestThreeOrMoreEntries:
    """More entries than Railway writes: its stripping failed or the topology
    changed, so the first entry may be the caller's own. #250's walk decides
    instead: from the right, past Fastly's published ranges and internal
    ones, to the first hop that is neither. It never reaches an entry left of
    an address it does not know, and Railway's edge is one, so on Railway's
    shape it keys on the edge: a shared bucket, never the caller's choice."""

    def test_railways_shape_with_an_entry_in_front_keys_on_the_edge(self):
        assert key(f"{FORGED}, {CLIENT}, {EDGE}") == EDGE
        keys = {key(f"203.0.113.{n}, {CLIENT}, {EDGE}") for n in range(1, 50)}
        assert keys == {EDGE}

    @pytest.mark.parametrize("forged", [FORGED, "10.9.9.9", "151.101.9.9", "evil"])
    def test_forged_then_client_then_a_known_proxy_is_the_client(self, forged):
        assert key(f"{forged}, {CLIENT}, {FASTLY_V4}") == CLIENT
        assert key(f"{forged}, {CLIENT}, {CGNAT}") == CLIENT

    def test_rotating_the_forged_entry_does_not_rotate_the_key(self):
        keys = {key(f"203.0.113.{n}, {CLIENT}, {FASTLY_V4}") for n in range(1, 50)}
        assert keys == {CLIENT}

    @pytest.mark.parametrize("internal", [
        CGNAT, "100.127.255.254", "10.1.2.3", "172.16.0.9", "192.168.1.1",
        "127.0.0.1", "169.254.1.1", "::1", "fd00::1", "fe80::1",
    ])
    def test_an_internal_hop_is_skipped(self, internal):
        assert key(f"{FORGED}, {CLIENT}, {internal}") == CLIENT

    def test_every_published_fastly_range_is_skipped(self):
        for published in ratelimit._FASTLY_EDGE_RANGES:
            network = ipaddress.ip_network(published)
            for fastly in (network[0], network[-1]):
                assert key(f"{FORGED}, {CLIENT}, {fastly}") == CLIENT, published
        assert key(f"{FORGED}, {CLIENT}, ::ffff:{FASTLY_V4}") == CLIENT

    def test_documentation_ranges_are_not_internal(self):
        """`is_global` is false for these too, which is why the internal
        ranges are listed rather than read off it."""
        for doc in ("192.0.2.4", "198.51.100.4", "203.0.113.4"):
            assert key(f"{FORGED}, {doc}, {CGNAT}") == doc
        assert key(f"{FORGED}, 2001:db8::4, {CGNAT}") == "2001:db8::/64"

    def test_every_hop_a_known_proxy_is_the_leftmost(self):
        assert key(f"{FASTLY_V4}, {CGNAT}, {FASTLY_V6}") == FASTLY_V4
        assert key(f"{CGNAT}, {FASTLY_V4}, {CGNAT}") == CGNAT

    @pytest.mark.parametrize("xff", [
        f"{CLIENT}, not-an-ip, {FASTLY_V4}",
        f"junk, {CGNAT}, {FASTLY_V4}",
        f"{FORGED}, {CLIENT}, {EDGE}:5678",
        "r1, r2, junk",
    ])
    def test_a_hop_that_is_not_an_address_stops_the_walk(self, xff):
        """Skipping it would walk on into the caller's end of the header."""
        assert key(xff) == UNPARSEABLE
        assert deployed_key(xff) == UNPARSEABLE

    def test_a_non_address_left_of_the_client_is_never_reached(self):
        assert key(f"evil, {CLIENT}, {FASTLY_V4}") == CLIENT

    WARNING = ("x-forwarded-for carried 3 hop(s), more than the 2 Railway "
               "writes: its stripping failed or the topology changed. Such "
               "requests are keyed by the walk from the right, which skipped "
               "1 known proxy hop(s) (Fastly edge or internal); the per-IP key "
               "is the nearest hop that is not a known proxy. Once per process "
               "(RUNBOOK §5.8)")

    def test_warns_once_per_process_with_counts_only(self, caplog):
        logged = records(caplog, [
            f"{FORGED}, {CLIENT}, {FASTLY_V4}",
            f"{FORGED}, {CLIENT}, {EDGE}",
            ", ".join([FORGED] * 5 + [CLIENT, EDGE]),
            [FORGED, f"{CLIENT}, {EDGE}"],
        ])
        assert [(r.levelno, r.getMessage()) for r in logged] \
            == [(logging.WARNING, self.WARNING)]
        message = logged[0].getMessage()
        assert not re.search(r"\d+\.\d+\.\d+\.\d+", message)
        assert not any(a in message for a in (CLIENT, FORGED, EDGE, FASTLY_V4))

    def test_the_warning_says_how_the_walk_ended(self, caplog, monkeypatch):
        endings = []
        for xff in (f"{FORGED}, {CLIENT}, {EDGE}", "evil, junk, x",
                    f"{FASTLY_V4}, {CGNAT}, {FASTLY_V6}"):
            monkeypatch.setattr(ratelimit, "_EXTRA_HOPS_WARNED", False)
            caplog.clear()
            [warning] = records(caplog, [xff])
            assert warning.levelno == logging.WARNING
            endings.append(re.findall(r"skipped (\d+) .*the per-IP key is (.*)\. Once",
                                      warning.getMessage())[0])
        assert endings == [
            ("0", "the nearest hop that is not a known proxy"),
            ("0", "a fixed one, as the nearest hop that is not a known proxy "
                  "is not an address"),
            ("3", "the leftmost address, as every hop is a known proxy"),
        ]


class TestTheNote:
    """The production evidence: counts and fixed words, once per pair."""

    POST_DEPLOY_LINE = ("x-forwarded-for carried 2 hop(s); the per-IP key is "
                        "the first entry (Railway strips client values)")

    def lines(self, caplog, headers):
        return [r.getMessage() for r in records(caplog, headers)
                if r.levelno == logging.INFO]

    def test_the_post_deploy_line_reads_as_the_runbook_quotes_it(self, caplog):
        """RUNBOOK §5.8 and the 1.5.1 pre-submit list tell the operator to
        look for this after a deploy."""
        assert self.lines(caplog, [f"{CLIENT}, {EDGE}"]) == [self.POST_DEPLOY_LINE]

    def test_once_per_distinct_pair_and_never_an_address(self, caplog):
        headers = [
            CLIENT,                                     # (1, address)
            "198.51.100.24",                            # (1, address) again
            f"{CLIENT}, {EDGE}",                        # (2, address)
            f"2001:db8::23, {FASTLY_V6}",               # (2, address) again
            "evil",                                     # (1, not)
            f"evil, {EDGE}",                            # (2, not)
            f"junk, {CLIENT}",                          # (2, not) again
            f"{FORGED}, {CLIENT}, {EDGE}",              # three: the warning
        ]
        lines = self.lines(caplog, headers)
        assert lines == [
            "x-forwarded-for carried 1 hop(s); the per-IP key is the first "
            "entry (Railway strips client values)",
            self.POST_DEPLOY_LINE,
            "x-forwarded-for carried 1 hop(s); the per-IP key is a fixed one, "
            "as the first entry is not an address",
            "x-forwarded-for carried 2 hop(s); the per-IP key is a fixed one, "
            "as the first entry is not an address",
        ]
        for line in lines:
            assert not re.search(r"\d+\.\d+\.\d+\.\d+|::", line), line

    def test_no_header_logs_nothing(self, caplog):
        assert records(caplog, [None, ", ,"]) == []


class TestNoEntries:
    @pytest.mark.parametrize("xff", [
        f" , {CLIENT} ,, {EDGE}",
        f"{CLIENT}, ",
        f"{CLIENT},",
        f",{CLIENT}",
        f" , , {CLIENT}, ,{EDGE}, ",
    ])
    def test_empty_entries_are_not_entries(self, xff):
        """Nor do they count towards three."""
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
        assert deployed_key() == "testclient"

    def test_the_key_is_truncated_on_every_path(self):
        """It reaches a cache key and is attacker-influenced. A parsed address
        is short once keyed: IPv6 allows a zone id of any length, and the
        /64 drops it."""
        scoped = "2001:db8::1%" + "z" * 200
        assert key(f"{scoped}, {EDGE}") == "2001:db8::/64"
        assert key(f"{FORGED}, {CLIENT}, {scoped}") == "2001:db8::/64"
        assert key("fe80::1%" + "z" * 200) == "fe80::/64"
        assert key("x" * 500, host="y" * 500) == UNPARSEABLE
        assert key(None, host="x" * 500) == "x" * 64


class TestAFirstEntryThatIsNotAnAddress:
    """Every request whose first entry is not an address shares one fixed
    key. Deployed, `request.client.host` is that entry verbatim (`deployed`,
    above), so keying on it or on the raw text would be a fresh bucket per
    value; were Railway ever to write something that is not a bare address
    there, everyone would share the one bucket instead, and the note says so."""

    def test_behind_uvicorn_no_address_is_one_bucket(self):
        assert deployed_key("evil-a") == deployed_key("evil-b") == UNPARSEABLE
        assert deployed_key("evil-a, evil-b") == UNPARSEABLE

    def test_rotating_it_does_not_rotate_the_key(self):
        keys = {deployed_key(f"evil-{n}, {EDGE}") for n in range(30)}
        assert keys == {UNPARSEABLE}
        keys = {deployed_key(f"{CLIENT}:{n}, {EDGE}") for n in range(5000, 5030)}
        assert keys == {UNPARSEABLE}

    @pytest.mark.parametrize("xff", [
        f"{CLIENT}:5678, {EDGE}", "evil", "999.1.1.1, x", "unknown",
        f"[2001:db8::1], {EDGE}",
    ])
    def test_it_is_the_fixed_key(self, xff):
        assert key(xff) == UNPARSEABLE
        assert key(xff, host=None) == UNPARSEABLE


class TestEveryCallerUsesIt:
    """One resolver: `auth`, `main` and `referral` must all key on the first
    entry on Railway's shape, and on the walk past it, or they drift apart
    again (B-14)."""

    CASES = [(f"{CLIENT}, {EDGE}", CLIENT),
             (f"{FORGED}, {CLIENT}, {FASTLY_V4}", CLIENT),
             (f"{FORGED}, {CLIENT}, {EDGE}", EDGE)]

    @pytest.mark.parametrize("xff, expected", CASES)
    def test_main_client_ip(self, xff, expected):
        assert main._client_ip(Req(xff)) == expected  # type: ignore[arg-type]

    @pytest.mark.parametrize("xff, expected", CASES)
    def test_auth_unauthenticated_limiter(self, monkeypatch, xff, expected):
        seen = []

        async def recording(ip):
            seen.append(ip)
        monkeypatch.setattr(auth.deps, "ip_limiter", recording)
        asyncio.run(auth._limit_unauthenticated(Req(xff)))  # type: ignore[arg-type]
        assert seen == [expected]

    @pytest.mark.parametrize("xff, expected", CASES)
    def test_the_auth_challenge_route(self, monkeypatch, xff, expected):
        seen = []

        async def recording(ip):
            seen.append(ip)
        monkeypatch.setattr(auth.deps, "ip_limiter", recording)
        r = client.post("/auth/challenge", headers={"x-forwarded-for": xff})
        assert r.status_code == 200
        assert seen == [expected]

    @pytest.mark.parametrize("xff, expected", CASES)
    def test_mains_apple_notifications_route(self, monkeypatch, xff, expected):
        seen = []

        async def recording(ip):
            seen.append(ip)
            raise HTTPException(status_code=418)
        monkeypatch.setattr(main, "_enforce_ip_limit", recording)
        r = client.post("/apple/notifications", json={},
                        headers={"x-forwarded-for": xff})
        assert r.status_code == 418
        assert seen == [expected]

    @pytest.mark.parametrize("xff, expected", CASES)
    def test_the_referral_routes(self, monkeypatch, xff, expected):
        seen = []

        async def recording(route, subject, ip):
            seen.append((route, ip))
        monkeypatch.setattr(referral, "config",
                            referral.ReferralConfig(enabled=True,
                                                    friend_offer="referral-friend-7d"))
        monkeypatch.setattr(referral, "limiter", recording)
        token, _ = auth.deps.signer.mint("subj-client-ip")
        headers = {"Authorization": f"Bearer {token}", "x-forwarded-for": xff}
        client.post("/referral/status", json={"device_id": "dev-a"}, headers=headers)
        client.post("/referral/claim", json={"device_id": "dev-a", "code": "ZZZZZZ"},
                    headers=headers)
        assert seen == [("status", expected), ("claim", expected)]


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

    def test_a_fixed_client_behind_the_edge_is_refused_at_the_limit(self):
        assert self.codes([f"{CLIENT}, {EDGE}"] * 8) == [200] * 5 + [429] * 3

    def test_a_rotating_first_header_line_is_refused_at_the_limit(self):
        """Three entries: the walk, which keys on the edge here."""
        codes = [deployed.post("/auth/challenge", headers=[
                     ("x-forwarded-for", f"203.0.113.{n}"),
                     ("x-forwarded-for", f"{CLIENT}, {EDGE}")]).status_code
                 for n in range(1, 9)]
        assert codes == [200] * 5 + [429] * 3

    def test_rotating_inside_one_ipv6_64_is_refused_at_the_limit(self):
        net = ipaddress.ip_network("2001:db8:1234:5678::/64")
        assert self.codes([f"{net[n]}, {EDGE}" for n in range(1, 9)]) \
            == [200] * 5 + [429] * 3
        main._ip_rate_store.clear()
        assert self.codes([str(net[n * 7919]) for n in range(1, 9)]) == [200] * 5 + [429] * 3

    def test_rotating_a_first_entry_that_is_not_an_address_is_refused_at_the_limit(self):
        assert self.codes([f"evil-{n}" for n in range(8)]) == [200] * 5 + [429] * 3
        main._ip_rate_store.clear()
        assert self.codes([f"evil-{n}, {EDGE}" for n in range(8)]) \
            == [200] * 5 + [429] * 3

    def test_thirty_users_behind_one_edge_address_each_get_their_own_bucket(self):
        """The launch scenario. Every user Railway routes through one edge
        arrives as "user, edge" with the same edge. #250 keyed all of them on
        it, so the sixth request from any of them here was refused; that is
        60/h shared by everyone behind the edge in production. Here 30 users
        each spend all of theirs through one edge, and the next is refused
        only by its own bucket."""
        for n in range(1, 31):
            assert self.codes([f"203.0.113.{n}, {EDGE}"] * self.LIMIT) \
                == [200] * self.LIMIT
        assert self.codes([f"{CLIENT}, {EDGE}"] * 6) == [200] * 5 + [429]
        assert set(main._ip_rate_store) \
            == {f"203.0.113.{n}" for n in range(1, 31)} | {CLIENT}


class TestTheResidualRisk:
    """Known, and pinned so the assumption stays visible.

    The first entry is the caller only because Railway's edge strips a
    client's header, which forged probes showed on 2026-09-27. If it ever
    stops while still sending two entries, "forged, client" is keyed on
    the forged entry, a fresh bucket per value, and nothing in the header
    tells that request from a real one. App Attest where it is required, the
    per-device buckets and the daily spend alert once it is set still apply;
    this limit does not. RUNBOOK §5.8
    has the probe that checks stripping, after any Railway networking change
    and monthly."""

    def test_two_entries_are_trusted_to_be_railways(self):
        keys = {key(f"203.0.113.{n}, {CLIENT}") for n in range(1, 50)}
        assert len(keys) == 49
