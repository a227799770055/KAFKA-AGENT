import logging
import signal

from configs.kafka_config import (
    GROUP_AGGREGATORS,
    TOPIC_AGENT_THOUGHTS,
    TOPIC_ANALYSIS_RESULTS,
    TOPIC_FINAL_REPORTS,
)
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.logging_config import setup_logging
from src.common.redis_client import RedisClient
from src.common.schemas import AnalysisResult, FinalReport
from src.aggregator.agent import AggregatorAgent

setup_logging()
logger = logging.getLogger(__name__)

_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("[aggregator] shutdown signal received")
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    consumer = KafkaConsumer(topics=[TOPIC_ANALYSIS_RESULTS], group_id=GROUP_AGGREGATORS)
    producer = KafkaProducer()
    redis = RedisClient()
    agent = AggregatorAgent()

    logger.info("[aggregator] started, listening on %s", TOPIC_ANALYSIS_RESULTS)

    try:
        while _running:
            result: AnalysisResult | None = consumer.poll(AnalysisResult)
            if result is None:
                continue

            logger.info("[aggregator] received result ticker=%s task_id=%s", result.ticker, result.task_id)

            final_report, thoughts = agent.run(result, redis)

            # ── 發布 AgentThoughts ────────────────────────────────────
            for thought in thoughts:
                producer.produce(TOPIC_AGENT_THOUGHTS, thought, key=result.task_id)

            # ── 合成完成：發布 FinalReport ────────────────────────────
            if isinstance(final_report, FinalReport):
                producer.produce(TOPIC_FINAL_REPORTS, final_report, key=result.task_id)
                logger.info(
                    "[aggregator] published final report task_id=%s tickers=%s",
                    result.task_id, final_report.tickers,
                )

            producer.flush()
            consumer.commit()  # 發布成功後才 commit offset

    finally:
        producer.flush()
        consumer.close()
        logger.info("[aggregator] stopped")


if __name__ == "__main__":
    main()
