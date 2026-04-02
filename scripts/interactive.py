"""
互動式 CLI 入口。
啟動後持續等待使用者輸入，每筆查詢完成後繼續等待下一輪。

使用方式：
  PYTHONPATH=. python scripts/interactive.py

輸入 'exit' 或按 Ctrl+C 結束。
"""
import logging

from configs.kafka_config import TOPIC_AGENT_THOUGHTS, TOPIC_FINAL_REPORTS, TOPIC_TICKER_TASKS
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.logging_config import setup_logging
from src.common.redis_client import RedisClient
from src.common.schemas import FinalReport
from src.decomposer.agent import DecomposerAgent

setup_logging()
logger = logging.getLogger(__name__)

POLL_TIMEOUT = 2.0
MAX_WAIT = 120.0


def run_query(query: str, producer: KafkaProducer, redis: RedisClient, agent: DecomposerAgent) -> None:
    try:
        tasks, thoughts = agent.run(query)
    except ValueError as e:
        print(f"\n無法識別股票代碼，請輸入感興趣的股票代號（例如：分析 NVDA 和 TSLA）。\n原因：{e}\n")
        return

    task_id = tasks[0].task_id
    total = len(tasks)
    tickers = [t.ticker for t in tasks]

    redis.init_task(task_id=task_id, total=total, query=query)

    # 先訂閱再 produce，避免錯過訊息
    consumer = KafkaConsumer(
        topics=[TOPIC_FINAL_REPORTS],
        group_id=f"cli-{task_id}",
    )
    consumer.poll(FinalReport, timeout=2.0)  # 觸發 partition assignment

    for thought in thoughts:
        producer.produce(TOPIC_AGENT_THOUGHTS, thought, key=task_id)
    for task in tasks:
        producer.produce(TOPIC_TICKER_TASKS, task, key=None)
    producer.flush()

    print(f"\n分析中：{tickers}，請稍候...\n")

    import time
    start = time.monotonic()
    report = None

    try:
        while True:
            if time.monotonic() - start > MAX_WAIT:
                print(f"逾時（>{MAX_WAIT}s），未收到報告。\n")
                break
            msg = consumer.poll(FinalReport, timeout=POLL_TIMEOUT)
            if msg and msg.task_id == task_id:
                report = msg
                break
    finally:
        consumer.close()

    if report:
        print(report.report)
        print()


def main() -> None:
    print("=== Kafka AI Agent ===")
    print("輸入股票查詢（例如：分析 NVDA 和 TSLA），輸入 'exit' 結束。\n")

    producer = KafkaProducer()
    redis = RedisClient()
    agent = DecomposerAgent()

    try:
        while True:
            try:
                query = input("查詢> ").strip()
            except EOFError:
                break

            if not query:
                continue
            if query.lower() == "exit":
                break

            run_query(query, producer, redis, agent)

    except KeyboardInterrupt:
        pass
    finally:
        producer.flush()
        print("\n已結束。")


if __name__ == "__main__":
    main()
