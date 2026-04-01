import logging
import sys

from configs.kafka_config import TOPIC_AGENT_THOUGHTS, TOPIC_FINAL_REPORTS, TOPIC_TICKER_TASKS
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.logging_config import setup_logging
from src.common.redis_client import RedisClient
from src.common.schemas import FinalReport
from src.decomposer.agent import DecomposerAgent

setup_logging()
logger = logging.getLogger(__name__)


def main(query: str) -> None:
    agent = DecomposerAgent()
    producer = KafkaProducer()
    redis = RedisClient()

    tasks, thoughts = agent.run(query)

    task_id = tasks[0].task_id
    total = len(tasks)

    # ── Redis 登記任務狀態 ────────────────────────────────────────────
    redis.init_task(task_id=task_id, total=total, query=query)
    logger.info("[decomposer] init_task task_id=%s total=%d", task_id, total)

    # ── 發布 AgentThoughts ────────────────────────────────────────────
    for thought in thoughts:
        producer.produce(TOPIC_AGENT_THOUGHTS, thought, key=task_id)

    # ── 發布 TickerTasks ──────────────────────────────────────────────
    for task in tasks:
        producer.produce(TOPIC_TICKER_TASKS, task, key=task_id)
        logger.info("[decomposer] produced task ticker=%s task_id=%s", task.ticker, task_id)

    producer.flush()
    logger.info("[decomposer] done, dispatched %d task(s) for query=%r", total, query)

    # ── 等待並印出最終報告 ────────────────────────────────────────────
    print("\n等待分析報告中...\n")

    consumer = KafkaConsumer(
        topics=[TOPIC_FINAL_REPORTS],
        group_id=f"cli-{task_id}",  # 每次查詢用獨立 group_id，確保讀到自己的報告
    )

    try:
        while True:
            report = consumer.poll(FinalReport, timeout=2.0)
            if report and report.task_id == task_id:
                print(report.report)
                break
    finally:
        consumer.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.decomposer.main \"<query>\"")
        sys.exit(1)

    main(sys.argv[1])
