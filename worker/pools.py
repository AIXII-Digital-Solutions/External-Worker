"""
Optional process pool for CPU-bound work in external-worker.

external-worker is almost entirely IO-bound (HTTP + asyncpg), so its real concurrency
levers are the async fan-out ``MAX_JOBS`` and running more worker replicas — NOT a process
pool. This helper exists for the occasional CPU-bound task (heavy parsing / number
crunching): wrap it in ``await run_cpu(fn, *picklable_args)`` to run it on a separate core
instead of blocking the event loop (and every other concurrent job on it).

Toggle with ``USE_PROCESS_POOL`` (default true -> process pool; false -> thread fallback).
Size with ``PROCESS_WORKERS`` (default: CPU count). Submitted callables must be importable
top-level functions whose args are picklable (never live DB sessions/engines).
"""
import asyncio
import os
from concurrent.futures import ProcessPoolExecutor

_pool: ProcessPoolExecutor | None = None

USE_PROCESS_POOL: bool = os.getenv("USE_PROCESS_POOL", "true").lower() in ("1", "true", "yes", "on")


def _max_workers() -> int:
    try:
        configured = int(os.getenv("PROCESS_WORKERS", "0"))
    except ValueError:
        configured = 0
    return max(1, configured or (os.cpu_count() or 2))


def _init_worker() -> None:
    """Runs once in every pool worker. Under fork a child inherits the parent's loggers in
    memory; strip inherited file handlers so children never write/rotate the shared log file
    (they log to console, captured by the container)."""
    import logging
    names = ["", *list(logging.root.manager.loggerDict)]
    for name in names:
        lg = logging.getLogger(name)
        for h in list(getattr(lg, "handlers", [])):
            if isinstance(h, logging.FileHandler):
                try:
                    lg.removeHandler(h)
                except Exception:
                    pass


def get_process_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=_max_workers(), initializer=_init_worker)
    return _pool


async def run_cpu(func, *args):
    """Run a picklable CPU-bound function off the event loop and await its result."""
    if not USE_PROCESS_POOL:
        return await asyncio.to_thread(func, *args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_process_pool(), func, *args)


def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _pool = None


__all__ = ["run_cpu", "get_process_pool", "shutdown_pool", "USE_PROCESS_POOL"]
