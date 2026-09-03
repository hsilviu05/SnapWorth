"""Instagram and TikTok readers, the TikTok link flow, and the /social view."""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402
import social  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from tests.test_notify import FAKE_CHAT, FAKE_TOKEN, Recorder  # noqa: E402


class FakePlatforms:
    """Both APIs behind one MockTransport, with scripted responses."""

    def __init__(self) -> None:
        self.ig_profile = {"username": "snapworth.app", "followers_count": 1240, "media_count": 38}
        self.ig_media = {"data": [
            {"caption": "Thrift haul: five finds under $10", "media_type": "IMAGE",
             "timestamp": "2026-09-02T18:02:11+0000", "permalink": "https://instagram.com/p/x",
             "like_count": 210, "comments_count": 14}]}
        self.ig_status = 200
        self.tt_user = {"data": {"user": {"display_name": "snapworth", "follower_count": 860,
                                          "likes_count": 12300, "video_count": 41}}}
        self.tt_videos = {"data": {"videos": [
            {"id": "1", "title": "POV: the $4 jacket", "create_time": 1756771200,
             "view_count": 3200, "like_count": 210, "comment_count": 14, "share_count": 9,
             "share_url": "https://tiktok.com/@snapworth/video/1"}]}}
        self.token_requests: list[dict] = []
        self.token_response = {"access_token": "acc-1", "expires_in": 86400,
                               "refresh_token": "ref-1", "refresh_expires_in": 31536000,
                               "open_id": "oid"}
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if "graph.facebook.com" in url:
            if url.split("?")[0].endswith("/media"):
                return httpx.Response(200, json=self.ig_media)
            if self.ig_status != 200:
                return httpx.Response(self.ig_status, json={"error": {"code": 190, "message": "expired"}})
            return httpx.Response(200, json=self.ig_profile)
        if url.endswith("/oauth/token/"):
            self.token_requests.append(dict(parse_qs(request.content.decode())))
            return httpx.Response(200, json=self.token_response)
        if "/user/info/" in url:
            # The Display API's user endpoint is a GET; a POST is what TikTok
            # answered 404 to in production. The mock enforces the verb so the
            # regression cannot come back green.
            if request.method != "GET":
                return httpx.Response(404, json={"error": {"code": "route_not_found",
                                                           "message": "not found"}})
            return httpx.Response(200, json=self.tt_user)
        if "/video/list/" in url:
            return httpx.Response(200, json=self.tt_videos)
        return httpx.Response(404)


@pytest.fixture
def cache():
    return ResilientCache(None, InMemoryCache())


@pytest.fixture
def platforms():
    return FakePlatforms()


def make_social(cache, platforms, *, ig=True, tt=True) -> social.Social:
    client = httpx.AsyncClient(transport=httpx.MockTransport(platforms.handler))
    return social.Social(
        instagram=social.InstagramClient("178" if ig else "", "tok" if ig else "", client=client),
        tiktok=social.TikTokClient("key" if tt else "", "secret" if tt else "",
                                   "https://api.snapworth.eu/social/tiktok/callback", cache, client=client))


class TestInstagram:
    @pytest.mark.asyncio
    async def test_reads_profile_and_recent_posts(self, cache, platforms):
        account = await make_social(cache, platforms).instagram.account()
        assert account.ok
        assert (account.handle, account.followers, account.posts) == ("snapworth.app", 1240, 38)
        (post,) = account.recent
        assert post.likes == 210 and post.comments == 14
        assert post.created_at is not None

    @pytest.mark.asyncio
    async def test_expired_token_is_a_note_not_an_error(self, cache, platforms):
        platforms.ig_status = 400
        account = await make_social(cache, platforms).instagram.account()
        assert not account.ok and "token expired" in account.note

    @pytest.mark.asyncio
    async def test_unconfigured(self, cache, platforms):
        account = await make_social(cache, platforms, ig=False).instagram.account()
        assert "not configured" in account.note


class TestTikTokLink:
    @pytest.mark.asyncio
    async def test_unlinked_account_offers_an_authorisation_url(self, cache, platforms):
        account = await make_social(cache, platforms).tiktok.account()
        assert account.note.startswith("not linked — https://www.tiktok.com/v2/auth/authorize/")
        query = parse_qs(urlparse(account.note.split(" — ", 1)[1]).query)
        assert query["client_key"] == ["key"]
        assert query["scope"] == ["user.info.basic,user.info.stats,video.list"]
        assert query["redirect_uri"] == ["https://api.snapworth.eu/social/tiktok/callback"]
        assert await cache.get(f"opssocial:tiktok:state:{query['state'][0]}") == "1"

    @pytest.mark.asyncio
    async def test_callback_exchanges_the_code_once(self, cache, platforms):
        s = make_social(cache, platforms)
        state = parse_qs(urlparse(await s.tiktok.begin_link()).query)["state"][0]
        assert await s.tiktok.complete_link("the-code", state) is True
        assert platforms.token_requests[0]["code"] == ["the-code"]
        assert platforms.token_requests[0]["grant_type"] == ["authorization_code"]
        assert await s.tiktok.linked()
        # The state is single-use; a replay or a forgery is refused.
        assert await s.tiktok.complete_link("the-code", state) is False
        assert await s.tiktok.complete_link("the-code", "made-up") is False

    @pytest.mark.asyncio
    async def test_linked_account_reads_stats_and_refreshes_when_stale(self, cache, platforms):
        s = make_social(cache, platforms)
        await cache.set(social.TIKTOK_TOKENS_KEY, json.dumps({
            "access_token": "old", "expires_at": int(time.time()) + 60,   # inside the margin
            "refresh_token": "ref-0"}), 600)
        account = await s.tiktok.account()
        assert account.ok
        assert (account.handle, account.followers, account.posts, account.total_likes) == \
            ("snapworth", 860, 41, 12300)
        (video,) = account.recent
        assert (video.views, video.likes, video.comments, video.shares) == (3200, 210, 14, 9)
        assert platforms.token_requests[0]["grant_type"] == ["refresh_token"]
        assert json.loads(await cache.get(social.TIKTOK_TOKENS_KEY))["access_token"] == "acc-1"

    @pytest.mark.asyncio
    async def test_fresh_token_is_not_refreshed(self, cache, platforms):
        s = make_social(cache, platforms)
        await cache.set(social.TIKTOK_TOKENS_KEY, json.dumps({
            "access_token": "fresh", "expires_at": int(time.time()) + 3600, "refresh_token": "r"}), 600)
        assert (await s.tiktok.account()).ok
        assert platforms.token_requests == []


class TestCallbackRoute:
    def test_route_completes_or_refuses(self, cache, platforms):
        s = make_social(cache, platforms)
        social.configure(s)
        app = FastAPI()
        app.include_router(social.router)
        client = TestClient(app)
        import asyncio
        state = parse_qs(urlparse(asyncio.run(s.tiktok.begin_link())).query)["state"][0]

        ok = client.get(f"/social/tiktok/callback?code=abc&state={state}")
        assert ok.status_code == 200 and "linked" in ok.text
        replay = client.get(f"/social/tiktok/callback?code=abc&state={state}")
        assert replay.status_code == 400
        denied = client.get("/social/tiktok/callback?error=access_denied")
        assert denied.status_code == 400


class TestSocialInBot:
    @pytest_asyncio.fixture
    async def bot(self, cache, platforms):
        recorder = Recorder()
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)))
        notify.configure(cache, notifier=notifier, social=make_social(cache, platforms))
        yield recorder
        await notify.aclose()

    @pytest.mark.asyncio
    async def test_social_command_renders_both_platforms(self, bot, cache, platforms):
        await cache.set(social.TIKTOK_TOKENS_KEY, json.dumps({
            "access_token": "fresh", "expires_at": int(time.time()) + 3600, "refresh_token": "r"}), 600)
        text = await notify.handle_command("/social")
        assert "<b>Instagram</b> @snapworth.app — 1,240 followers · 38 posts" in text
        assert "Thrift haul: five finds under $10" in text and "210 likes · 14 comments" in text
        assert "<b>TikTok</b> @snapworth — 860 followers · 41 videos · 12.3K likes" in text
        assert "3.2K views" in text

    @pytest.mark.asyncio
    async def test_unlinked_tiktok_shows_a_tap_to_link(self, bot):
        text = await notify.handle_command("/social")
        assert 'tap to link your account</a>' in text
        assert "tiktok.com/v2/auth/authorize" in text

    @pytest.mark.asyncio
    async def test_digest_line_carries_follower_deltas(self, bot, cache):
        from datetime import datetime, timedelta, timezone
        yesterday = notify._day(datetime.now(timezone.utc) - timedelta(days=1))
        await cache.set(notify._social_snapshot_key(yesterday),
                        json.dumps({"instagram": 1228}), 600)
        line = await notify._social_line()
        assert line == "Social: Instagram 1,240 (▲ 12)"        # TikTok unlinked: omitted
        assert json.loads(await cache.get(notify._social_snapshot_key(notify._day())))["instagram"] == 1240

    @pytest.mark.asyncio
    async def test_unconfigured_social_is_quiet_in_digest_and_explains_in_command(self, cache):
        recorder = Recorder()
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)))
        notify.configure(cache, notifier=notifier)
        try:
            assert await notify._social_line() == ""
            assert "Not configured" in await notify.handle_command("/social")
        finally:
            await notify.aclose()


class TestTikTokVerbs:
    @pytest.mark.asyncio
    async def test_user_info_is_fetched_with_get(self, cache, platforms):
        sc = make_social(cache, platforms)
        assert await sc.tiktok.complete_link("code", await _state(sc, cache))
        account = await sc.tiktok.account()
        assert account.ok, account.note
        assert account.followers == 860

    @pytest.mark.asyncio
    async def test_api_error_names_tiktoks_code(self, cache, platforms):
        sc = make_social(cache, platforms)
        assert await sc.tiktok.complete_link("code", await _state(sc, cache))
        platforms.tt_user = None

        def failing(request: httpx.Request) -> httpx.Response:
            if "/user/info/" in str(request.url):
                return httpx.Response(401, json={"error": {"code": "access_token_invalid",
                                                           "message": "bad token"}})
            return platforms.handler(request)
        sc.tiktok._client = httpx.AsyncClient(transport=httpx.MockTransport(failing))
        account = await sc.tiktok.account()
        assert account.note == "API error 401 (access_token_invalid)"


async def _state(sc: social.Social, cache) -> str:
    """Start a link and return the state nonce the callback must echo."""
    url = await sc.tiktok.begin_link()
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(url).query)["state"][0]
