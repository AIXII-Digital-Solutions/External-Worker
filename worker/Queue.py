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
# Dedicated queue for the external Cirium scraper robot (a separate service/repo). The
# dispatcher enqueues scrape_cirium here; this worker never consumes it (its own queue_name
# is EXTERNAL_QUEUE), so only the robot's ARQ worker picks it up.
ROBOT_QUEUE = "core:robot"


def get_redis_settings() -> RedisSettings:
    """Build ARQ RedisSettings from the shared DBSettings (raw, non-url-quoted password)."""
    s = DBSettings()
    return RedisSettings(
        host=s.REDIS_HOST,
        port=int(s.REDIS_PORT),
        username=s.REDIS_USER or None,
        password=s.REDIS_USER_PASSWORD or None,
    )


__all__ = ["FILE_QUEUE", "EXTERNAL_QUEUE", "ROBOT_QUEUE", "get_redis_settings"]
