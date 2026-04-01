import logging
import signal
import sys

from configs.kafka_config import (
    GROUP_WORKERS,
    TOPIC_AGENT_THOUGHTS,
    TOPIC_ANALYSIS_RESULTS,
    TOPIC_DLQ,
    TOPIC_TICKER_TASKS,
)
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.logging_config import setup_logging
from src.common.redis_client import RedisClient
from src.common.schemas import AnalysisResult, DLQMessage, TickerTask
from src.worker.agent import WorkerAgent

setup_logging()
logger = logging.getLogger(__name__)

_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("[worker] shutdown signal received")
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    consumer = KafkaConsumer(topics=[TOPIC_TICKER_TASKS], group_id=GROUP_WORKERS)
    producer = KafkaProducer()
    redis = RedisClient()
    agent = WorkerAgent()

    logger.info("[worker] started, listening on %s", TOPIC_TICKER_TASKS)

    try:
        while _running:
            task: TickerTask | None = consumer.poll(TickerTask)
            if task is None:
                continue

            logger.info("[worker] received task ticker=%s task_id=%s", task.ticker, task.task_id)

            result, thoughts = agent.run(task)

            # ── 發布 AgentThoughts ─────────────────────────────────────
            for thought in thoughts:
                producer.produce(TOPIC_AGENT_THOUGHTS, thought, key=task.task_id)

            # ── 成功：先寫 Redis，再發 Kafka（避免 Aggregator 收到訊息時 Redis 尚未更新）
            if isinstance(result, AnalysisResult):
                redis.push_result(task.task_id, result.content)
                redis.push_ticker(task.task_id, task.ticker)
                completed = redis.increment_completed(task.task_id)
                producer.produce(TOPIC_ANALYSIS_RESULTS, result, key=task.task_id)
                logger.info(
                    "[worker] published result ticker=%s completed=%d/%d",
                    task.ticker, completed, task.total_subtasks,
                )

            # ── 失敗：先更新 Redis，再發 DLQ，讓 Aggregator 不卡住 ──────
            elif isinstance(result, DLQMessage):
                completed = redis.increment_completed(task.task_id)
                producer.produce(TOPIC_DLQ, result, key=task.task_id)
                logger.warning(
                    "[worker] sent to DLQ ticker=%s completed=%d/%d",
                    task.ticker, completed, task.total_subtasks,
                )

            producer.flush()

    finally:
        consumer.close()
        logger.info("[worker] stopped")


if __name__ == "__main__":
    main()
