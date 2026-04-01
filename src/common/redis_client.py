import os
from typing import Optional

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Lua Script：原子性取鎖，只有第一個到的 Aggregator 能成功
# KEYS[1] = lock key，例如 task:{task_id}:synthesis_lock
# 回傳 1 = 取鎖成功，回傳 0 = 已被其他人取走
_ACQUIRE_LOCK_SCRIPT = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', '60') then
  return 1
else
  return 0
end
"""


class RedisClient:
    """封裝 Redis 操作，提供任務狀態追蹤與原子鎖功能。"""

    def __init__(self, url: str = REDIS_URL) -> None:
        self._r = redis.from_url(url, decode_responses=True)
        self._acquire_lock = self._r.register_script(_ACQUIRE_LOCK_SCRIPT)

    # ── 任務初始化（Decomposer 呼叫） ──────────────────────────────────

    def init_task(self, task_id: str, total: int, query: str) -> None:
        """登記任務 metadata，設定 10 分鐘 TTL。"""
        pipe = self._r.pipeline()
        pipe.hset(f"task:{task_id}:meta", mapping={
            "total": total,
            "query": query,
        })
        pipe.set(f"task:{task_id}:completed", 0)
        pipe.expire(f"task:{task_id}:meta", 600)
        pipe.expire(f"task:{task_id}:completed", 600)
        pipe.execute()

    # ── 任務進度追蹤（Aggregator 呼叫） ───────────────────────────────

    def push_result(self, task_id: str, content: str) -> None:
        """將 Worker 的分析結果推入暫存 List，設定 10 分鐘 TTL。"""
        key = f"task:{task_id}:results"
        self._r.rpush(key, content)
        self._r.expire(key, 600)

    def increment_completed(self, task_id: str) -> int:
        """已完成的 subtask 數量 +1，回傳更新後的數值。"""
        return int(self._r.incr(f"task:{task_id}:completed"))

    def get_task_meta(self, task_id: str) -> Optional[dict]:
        """取得任務 metadata（total、query）。找不到時回傳 None。"""
        data = self._r.hgetall(f"task:{task_id}:meta")
        return data if data else None

    def get_results(self, task_id: str) -> list[str]:
        """取得所有 Worker 暫存的分析結果。"""
        return self._r.lrange(f"task:{task_id}:results", 0, -1)

    def get_completed(self, task_id: str) -> int:
        """取得已完成的 subtask 數量。"""
        val = self._r.get(f"task:{task_id}:completed")
        return int(val) if val else 0

    def push_ticker(self, task_id: str, ticker: str) -> None:
        """記錄已完成的 ticker，供 Aggregator 填入 FinalReport.tickers。"""
        key = f"task:{task_id}:tickers"
        self._r.sadd(key, ticker)
        self._r.expire(key, 600)

    def get_tickers(self, task_id: str) -> list[str]:
        """取得所有已完成的 ticker 清單。"""
        return list(self._r.smembers(f"task:{task_id}:tickers"))

    # ── 原子鎖（Aggregator 防止重複合成） ─────────────────────────────

    def acquire_synthesis_lock(self, task_id: str) -> bool:
        """
        嘗試取得合成鎖，60 秒後自動釋放。
        回傳 True 表示取鎖成功（此 Aggregator 負責合成）。
        回傳 False 表示已被其他 Aggregator 取走，直接跳過。
        """
        key = f"task:{task_id}:synthesis_lock"
        result = self._acquire_lock(keys=[key])
        return result == 1

    # ── 健康確認 ───────────────────────────────────────────────────────

    def ping(self) -> bool:
        """確認 Redis 連線是否正常。"""
        try:
            return self._r.ping()
        except Exception:
            return False
