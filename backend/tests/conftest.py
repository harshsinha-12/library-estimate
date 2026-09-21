from __future__ import annotations

from uuid import uuid4

import fakeredis

from backend.app.main import create_app
from backend.app.storage.objects import MemoryObjectStore


def isolated_app(key_prefix: str | None = None, redis_client=None, object_store=None):
    client = redis_client or fakeredis.FakeRedis(decode_responses=True)
    store = object_store or MemoryObjectStore()
    prefix = key_prefix or f"ls:test:{uuid4().hex[:8]}"
    app = create_app(
        redis_client=client,
        object_store=store,
        key_prefix=prefix,
    )
    return app, client, store
