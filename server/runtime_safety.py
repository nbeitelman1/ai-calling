"""Bounded, provider-independent async supervision helpers."""
from __future__ import annotations

import asyncio
import math
import os
from loguru import logger


def timeout_setting(name: str, default: float, *, maximum: float = 120.0) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f'{name} must be finite and in (0, {maximum}]')
    return value


async def wait_pipeline_started(started: asyncio.Event, runner_task,
                                disconnect_task, *, timeout: float = 15.0):
    """Wait for the native StartFrame event, NOT an audio-dependent flush.

    StartFrame is necessary but not sufficient for healthy data flow; the
    regression suite separately proves data frames traverse every processor.
    """
    ready_task = asyncio.create_task(started.wait(), name='media-start-gate')
    try:
        done, _ = await asyncio.wait(
            {ready_task, runner_task, disconnect_task},
            timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        # A disconnect or worker termination wins over simultaneous readiness.
        if disconnect_task in done:
            disconnect_task.result()
            raise ConnectionError('media disconnected during pipeline startup')
        if runner_task in done:
            if runner_task.cancelled():
                raise RuntimeError('pipeline cancelled during startup')
            runner_task.result()
            raise RuntimeError('pipeline ended during startup')
        if ready_task not in done:
            raise TimeoutError('pipeline did not start before absolute deadline')
    finally:
        ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)


def _consume_result(task):
    if not task.cancelled():
        # Do not log exception repr: providers may include credentials/text.
        exc = task.exception()
        if exc is not None:
            logger.warning('background cleanup result type={}', type(exc).__name__)


async def cancel_tasks_bounded(tasks, *, timeout: float = 3.0) -> int:
    """Cancel owned tasks and bound waiting even if a child ignores cancellation.

    asyncio.wait_for(gather(...)) is NOT a hard deadline because wait_for
    waits for cancellation acknowledgement. Retain callbacks on any stragglers
    to consume their result. A nonzero return requires operator visibility;
    Python cannot forcibly kill an uncooperative coroutine within a process.
    """
    owned = {task for task in tasks if task is not None}
    for task in owned:
        if not task.done():
            task.cancel()
    if not owned:
        return 0
    done, pending = await asyncio.wait(owned, timeout=timeout)
    for task in done:
        _consume_result(task)
    for task in pending:
        task.add_done_callback(_consume_result)
    if pending:
        logger.error('media cleanup deadline exceeded; pending_tasks={}', len(pending))
    return len(pending)
