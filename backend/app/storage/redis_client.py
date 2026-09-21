from __future__ import annotations

import redis

from backend.app.config.settings import Settings


class RedisUnavailableError(RuntimeError):
    pass


def connect_redis(settings: Settings) -> redis.Redis:
    try:
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            username=settings.redis_username,
            password=settings.redis_password,
            decode_responses=True,
            socket_connect_timeout=8,
            socket_timeout=8,
        )
        if client.ping() is not True:
            raise RedisUnavailableError("Redis ping did not return PONG")
        return client
    except RedisUnavailableError:
        raise
    except Exception as error:
        raise RedisUnavailableError(
            "Redis is unavailable. Check REDIS_HOST, REDIS_PORT, REDIS_USERNAME, "
            "and REDIS_PASSWORD on the server. Credentials are never sent to the iOS app."
        ) from error
