"""
Redis integration tests — requires real Redis at localhost:6379.

Run: docker-compose up -d redis
Then: pytest tests/integration/test_redis.py -v
"""
import pytest

from src.common.redis_client import RedisClient
from tests.integration.conftest import requires_redis


@pytest.fixture
def redis(task_id) -> RedisClient:
    """建立 RedisClient，測試結束後清除所有 task 相關 key。"""
    client = RedisClient()
    yield client
    # cleanup
    pattern = f"task:{task_id}:*"
    keys = client._r.keys(pattern)
    if keys:
        client._r.delete(*keys)


@requires_redis
class TestRedisPing:
    def test_ping_returns_true(self):
        assert RedisClient().ping() is True


@requires_redis
class TestRedisInitTask:
    def test_init_task_stores_meta(self, redis, task_id):
        redis.init_task(task_id=task_id, total=3, query="分析 NVDA TSLA AAPL")

        meta = redis.get_task_meta(task_id)
        assert meta is not None
        assert meta["total"] == "3"
        assert meta["query"] == "分析 NVDA TSLA AAPL"

    def test_init_task_sets_completed_to_zero(self, redis, task_id):
        redis.init_task(task_id=task_id, total=2, query="test")

        assert redis.get_completed(task_id) == 0

    def test_get_task_meta_returns_none_for_unknown(self, redis):
        assert redis.get_task_meta("nonexistent-task-id") is None


@requires_redis
class TestRedisResults:
    def test_push_and_get_results(self, redis, task_id):
        redis.push_result(task_id, "## NVDA\nStrong results")
        redis.push_result(task_id, "## TSLA\nMixed results")

        results = redis.get_results(task_id)
        assert len(results) == 2
        assert "## NVDA" in results[0]
        assert "## TSLA" in results[1]

    def test_get_results_empty_for_unknown(self, redis):
        assert redis.get_results("nonexistent-task-id") == []


@requires_redis
class TestRedisCompleted:
    def test_increment_returns_incremented_value(self, redis, task_id):
        redis.init_task(task_id=task_id, total=2, query="test")

        assert redis.increment_completed(task_id) == 1
        assert redis.increment_completed(task_id) == 2

    def test_get_completed_reflects_increments(self, redis, task_id):
        redis.init_task(task_id=task_id, total=3, query="test")
        redis.increment_completed(task_id)
        redis.increment_completed(task_id)

        assert redis.get_completed(task_id) == 2

    def test_get_completed_returns_zero_for_unknown(self, redis):
        assert redis.get_completed("nonexistent-task-id") == 0


@requires_redis
class TestRedisTickers:
    def test_push_and_get_tickers(self, redis, task_id):
        redis.push_ticker(task_id, "NVDA")
        redis.push_ticker(task_id, "TSLA")

        tickers = redis.get_tickers(task_id)
        assert set(tickers) == {"NVDA", "TSLA"}

    def test_push_duplicate_ticker_is_idempotent(self, redis, task_id):
        redis.push_ticker(task_id, "NVDA")
        redis.push_ticker(task_id, "NVDA")  # 重複 push

        tickers = redis.get_tickers(task_id)
        assert tickers.count("NVDA") == 1  # Set 不重複

    def test_get_tickers_empty_for_unknown(self, redis):
        assert redis.get_tickers("nonexistent-task-id") == []


@requires_redis
class TestRedisSynthesisLock:
    def test_first_acquire_succeeds(self, redis, task_id):
        assert redis.acquire_synthesis_lock(task_id) is True

    def test_second_acquire_fails(self, redis, task_id):
        redis.acquire_synthesis_lock(task_id)  # 第一個取鎖成功

        assert redis.acquire_synthesis_lock(task_id) is False  # 第二個失敗

    def test_different_tasks_have_independent_locks(self, redis, task_id):
        other_task_id = f"{task_id}-other"
        try:
            redis.acquire_synthesis_lock(task_id)
            # 不同 task_id 的鎖互相獨立
            assert redis.acquire_synthesis_lock(other_task_id) is True
        finally:
            redis._r.delete(f"task:{other_task_id}:synthesis_lock")
