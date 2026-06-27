"""
Shared ARQ (Redis-based message broker) configuration used by all segments.

- API Server enqueues jobs into the queues below.
- file_processor and external_worker consume them.

Queue names are kept explicit so each worker listens only on its own queue.
"""
from arq.connections import RedisSettings

from Config import DBSettings

# Queue names (one per worker segment)
FILE_QUEUE = "core:files"
EXTERNAL_QUEUE = "core:external"


def get_redis_settings() -> RedisSettings:
    """Build ARQ RedisSettings from the shared DBSettings (raw, non-url-quoted password)."""
    s = DBSettings()
    return RedisSettings(
        host=s.REDIS_HOST,
        port=int(s.REDIS_PORT),
        username=s.REDIS_USER or None,
        password=s.REDIS_USER_PASSWORD or None,
    )


__all__ = ["FILE_QUEUE", "EXTERNAL_QUEUE", "get_redis_settings"]
