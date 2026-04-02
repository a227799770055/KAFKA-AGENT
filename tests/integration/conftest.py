"""
Integration test fixtures.

These tests require real services to be running:
  docker-compose up -d kafka redis

Tests are automatically skipped if services are unavailable.
"""
import uuid

import pytest
import redis as redis_lib
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient


def _redis_available() -> bool:
    try:
        r = redis_lib.from_url("redis://localhost:6379/0")
        return r.ping()
    except Exception:
        return False


def _kafka_available() -> bool:
    try:
        admin = AdminClient({"bootstrap.servers": "localhost:29092"})
        meta = admin.list_topics(timeout=3)
        return meta is not None
    except Exception:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available (run: docker-compose up -d redis)",
)

requires_kafka = pytest.mark.skipif(
    not _kafka_available(),
    reason="Kafka not available (run: docker-compose up -d kafka)",
)


@pytest.fixture
def task_id() -> str:
    """每個測試產生獨立 task_id，避免 key 衝突。"""
    return f"test-{uuid.uuid4().hex[:8]}"
