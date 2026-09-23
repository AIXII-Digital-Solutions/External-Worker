import functools
import inspect
import json
import time
from functools import wraps
from typing import Any, Callable, Optional, Coroutine

from fastapi import Request
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from Config import setup_logger, DBSettings, ENABLE_PERFORMANCE_LOGGER
from Utils import JobMetrics

logger = setup_logger(
    'fastapi_app',
    log_format='%(levelname)s:     [%(name)s] %(asctime)s | %(message)s'
)

perf_dec_logger = setup_logger(
    "performance",
    log_format="%(levelname)s:     [%(name)s] %(asctime)s | %(message)s"
)

engine_cache = {}
session_cache = {}


class DBProxy:
    """
    DBProxy wraps the database and Redis interface:
    - get_or_cache: retrieves an object from Redis, or if not, from the database, and stores it in Redis
    - update_and_cache: updates the object in the database and directly in Redis
    """

    def __init__(self, redis: Redis):
        self._open_sessions = []
        self.db_settings = DBSettings()
        self.redis = redis

    async def get_db(self, db_name: str):
        if db_name not in session_cache:
            url = self.db_settings.get_db_url(db_name)
            engine = create_async_engine(url,
                                         future=True,
                                         echo=False,
                                         pool_pre_ping=True,
                                         pool_recycle=300,
                                         pool_size=10,
                                         max_overflow=20,
                                         )
            engine_cache[db_name] = engine
            session_cache[db_name] = async_sessionmaker(
                bind=engine, class_=AsyncSession, expire_on_commit=False
            )

        session = session_cache[db_name]()
        self._open_sessions.append(session)
        return session

    async def close_all(self):
        for session in self._open_sessions:
            await session.close()
        self._open_sessions.clear()

    # -----------------------------
    # Redis utils
    # -----------------------------
    async def redis_set(self, key: str, value: Any, ttl: Optional[int] = None):
        data = json.dumps(value)
        if ttl:
            await self.redis.setex(key, ttl, data)
        else:
            await self.redis.set(key, data)
        logger.debug(f"Redis SET {key} -> {value}")

    async def redis_get(self, key: str) -> Optional[Any]:
        data = await self.redis.get(key)
        logger.debug(f"Redis GET {key} -> {data}")
        return json.loads(data) if data else None

    async def redis_delete(self, key: str):
        deleted = await self.redis.delete(key)
        logger.debug(f"Redis DEL {key} -> deleted={deleted}")

    async def redis_expire(self, key: str, ttl: int):
        await self.redis.expire(key, ttl)
        logger.debug(f"Redis EXPIRE {key} -> {ttl}s")

    # -----------------------------
    # DB + Cache logic
    # -----------------------------
    async def get_or_cache(self, key: str, db_name: str, query_func: Callable[[AsyncSession], Coroutine[Any, Any, Any]],
                           ttl: int = 60) -> Optional[Any]:

        cached = await self.redis_get(key)
        if cached:
            logger.debug(f"{key} returned from cache")
            return cached

        session = await self.get_db(db_name)
        result = await query_func(session)
        if result:
            if isinstance(result, BaseModel):
                _result = result.model_dump(mode="json")
            elif isinstance(result, list):
                _result = [
                    item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                    for item in result
                ]
            else:
                _result = result
            await self.redis_set(key, _result, ttl)
            logger.debug(f"{key} returned from database")

        return result

    async def update_and_cache(self, key: str, db_name: str,
                               update_func: Callable[[AsyncSession], Coroutine[Any, Any, Any]],
                               ttl: int = 60, related_pattern: Optional[str] = None) -> Any:

        session = await self.get_db(db_name)
        result = await update_func(session)
        await session.commit()

        await self.redis_delete(key)
        logger.debug(f"Redis DEL {key} (main key)")

        if related_pattern:
            async for rk in self.redis.scan_iter(match=related_pattern):
                await self.redis_delete(rk)
                logger.debug(f"Redis DEL {rk} (related key via pattern {related_pattern})")

        if result:
            if isinstance(result, BaseModel):
                _result = result.model_dump(mode="json")
            elif isinstance(result, list):
                _result = [
                    item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                    for item in result
                ]
            else:
                _result = result
            await self.redis_set(key, _result, ttl)
            logger.debug(f"{key} updated in database and cache")

        return result


# -----------------------------
# Decorator for simplification
# -----------------------------
def cache_query(key_template: str, ttl: int = 60, update: bool = False, related_pattern: Optional[str] = None):
    """
    Wrapper for methods in endpoints:
    - If update=False → get_or_cache
    - If update=True → update_and_cache

    Example:
    @cache_query("registration:{reg}", ttl=120)
    async def get_registration(session, reg): ...
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs.get("request")
            if not request:
                raise ValueError("Request must be passed to the endpoint")

            key = key_template.format(**kwargs)
            db: DBProxy = request.app.state.db_proxy
            db_name = kwargs.get("db_name")

            _related_pattern = related_pattern.format(**kwargs) if related_pattern else None

            if update:
                return await db.update_and_cache(key, db_name, lambda session: func(session, *args, **kwargs), ttl,
                                                 related_pattern=_related_pattern)
            else:
                return await db.get_or_cache(key, db_name, lambda session: func(session, *args, **kwargs), ttl)

        return wrapper

    return decorator


def _performance_log(seconds: float, name, cost=None):
    """How long, AND how much of it was waiting on the database.

    `db=<statements>/<seconds in them>` is the number that explains the rest: this worker is a
    network away from its database, so a slow job is usually one that asked too many times, and
    that is invisible from the clock alone. A job slow enough to warn also gets its slowest
    statement named, once — enough to recognise the query without logging every query of every job.
    """
    if not ENABLE_PERFORMANCE_LOGGER:
        return
    db = f" | {cost.summary()}" if cost is not None else ""
    if seconds < 60:
        perf_dec_logger.info(f"{name} completed in {seconds:.2f} seconds{db}")
        return
    level = perf_dec_logger.warning if seconds < 600 else perf_dec_logger.critical
    suffix = "Improve performance" if seconds < 600 else "Improve performance!!"
    level(f"{name} completed in {seconds:.2f} seconds{db}. {suffix}")
    if cost is not None and cost.slowest_statement:
        level(f"  slowest statement of {name}: {cost.slowest_seconds * 1000:.0f}ms  "
              f"{cost.slowest_statement}")


def performance_timer(func):
    """
    A decorator for measuring function execution time.
    Supports both regular and async functions.
    """
    if inspect.iscoroutinefunction(func):
        # --- ASYNC  ---
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cost = JobMetrics.begin()
            start = time.perf_counter()
            result = await func(*args, **kwargs)
            end = time.perf_counter()

            elapsed = end - start
            _performance_log(elapsed, func.__name__, cost)

            return result

        return wrapper

    else:
        # --- SYNC  ---
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Synchronous, so it does no database work of its own through the async engine —
            # timed, but with nothing to count.
            start = time.perf_counter()
            result = func(*args, **kwargs)
            end = time.perf_counter()

            elapsed = end - start
            _performance_log(elapsed, func.__name__)

            return result

        return wrapper


__all__ = ["cache_query", "DBProxy", "performance_timer"]
