from __future__ import annotations

from uuid import uuid4

from backend.app.config.settings import Settings
from backend.app.storage.objects import S3ObjectStore, object_key
from backend.app.storage.redis_client import connect_redis


def test_live_redis_and_r2_round_trip() -> None:
    settings = Settings.from_environment()
    redis_client = connect_redis(settings)
    store = S3ObjectStore(settings)
    store.ping()
    probe = f"stage2-probe/{uuid4().hex}"
    redis_key = f"ls:probe:{uuid4().hex}"
    try:
        redis_client.set(redis_key, "ok")
        assert redis_client.get(redis_key) == "ok"
        key = object_key(uuid4(), "probe.txt")
        store.put(key, b"library-survey-stage2", "text/plain")
        assert store.get(key) == b"library-survey-stage2"
        store.delete(key)
        assert not store.exists(key)
    finally:
        redis_client.delete(redis_key)
        redis_client.delete(probe)
