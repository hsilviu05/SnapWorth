"""Fire-and-forget work that must not hold up a request.

One set of tasks for every module that spawns them (notify, opsstats, trends),
so `notify.aclose` cancels all of them at shutdown and a test can wait for all
of them in one place.
"""

from __future__ import annotations

import asyncio

tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task | None:
    """Run `coro` in the background, holding a reference until it finishes.

    Without the reference set, an un-awaited task is garbage-collectable
    mid-flight. Outside a running loop (sync tests, tooling) the coroutine is
    closed unrun rather than raising, and None is returned.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    task = loop.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task
