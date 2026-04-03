"""
Worker 數量 benchmark 工具。

測量從發出查詢到收到 FinalReport 的總耗時。
分別在不同 worker 數量下執行，比較加速效果。

使用方式：
  # 1 個 worker
  docker-compose up -d --scale worker=1
  python scripts/benchmark_workers.py "分析 NVDA 和 TSLA"

  # 2 個 worker
  docker-compose up -d --scale worker=2
  python scripts/benchmark_workers.py "分析 NVDA 和 TSLA"
"""
import sys
import time

from configs.kafka_config import TICKER_TASKS_PARTITIONS, TOPIC_AGENT_THOUGHTS, TOPIC_FINAL_REPORTS, TOPIC_TICKER_TASKS
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.logging_config import setup_logging
from src.common.redis_client import RedisClient
from src.common.schemas import FinalReport
from src.decomposer.agent import DecomposerAgent

setup_logging()

POLL_TIMEOUT = 2.0      # 每次 poll 等待秒數
MAX_WAIT = 120.0        # 最長等待秒數，超過視為逾時


def run_benchmark(query: str) -> None:
    print(f"\n{'='*60}")
    print(f"Query    : {query}")

    # ── 解析 tickers ──────────────────────────────────────────────
    agent = DecomposerAgent()
    try:
        tasks, thoughts = agent.run(query)
    except ValueError as e:
        print(f"錯誤：無法識別股票代碼。{e}")
        sys.exit(1)

    tickers = [t.ticker for t in tasks]
    task_id = tasks[0].task_id
    total = len(tasks)

    print(f"Tickers  : {tickers}")
    print(f"Task ID  : {task_id}")
    print(f"{'='*60}\n")

    # ── 登記 Redis 任務狀態 ───────────────────────────────────────
    redis = RedisClient()
    redis.init_task(task_id=task_id, total=total, query=query)

    # ── 訂閱 FinalReport（先訂閱再 produce，避免錯過訊息）────────
    consumer = KafkaConsumer(
        topics=[TOPIC_FINAL_REPORTS],
        group_id=f"bench-{task_id}",
    )
    consumer.poll(FinalReport, timeout=2.0)  # 觸發 partition assignment

    # ── 發布任務，計時從這裡開始 ──────────────────────────────────
    producer = KafkaProducer()
    for thought in thoughts:
        producer.produce(TOPIC_AGENT_THOUGHTS, thought, key=task_id)
    for i, task in enumerate(tasks):
        producer.produce(TOPIC_TICKER_TASKS, task, key=None, partition=i % TICKER_TASKS_PARTITIONS)
    producer.flush()

    start_time = time.monotonic()
    print(f"[{_ts()}] 任務已發布，開始計時...")

    # ── 等待 FinalReport ──────────────────────────────────────────
    report = None
    try:
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > MAX_WAIT:
                print(f"\n[{_ts()}] 逾時（>{MAX_WAIT}s），未收到報告。")
                break

            msg = consumer.poll(FinalReport, timeout=POLL_TIMEOUT)
            if msg and msg.task_id == task_id:
                report = msg
                break
    finally:
        consumer.close()

    end_time = time.monotonic()
    elapsed = end_time - start_time

    # ── 輸出結果 ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if report:
        print(f"✅ 收到報告")
        print(f"{'─'*60}")
        print(f"  Tickers   : {report.tickers}")
        print(f"  Elapsed   : {elapsed:.2f}s")
        print(f"  Per ticker: {elapsed / len(tickers):.2f}s")
        print(f"{'─'*60}")
        print("\n--- Report Preview (first 300 chars) ---")
        print(report.report[:300])
        print("...")
    else:
        print(f"❌ 未收到報告（elapsed: {elapsed:.2f}s）")
    print(f"{'='*60}\n")


def _ts() -> str:
    """回傳當前時間字串，用於 log 輸出。"""
    return time.strftime("%H:%M:%S")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python scripts/benchmark_workers.py "分析 NVDA 和 TSLA"')
        sys.exit(1)

    run_benchmark(sys.argv[1])
