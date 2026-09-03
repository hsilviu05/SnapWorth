"""Social reach for the operator: Instagram and TikTok numbers in the bot.

Marketing happens off the backend, on Instagram and TikTok. This pulls the
numbers those platforms publish about the app's own accounts — followers and
the last few posts' views, likes and comments — so `/social` and the daily
digest can put them next to scans and subscriptions.

Everything is read-only, about accounts the operator owns, and off unless
configured. Each platform has its own gate:

* **Instagram** (Graph API): a Business or Creator account linked to a
  Facebook Page, a Meta app with the Instagram permissions approved, and a
  long-lived user token. Configured with `IG_USER_ID` and `IG_ACCESS_TOKEN`.
* **TikTok** (Display API): a TikTok for Developers app with Login Kit and the
  `user.info.basic`, `user.info.stats` and `video.list` scopes. Configured
  with `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`, then *linked* once:
  the bot hands the operator an authorisation link, TikTok redirects to
  `/social/tiktok/callback`, and the tokens live in the cache from then on
  (access tokens last a day and are refreshed here; refresh tokens a year).

Failures are reported inside the message ("Instagram: token expired") rather
than raised — a marketing dashboard must never be able to fail a deploy.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

log = logging.getLogger("snapworth.social")

IG_GRAPH = "https://graph.facebook.com/v21.0"
TIKTOK_API = "https://open.tiktokapis.com/v2"
TIKTOK_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_SCOPES = "user.info.basic,user.info.stats,video.list"

HTTP_TIMEOUT = 10.0
RECENT_POSTS = 3

TIKTOK_TOKENS_KEY = "opssocial:tiktok:tokens"
TIKTOK_STATE_TTL = 10 * 60
TOKENS_TTL = 60 * 60 * 24 * 400
# Refresh an access token this long before it expires; TikTok's last 24h.
REFRESH_MARGIN_SECONDS = 15 * 60


@dataclass
class Post:
    title: str
    created_at: int | None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    url: str | None = None


@dataclass
class Account:
    platform: str                      # "instagram" | "tiktok"
    handle: str | None = None
    followers: int | None = None
    posts: int | None = None
    total_likes: int | None = None
    recent: list[Post] = field(default_factory=list)
    # Human-readable state when there is nothing to show: "not configured",
    # "not linked — <url>", "token expired", "API error 400".
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.note is None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Instagram ────────────────────────────────────────────────────────────────

class InstagramClient:
    """Reads the operator's own Instagram Business/Creator account."""

    def __init__(self, user_id: str, access_token: str, client=None) -> None:
        self._user_id = user_id
        self._token = access_token
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._user_id and self._token)

    async def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        return self._client

    async def account(self) -> Account:
        if not self.configured:
            return Account("instagram", note="not configured — set IG_USER_ID and IG_ACCESS_TOKEN")
        try:
            client = await self._http()
            profile = await client.get(
                f"{IG_GRAPH}/{self._user_id}",
                params={"fields": "username,followers_count,media_count",
                        "access_token": self._token})
            if profile.status_code != 200:
                return Account("instagram", note=self._error(profile))
            data = profile.json()
            media = await client.get(
                f"{IG_GRAPH}/{self._user_id}/media",
                params={"fields": "caption,media_type,timestamp,permalink,like_count,comments_count",
                        "limit": RECENT_POSTS, "access_token": self._token})
            posts = []
            if media.status_code == 200:
                for item in (media.json().get("data") or [])[:RECENT_POSTS]:
                    posts.append(Post(
                        title=(item.get("caption") or item.get("media_type") or "post"),
                        created_at=_parse_iso(item.get("timestamp")),
                        likes=_int(item.get("like_count")),
                        comments=_int(item.get("comments_count")),
                        url=item.get("permalink")))
            return Account("instagram", handle=data.get("username"),
                           followers=_int(data.get("followers_count")),
                           posts=_int(data.get("media_count")), recent=posts)
        except Exception as exc:
            log.warning("instagram fetch failed: %s", type(exc).__name__)
            return Account("instagram", note=f"unreachable ({type(exc).__name__})")

    @staticmethod
    def _error(resp) -> str:
        try:
            err = resp.json().get("error") or {}
        except Exception:
            err = {}
        code = err.get("code")
        if code == 190:
            return "token expired or invalid — generate a new long-lived token"
        return f"API error {resp.status_code}" + (f" ({err.get('message')})" if err.get("message") else "")


def _parse_iso(value) -> int | None:
    """Instagram timestamps look like 2026-09-02T18:02:11+0000."""
    if not isinstance(value, str):
        return None
    from datetime import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(datetime.strptime(value, fmt).timestamp())
        except ValueError:
            continue
    return None


# ── TikTok ───────────────────────────────────────────────────────────────────

class TikTokClient:
    """Reads the operator's own TikTok account via the Display API.

    Needs a one-time link: `begin_link()` returns the authorisation URL, TikTok
    redirects to `redirect_uri` with a code, `complete_link()` exchanges it and
    keeps the tokens in the cache. The state nonce ties the two halves together
    and expires, so a stale or forged callback is refused.
    """

    def __init__(self, client_key: str, client_secret: str, redirect_uri: str,
                 cache, client=None) -> None:
        self._key = client_key
        self._secret = client_secret
        self._redirect = redirect_uri
        self._cache = cache
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._key and self._secret)

    async def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        return self._client

    # ── linking ──
    async def begin_link(self) -> str:
        state = secrets.token_urlsafe(24)
        await self._cache.set(f"opssocial:tiktok:state:{state}", "1", TIKTOK_STATE_TTL)
        return TIKTOK_AUTHORIZE + "?" + urlencode({
            "client_key": self._key, "scope": TIKTOK_SCOPES, "response_type": "code",
            "redirect_uri": self._redirect, "state": state})

    async def complete_link(self, code: str, state: str) -> bool:
        if not state or not await self._cache.get(f"opssocial:tiktok:state:{state}"):
            return False
        await self._cache.delete(f"opssocial:tiktok:state:{state}")
        return await self._token_request({
            "client_key": self._key, "client_secret": self._secret,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": self._redirect})

    async def linked(self) -> bool:
        return bool(await self._cache.get(TIKTOK_TOKENS_KEY))

    async def unlink(self) -> None:
        await self._cache.delete(TIKTOK_TOKENS_KEY)

    async def _token_request(self, form: dict) -> bool:
        try:
            client = await self._http()
            resp = await client.post(f"{TIKTOK_API}/oauth/token/", data=form,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
            body = resp.json()
        except Exception as exc:
            log.warning("tiktok token request failed: %s", type(exc).__name__)
            return False
        if resp.status_code != 200 or not body.get("access_token"):
            log.warning("tiktok token request rejected: %s", body.get("error") or resp.status_code)
            return False
        now = int(time.time())
        await self._cache.set(TIKTOK_TOKENS_KEY, json.dumps({
            "access_token": body["access_token"],
            "expires_at": now + int(body.get("expires_in") or 86400),
            "refresh_token": body.get("refresh_token") or form.get("refresh_token"),
            "refresh_expires_at": now + int(body.get("refresh_expires_in") or 0),
            "open_id": body.get("open_id"),
        }), TOKENS_TTL)
        return True

    async def _access_token(self) -> str | None:
        raw = await self._cache.get(TIKTOK_TOKENS_KEY)
        if not raw:
            return None
        tokens = json.loads(raw)
        if tokens.get("expires_at", 0) - REFRESH_MARGIN_SECONDS > time.time():
            return tokens.get("access_token")
        if not tokens.get("refresh_token"):
            return None
        if not await self._token_request({
                "client_key": self._key, "client_secret": self._secret,
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}):
            return None
        return json.loads(await self._cache.get(TIKTOK_TOKENS_KEY) or "{}").get("access_token")

    # ── reading ──
    async def account(self) -> Account:
        if not self.configured:
            return Account("tiktok", note="not configured — set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET")
        if not await self.linked():
            return Account("tiktok", note="not linked — " + await self.begin_link())
        token = await self._access_token()
        if not token:
            return Account("tiktok", note="link expired — " + await self.begin_link())
        try:
            client = await self._http()
            headers = {"Authorization": f"Bearer {token}"}
            info = await client.post(
                f"{TIKTOK_API}/user/info/",
                params={"fields": "open_id,display_name,follower_count,likes_count,video_count"},
                headers=headers)
            if info.status_code != 200:
                return Account("tiktok", note=f"API error {info.status_code}")
            user = (info.json().get("data") or {}).get("user") or {}
            videos = await client.post(
                f"{TIKTOK_API}/video/list/",
                params={"fields": "id,title,create_time,view_count,like_count,comment_count,share_count,share_url"},
                headers=headers, json={"max_count": RECENT_POSTS})
            posts = []
            if videos.status_code == 200:
                for v in ((videos.json().get("data") or {}).get("videos") or [])[:RECENT_POSTS]:
                    posts.append(Post(
                        title=v.get("title") or "video", created_at=_int(v.get("create_time")),
                        views=_int(v.get("view_count")), likes=_int(v.get("like_count")),
                        comments=_int(v.get("comment_count")), shares=_int(v.get("share_count")),
                        url=v.get("share_url")))
            return Account("tiktok", handle=user.get("display_name"),
                           followers=_int(user.get("follower_count")),
                           posts=_int(user.get("video_count")),
                           total_likes=_int(user.get("likes_count")), recent=posts)
        except Exception as exc:
            log.warning("tiktok fetch failed: %s", type(exc).__name__)
            return Account("tiktok", note=f"unreachable ({type(exc).__name__})")


# ── Wiring ───────────────────────────────────────────────────────────────────

@dataclass
class Social:
    instagram: InstagramClient
    tiktok: TikTokClient

    @property
    def configured(self) -> bool:
        return self.instagram.configured or self.tiktok.configured

    async def accounts(self) -> list[Account]:
        return [await self.instagram.account(), await self.tiktok.account()]


def from_env(cache, client=None) -> Social:
    base = os.environ.get("SOCIAL_PUBLIC_BASE_URL", "https://api.snapworth.eu").rstrip("/")
    return Social(
        instagram=InstagramClient(
            os.environ.get("IG_USER_ID", "").strip(),
            os.environ.get("IG_ACCESS_TOKEN", "").strip(), client=client),
        tiktok=TikTokClient(
            os.environ.get("TIKTOK_CLIENT_KEY", "").strip(),
            os.environ.get("TIKTOK_CLIENT_SECRET", "").strip(),
            f"{base}/social/tiktok/callback", cache, client=client),
    )


# The TikTok redirect target. Unauthenticated by necessity — TikTok's servers
# call it — and harmless without a live state nonce the bot minted minutes ago.
_social: Social | None = None
router = APIRouter(prefix="/social", tags=["social"])


def configure(social: Social | None) -> None:
    global _social
    _social = social


@router.get("/tiktok/callback", response_class=HTMLResponse)
async def tiktok_callback(code: str = Query(default=""), state: str = Query(default=""),
                          error: str = Query(default="")):
    page = "<!doctype html><meta charset='utf-8'><title>SnapWorth</title>" \
           "<body style='font-family:-apple-system,sans-serif;max-width:32em;margin:15vh auto;text-align:center'>{}</body>"
    if error or not code or _social is None:
        return HTMLResponse(page.format("<h2>TikTok link failed</h2><p>Try again from the bot.</p>"),
                            status_code=400)
    if await _social.tiktok.complete_link(code, state):
        return HTMLResponse(page.format(
            "<h2>TikTok linked ✅</h2><p>You can close this tab. /social in Telegram now shows TikTok.</p>"))
    return HTMLResponse(page.format("<h2>TikTok link failed</h2><p>The link expired or was already used. "
                                    "Ask the bot for a fresh one.</p>"), status_code=400)
