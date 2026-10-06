"""What the bot keeps of the operator's chat, so 🧹 Clear can take it back and
🗂 History can show it.

Split out of notify.py (#230), beside telegram.py, which sends what this
deletes and forwards. Message ids and the bot's own texts live in the cache
each call is handed; notify supplies its menu and /status text for the
replies. This module holds no state of its own.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import opsformat

if TYPE_CHECKING:
    from cache import ResilientCache
    from telegram import TelegramNotifier

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

# Every message id in the operator's chat — the bot's and the operator's — so
# 🧹 Clear can delete them. Telegram refuses anything older than 48 hours, so
# the list is pruned to that and capped; a longer memory would buy nothing.
MESSAGES_KEY = "opsstate:tgmsgs"
MESSAGES_CAP = 400
MESSAGES_TTL = 48 * 3600
# What 🧹 Clear says when the list is empty. The list is in the cache, not the
# process, so it survives restarts — "since this process started" was wrong
# both ways. It is empty when nothing was tracked in 48 hours, or, for the
# prompt, when the read failed, which `tracked_messages` cannot tell apart
# from that (`clear_chat` reads `required` and refuses instead); and never
# right after a clear, whose own confirmation is tracked.
CLEAR_NOTHING_TRACKED = ("🧹 Nothing to clear — the bot has no record of a message "
                         "in this chat from the last 48 hours.")

# What 🧹 Clear removed, kept so it is not lost: the text of the bot's own
# messages (the operator's are one-word commands and are not worth keeping),
# shown back by /history. A month, a few hundred entries.
ARCHIVE_KEY = "opsstate:tgarchive"
ARCHIVE_CAP = 300
ARCHIVE_TTL = 30 * 24 * 3600
HISTORY_DEFAULT = 8
HISTORY_MAX = 25
HISTORY_SNIPPET_CHARS = 220
HISTORY_SNIPPET_LINES = 4
# Optionally, a second chat — a private channel with the bot as admin — that
# /clear forwards everything to before deleting, so the copy is a real
# Telegram copy, photos included. Off when unset.
ARCHIVE_CHAT_ENV = "TELEGRAM_ARCHIVE_CHAT_ID"
# Message ids in a private chat are sequential, so /clear also sweeps the gaps
# between the oldest and newest ids it knows — at most this many ids. Those are
# messages inside the span being cleared that the bot lost track of (the
# tracked list is an unlocked read-modify-write, so two sends at once can drop
# one). Telegram skips ids it cannot delete. It used to sweep 600 ids below the
# newest whatever the tracked span, reaching past it into messages the bot had
# no copy of and had never been asked to clear.
CLEAR_SWEEP_IDS = 600


# ── Clear: take back the last two days of the chat ──────────────────────────

async def remember_message(cache: ResilientCache, message_id: int,
                           text: str | None = None) -> None:
    """Note a message in the chat: its id (so /clear can delete it) and, for
    the bot's own messages, its text (so /clear can keep a copy)."""
    try:
        now = int(time.time())
        # `required`, so an unreadable list raises into the `except` below and
        # is left alone — read as empty, it was overwritten with one entry
        # and /clear lost every id and archived text before it.
        raw = await cache.get(MESSAGES_KEY, required=True)
        entries = [e for e in (json.loads(raw) if raw else [])
                   if isinstance(e, list) and len(e) >= 2 and now - int(e[1]) < MESSAGES_TTL]
        entry: list[int | str] = [int(message_id), now]
        if text:
            entry.append(text[:4096])
        entries.append(entry)
        await cache.set(MESSAGES_KEY, json.dumps(entries[-MESSAGES_CAP:]), MESSAGES_TTL)
    except Exception as exc:
        log.debug("message id note failed: %s", type(exc).__name__)


async def archive(cache: ResilientCache, entries: list[list]) -> int | None:
    """Keep the text of the bot's messages that are about to be deleted.

    The number kept, or None when they could not be kept — which `/clear`
    must not take as "nothing to keep" and delete them anyway."""
    texts = [[int(e[1]), e[2]] for e in entries if len(e) >= 3 and e[2]]
    if not texts:
        return 0
    try:
        # `required` for the reason `remember_message` gives: an archive read
        # as empty is an archive about to be replaced by this one batch.
        raw = await cache.get(ARCHIVE_KEY, required=True)
        kept = [a for a in (json.loads(raw) if raw else []) if isinstance(a, list) and len(a) == 2]
        kept += texts
        await cache.set(ARCHIVE_KEY, json.dumps(kept[-ARCHIVE_CAP:]), ARCHIVE_TTL)
    except Exception as exc:
        log.debug("archive write failed: %s", type(exc).__name__)
        return None
    return len(texts)


async def tracked_messages(cache: ResilientCache) -> list[list]:
    """The chat's tracked messages: `[id, when]`, plus the text for the bot's."""
    try:
        raw = await cache.get(MESSAGES_KEY)
        return [e for e in (json.loads(raw) if raw else []) if isinstance(e, list) and e]
    except Exception:
        return []


async def clear_prompt(cache: ResilientCache) -> tuple[str, opsformat.Buttons, bool]:
    """What 🧹 Clear is about to delete and what survives it, before it does.

    The button runs on one tap no longer. It sits beside 🗂 History on every
    keyboard, and it takes up to two days of alerts and /sub answers with it —
    and what it keeps is less than "everything": the text of the bot's own
    messages, of which /history shows the first lines, and nothing of the
    operator's or of any photo unless an archive chat is configured.

    Returns the text, its own buttons, and whether notify should add its menu
    under them: yes when there is nothing to clear, no under the question."""
    known = {int(e[0]) for e in await tracked_messages(cache)}
    if not known:
        return (CLEAR_NOTHING_TRACKED, [], True)
    if os.environ.get(ARCHIVE_CHAT_ENV, "").strip():
        kept = ("Kept: everything tracked is forwarded to the archive chat first, "
                "photos included, and the text of the bot's own messages stays in "
                "/history for 30 days.")
    else:
        kept = ("Kept: the text of the bot's own messages, for 30 days; /history "
                "shows the first lines of each. Your messages and photos are not kept.")
    return ("🧹 <b>Clear the chat?</b>\n"
            f"Deletes the {len(known)} message{'s' if len(known) != 1 else ''} the bot "
            "tracked here in the last 48 hours — yours and its own — and any it "
            "lost track of between them.\n" + kept,
            [[("🧹 Yes, clear it", "clear yes"), ("Cancel", "status")]], False)


CLEAR_REFUSED = ("🧹 Nothing was cleared: the bot's messages could not be copied "
                  "to 🗂 History first, and deleting them uncopied would lose them. "
                  "Try again in a minute.")


async def clear_chat(notifier: TelegramNotifier, cache: ResilientCache,
                     menu: Callable[[], Awaitable[opsformat.Buttons]],
                     status_text: Callable[[], Awaitable[str]]) -> None:
    """Delete every message the bot remembers in this chat, then post a fresh
    status so the keyboard is still there. Only the last 48 hours can go —
    Telegram's limit for bots, not ours — and only what was tracked.

    What survives is the text of the bot's own messages, archived for /history
    first, and, when TELEGRAM_ARCHIVE_CHAT_ID names a second chat, everything
    tracked, forwarded there — a real Telegram copy, photos included. The
    operator's own messages and photos are not otherwise kept; `clear_prompt`
    says so before this runs.

    `menu` and `status_text` are notify's keyboard and /status, which the
    replies end with."""
    try:
        # `required`: read as empty, an unreadable list was deleted below
        # without anything in it being archived. `tracked_messages` reads
        # leniently, which is right for the prompt and wrong here.
        raw = await cache.get(MESSAGES_KEY, required=True)
    except Exception as exc:
        log.warning("message list unreadable, not clearing: %s", type(exc).__name__)
        await notifier.send(CLEAR_REFUSED, await menu())
        return
    try:
        entries = [e for e in (json.loads(raw) if raw else []) if isinstance(e, list) and e]
    except Exception:
        entries = []
    known = sorted({int(e[0]) for e in entries})
    archived = await archive(cache, entries)
    if archived is None:
        # Deleting now would lose exactly the texts the archive exists to keep.
        await notifier.send(CLEAR_REFUSED, await menu())
        return
    archive_chat = os.environ.get(ARCHIVE_CHAT_ENV, "").strip()
    forwarded = await notifier.forward_messages(archive_chat, known) if archive_chat and known else 0
    # Known ids first, then the gaps between them: private-chat ids are
    # sequential, and Telegram silently skips what it cannot delete. Only
    # *between* the oldest and newest known — see CLEAR_SWEEP_IDS.
    sweep: list[int] = []
    if known:
        oldest, newest = known[0], known[-1]
        tracked = set(known)
        sweep = [i for i in range(max(oldest, newest - CLEAR_SWEEP_IDS), newest + 1)
                 if i not in tracked]
    deleted = await notifier.delete_messages(known) if known else 0
    try:
        await cache.delete(MESSAGES_KEY)
    except Exception:
        pass
    swept = await notifier.delete_messages(sweep) if sweep else 0
    if known:
        # Report the tracked deletions, which are a real count, and describe
        # the sweep as a sweep. These used to be added together and presented
        # as "Cleared N" — but Telegram answers `deleteMessages` with ok for
        # ids it silently skips (a message that never existed, or one past the
        # 48-hour limit), so every id in the blind sweep counted as a deletion
        # and N was really just the sweep's range size. It read as a precise
        # figure and was not one.
        note = f"🧹 Cleared {deleted} tracked message{'s' if deleted != 1 else ''}."
        if swept:
            note += (f" Also swept {swept} untracked id"
                     f"{'s' if swept != 1 else ''} between them — Telegram "
                     "does not say how many of those existed.")
        if archived:
            note += f" {archived} of the bot's kept — 🗂 History shows them."
        if archive_chat:
            note += f" {forwarded} forwarded to the archive chat."
            moved = getattr(notifier, "last_forward_migrated_to", None)
            if moved:
                note += (f"\n⚠️ That chat is now a supergroup — set "
                         f"{ARCHIVE_CHAT_ENV} to <code>{html.escape(str(moved))}</code>; "
                         "this run followed the move.")
            elif not forwarded:
                why = getattr(notifier, "last_forward_refusal", None)
                note += (f" (Telegram refused: {html.escape(str(why))})" if why else
                         " (nothing in the tracked list could be forwarded)")
    else:
        note = CLEAR_NOTHING_TRACKED
    note += ("\nTelegram lets a bot delete only the last 48 hours; anything older is "
             "chat menu → Clear History.")
    await notifier.send(note + "\n\n" + await status_text(), await menu())


def snippet(text: str, limit: int = HISTORY_SNIPPET_CHARS) -> str:
    """A message as it was sent, shortened. The text is the bot's own Telegram
    HTML, so tags are stripped rather than re-escaped — cutting mid-tag would
    make the whole history message unparseable."""
    plain = re.sub(r"<[^>]+>", "", text)
    plain = html.unescape(plain)
    lines = [line for line in plain.split("\n") if line.strip()]
    clipped = len(lines) > HISTORY_SNIPPET_LINES
    plain = "\n".join(lines[:HISTORY_SNIPPET_LINES])
    if len(plain) > limit:
        plain = plain[:limit].rstrip()
        clipped = True
    return html.escape(plain) + ("…" if clipped else "")


async def history_text(cache: ResilientCache, argument: str) -> str:
    try:
        count = min(HISTORY_MAX, max(1, int(argument))) if argument else HISTORY_DEFAULT
    except ValueError:
        count = HISTORY_DEFAULT
    try:
        raw = await cache.get(ARCHIVE_KEY)
        entries = [a for a in (json.loads(raw) if raw else []) if isinstance(a, list) and len(a) == 2]
    except Exception:
        entries = []
    if not entries:
        return ("🗂 <b>History</b>\nNothing archived yet. 🧹 Clear keeps a copy of what the bot "
                "said; it shows up here.")
    shown = entries[-count:]
    lines = [f"🗂 <b>History</b> — last {len(shown)} of {len(entries)} kept messages"]
    budget = 3800
    blocks = []
    for at, text in reversed(shown):
        when = datetime.fromtimestamp(int(at), timezone.utc).strftime("%d %b %H:%M")
        block = f"<b>{when} UTC</b>\n{snippet(str(text))}"
        if budget - len(block) < 0:
            blocks.append("…")
            break
        budget -= len(block)
        blocks.append(block)
    lines.append("\n\n".join(blocks))
    lines.append("/history 25 for more. Kept 30 days.")
    return "\n\n".join(lines)

