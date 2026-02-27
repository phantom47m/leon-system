"""
safe_tasks — fire-and-forget asyncio.create_task() with exception logging.

Bare ``asyncio.create_task()`` silently swallows exceptions when the returned
Task is never awaited.  ``create_safe_task()`` attaches a done-callback that
logs any unhandled exception so background failures are always visible.

Usage::

    from core.safe_tasks import create_safe_task

    create_safe_task(some_coroutine(), name="memory-extract")
"""

import asyncio
import logging
from typing import Any, Coroutine, Optional

logger = logging.getLogger("leon")


def _task_done_callback(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        task_name = task.get_name() if hasattr(task, "get_name") else "unnamed"
        logger.error(
            "Background task '%s' failed with %s: %s",
            task_name,
            type(exc).__name__,
            exc,
            exc_info=exc,
        )


def create_safe_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: Optional[str] = None,
) -> asyncio.Task:
    """Create an asyncio task with automatic exception logging.

    Drop-in replacement for ``asyncio.create_task()`` that ensures
    any unhandled exception is logged instead of silently swallowed.

    Parameters
    ----------
    coro:
        The coroutine to schedule.
    name:
        Optional human-readable name for the task (shown in log messages).

    Returns
    -------
    asyncio.Task
        The scheduled task (can still be awaited or cancelled if needed).
    """
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_task_done_callback)
    return task
