"""The operator's Telegram Bot API client.

Split out of notify.py (#230). Nothing here decides what to say: notify builds
the messages, owns the command menu and routes the commands; this sends,
edits, deletes, forwards and polls. What the bot keeps of the chat for 🧹 Clear
and 🗂 History is chatlog.py's.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import opsformat

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

TELEGRAM_API = "https://api.telegram.org"

SEND_TIMEOUT_SECONDS = 10.0

# Bot API long-poll. Telegram holds the request open until a message arrives
# or the timeout passes, so an idle loop costs one HTTP request per timeout.
POLL_TIMEOUT_SECONDS = 25

# Upper bound on deleteMessages calls per /clear, however the batches split.
DELETE_MAX_CALLS = 60
FORWARD_MAX_CALLS = 60


class TelegramNotifier:
    """Thin sendMessage client over the shared httpx stack."""

    def __init__(self, bot_token: str, chat_id: str, client=None) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        # Why the last forward batch was refused, for /clear to show.
        self.last_forward_refusal: str | None = None
        # The id Telegram redirected the archive to, if the chat moved.
        self.last_forward_migrated_to: str | None = None
        self._client = client          # injectable for tests
        # Consecutive-identical-failure tracking, so an outage is logged with
        # decreasing frequency rather than every ~2s. See `_note_failure`.
        self._last_failure: str = ""
        self._failure_streak: int = 0
        # Told the message_id of every message this notifier sends, so /clear
        # can take them back. Set by `configure`; None is "don't bother".
        self.on_sent: Callable[[int, str], Awaitable[None]] | None = None

    async def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS)
        return self._client

    async def send(self, text: str, buttons: opsformat.Buttons | None = None, *,
                   ask: str | None = None) -> bool:
        """Deliver one message. Returns success; never raises.

        `ask` turns the message into a question: Telegram opens the reply box
        on it with that placeholder, so a button can stand in for a command
        that needs typed input — the operator taps, types, sends.

        Failures log the exception *class* only: httpx error messages quote the
        request URL, and the URL carries the bot token.
        """
        try:
            client = await self._http()
            payload: dict = {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if ask is not None:
                payload["reply_markup"] = {"force_reply": True, "selective": True,
                                           "input_field_placeholder": ask[:64]}
            elif buttons:
                payload["reply_markup"] = {"inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons]}
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/sendMessage", json=payload)
            if resp.status_code != 200:
                # Telegram's own reason ("can't parse entities: …") names the
                # bug; the bare status code never did. It carries no token.
                log.warning("telegram send failed: HTTP %s %s",
                            resp.status_code, self._description(resp))
                return False
            if self.on_sent is not None:
                try:
                    message_id = ((resp.json() or {}).get("result") or {}).get("message_id")
                    if isinstance(message_id, int):
                        await self.on_sent(message_id, text)
                except Exception:
                    pass
            return True
        except Exception as exc:
            self._note_failure("send", type(exc).__name__)
            return False

    @staticmethod
    def _description(resp) -> str:
        try:
            return str((resp.json() or {}).get("description") or "")[:200]
        except Exception:
            return ""

    @staticmethod
    def _migrate_to(resp) -> str | None:
        """The id Telegram hands back when a chat has moved.

        Promoting a bot to administrator turns a basic group into a supergroup,
        and the supergroup gets a different id. Telegram says so in the error's
        `parameters.migrate_to_chat_id` rather than making anyone derive it —
        so read it instead of guessing a -100 prefix."""
        try:
            new = ((resp.json() or {}).get("parameters") or {}).get("migrate_to_chat_id")
            return str(new) if new is not None else None
        except Exception:
            return None

    @property
    def chat_id(self) -> str:
        return self._chat_id

    def _note_failure(self, what: str, detail: str) -> None:
        """Log a transport failure without flooding.

        `SamplingFilter` never samples WARNING and above — deliberately, since
        a dropped error is an incident you cannot investigate — so the poll
        loop's one WARNING every ~2s during a Telegram outage or a revoked
        token became ~43k identical lines a day, burying the signal it was
        supposed to be. Consecutive identical failures are now logged on the
        1st, 2nd, 4th, 8th... occurrence, so an outage is still visible and
        still timestamped at both ends, at a fraction of the volume.
        """
        key = f"{what}:{detail}"
        if key == self._last_failure:
            self._failure_streak += 1
        else:
            self._last_failure, self._failure_streak = key, 1
        streak = self._failure_streak
        if streak & (streak - 1) == 0:            # 1, 2, 4, 8, 16, ...
            suffix = f" (x{streak})" if streak > 1 else ""
            log.warning("telegram %s failed: %s%s", what, detail, suffix)

    def _note_success(self, what: str) -> None:
        if self._failure_streak:
            log.info("telegram %s recovered after %d failures", what, self._failure_streak)
        self._last_failure, self._failure_streak = "", 0

    async def get_updates(self, offset: int | None) -> list[dict]:
        """Long-poll for incoming messages. Returns [] on any failure."""
        params: dict = {"timeout": POLL_TIMEOUT_SECONDS,
                        "allowed_updates": '["message","callback_query"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            client = await self._http()
            resp = await client.get(
                f"{TELEGRAM_API}/bot{self._token}/getUpdates",
                params=params, timeout=POLL_TIMEOUT_SECONDS + 10)
            if resp.status_code != 200:
                self._note_failure("poll", f"HTTP {resp.status_code}")
                return []
            body = resp.json()
            self._note_success("poll")
            return list(body.get("result") or []) if body.get("ok") else []
        except Exception as exc:
            self._note_failure("poll", type(exc).__name__)
            return []

    async def delete_messages(self, message_ids: list[int]) -> int:
        """Delete the bot's own (and, in a private chat, the operator's)
        messages, up to 100 per call. Returns how many ids Telegram accepted.
        Messages older than 48 hours cannot be deleted by any bot — that is
        Telegram's rule, and the operator clears those from the chat menu."""
        # Telegram answers a batch as a whole: one id it will not delete — a
        # message past the 48-hour limit, say — refuses the entire call, and
        # the sweep below the newest known id spans days of them. So a refused
        # batch is split in half and retried, down to single ids, which
        # isolates the undeletable ones at a cost of O(k log n) calls instead
        # of one call per id. A hard cap keeps a pathological chat from
        # turning /clear into hundreds of requests.
        deleted = 0
        calls = 0
        try:
            client = await self._http()
            pending = [message_ids[i:i + 100] for i in range(0, len(message_ids), 100)]
            while pending and calls < DELETE_MAX_CALLS:
                chunk = pending.pop()
                calls += 1
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/deleteMessages",
                    json={"chat_id": self._chat_id, "message_ids": chunk})
                if resp.status_code == 200 and (resp.json() or {}).get("ok"):
                    deleted += len(chunk)
                elif len(chunk) > 1:
                    half = len(chunk) // 2
                    pending += [chunk[:half], chunk[half:]]
                else:
                    log.debug("telegram deleteMessages refused id %s: %s",
                              chunk[0], self._description(resp))
        except Exception as exc:
            log.warning("telegram deleteMessages failed: %s", type(exc).__name__)
        return deleted

    async def forward_messages(self, to_chat_id: str, message_ids: list[int]) -> int:
        """Copy messages to another chat — the archive — before /clear deletes
        them here. Returns how many were forwarded.

        Telegram answers a forward batch as a whole, exactly as it does a
        delete batch: one id it will not forward — a service message ("X
        created the group"), a message already gone, one whose content is
        protected — refuses the entire call, and the tracked list mixes the
        bot's own messages with everything it saw the operator send. Sending
        all of them in one call therefore archives *nothing* the moment a
        single id is unforwardable, which is how a healthy chat reports
        "0 forwarded". So a refused batch is halved and retried down to single
        ids, isolating the unforwardable ones, under the same call cap as
        delete."""
        forwarded = 0
        calls = 0
        self.last_forward_refusal = None
        self.last_forward_migrated_to = None
        try:
            client = await self._http()
            # Strictly increasing ids are required by forwardMessages, and
            # halving a sorted list keeps every chunk sorted.
            ordered = sorted(set(message_ids))
            pending = [ordered[i:i + 100] for i in range(0, len(ordered), 100)]
            while pending and calls < FORWARD_MAX_CALLS:
                chunk = pending.pop()
                calls += 1
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/forwardMessages",
                    json={"chat_id": to_chat_id, "from_chat_id": self._chat_id,
                          "message_ids": chunk, "disable_notification": True})
                if resp.status_code == 200 and (resp.json() or {}).get("ok"):
                    forwarded += len((resp.json() or {}).get("result") or chunk)
                    continue

                # The chat moved: follow it once, put the batch back, and
                # remember the new id so /clear can name it. Archiving to a
                # chat that has merely been upgraded should not need a redeploy
                # to succeed — only to stop needing this hop.
                moved = self._migrate_to(resp)
                if moved and moved != to_chat_id and self.last_forward_migrated_to is None:
                    log.info("archive chat %s migrated to %s", to_chat_id, moved)
                    self.last_forward_migrated_to = moved
                    to_chat_id = moved
                    pending.append(chunk)
                    continue

                if len(chunk) > 1:
                    half = len(chunk) // 2
                    pending += [chunk[:half], chunk[half:]]
                else:
                    # The last one standing explains the whole batch: keep it
                    # for /clear to show, so "0 forwarded" is never mute.
                    self.last_forward_refusal = self._description(resp)
                    log.info("telegram forwardMessages refused id %s: %s",
                             chunk[0], self.last_forward_refusal)
        except Exception as exc:
            self.last_forward_refusal = type(exc).__name__
            log.warning("telegram forwardMessages failed: %s", type(exc).__name__)
        return forwarded

    async def get_chat(self, chat_id: str) -> dict | None:
        """What Telegram knows about a chat id — title and type — or None if
        the bot cannot see it. Used to verify the archive chat."""
        try:
            client = await self._http()
            resp = await client.get(f"{TELEGRAM_API}/bot{self._token}/getChat",
                                    params={"chat_id": chat_id})
            if resp.status_code != 200:
                return None
            return (resp.json() or {}).get("result") or None
        except Exception as exc:
            log.debug("telegram getChat failed: %s", type(exc).__name__)
            return None

    async def download_photo(self, file_id: str, max_bytes: int = 10 * 1024 * 1024) -> bytes | None:
        """Fetch a photo the operator sent, via getFile. None on any failure."""
        try:
            client = await self._http()
            meta = await client.get(f"{TELEGRAM_API}/bot{self._token}/getFile",
                                    params={"file_id": file_id})
            path = ((meta.json() or {}).get("result") or {}).get("file_path") if meta.status_code == 200 else None
            if not path:
                log.warning("telegram getFile failed: HTTP %s %s", meta.status_code, self._description(meta))
                return None
            resp = await client.get(f"{TELEGRAM_API}/file/bot{self._token}/{path}",
                                    timeout=SEND_TIMEOUT_SECONDS * 3)
            if resp.status_code != 200 or len(resp.content) > max_bytes:
                log.warning("telegram file download failed: HTTP %s, %d bytes",
                            resp.status_code, len(resp.content))
                return None
            return resp.content
        except Exception as exc:
            log.warning("telegram file download failed: %s", type(exc).__name__)
            return None

    async def answer_callback(self, callback_id: str) -> None:
        """Stop the button's spinner. Best-effort; the reply is sent regardless."""
        try:
            client = await self._http()
            await client.post(
                f"{TELEGRAM_API}/bot{self._token}/answerCallbackQuery",
                json={"callback_query_id": callback_id})
        except Exception as exc:
            log.debug("telegram answerCallbackQuery failed: %s", type(exc).__name__)

    async def clear_default_commands(self) -> bool:
        """Withdraw any command menu published at the default scope.

        A chat-scoped list does not replace a default-scoped one, so publishing
        the operator menu to the right chat is not enough on its own — what is
        already live has to be taken down.
        """
        try:
            client = await self._http()
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/deleteMyCommands",
                json={"scope": {"type": "default"}})
            return resp.status_code == 200
        except Exception as exc:
            log.warning("telegram deleteMyCommands failed: %s",
                        type(exc).__name__)
            return False

    async def set_commands(self, commands: tuple[tuple[str, str], ...]) -> bool:
        """Publish the command menu Telegram shows behind the "/" button.

        Scoped to the operator's chat. Omitting `scope` defaults it to
        `BotCommandScopeDefault`, which covers every private chat, group and
        supergroup — so all 23 entries were what any Telegram user saw behind
        the Menu button on opening the bot, descriptions included: "lever —
        Arm or disarm the free-scan allowance without a redeploy", "subs —
        Every subscription seen: plan, how obtained, renews", "costs — Gemini
        spend: today, 7 and 30 days, per scan, vs MRR".

        No access leaked — the chat gate drops every update from another
        chat — but the shape of the operation did, along with an invitation to
        try. A published menu is documentation.
        """
        try:
            client = await self._http()
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/setMyCommands",
                json={"commands": [{"command": c, "description": d}
                                   for c, d in commands],
                      "scope": {"type": "chat", "chat_id": self._chat_id}})
            return resp.status_code == 200
        except Exception as exc:
            log.warning("telegram setMyCommands failed: %s", type(exc).__name__)
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.aclose()
            except Exception:
                pass
