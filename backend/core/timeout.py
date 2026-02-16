"""
Robust async timeout utility.

Uses asyncio.wait instead of asyncio.wait_for to avoid hanging
when the target coroutine doesn't respond to cancellation
(e.g., supabase-py async client).
"""
import asyncio
import logging
from typing import TypeVar, Optional, Coroutine, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def safe_timeout(
    coro: Coroutine[Any, Any, T],
    timeout: float,
    default: Optional[T] = None,
    label: str = "operation"
) -> Optional[T]:
    """
    Run a coroutine with a hard timeout that never hangs.
    
    Unlike asyncio.wait_for, this does NOT wait for the task to
    acknowledge cancellation. If the task doesn't finish in time,
    it is abandoned (cancel sent but not awaited).
    
    Args:
        coro: The coroutine to run
        timeout: Timeout in seconds
        default: Value to return on timeout
        label: Label for logging
    
    Returns:
        The coroutine result, or default on timeout/error
    """
    task = asyncio.create_task(coro)
    try:
        done, pending = await asyncio.wait({task}, timeout=timeout)
        if done:
            # Task completed - check for exceptions
            exc = task.exception()
            if exc:
                logger.warning(f"{label} failed: {exc}")
                return default
            return task.result()
        else:
            # Timeout - abandon task
            task.cancel()
            logger.warning(f"{label} timed out after {timeout}s")
            return default
    except Exception as e:
        task.cancel()
        logger.warning(f"{label} error: {e}")
        return default
