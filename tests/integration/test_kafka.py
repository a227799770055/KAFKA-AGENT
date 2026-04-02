"""
Kafka integration tests — requires real Kafka at localhost:29092.

Run: docker-compose up -d kafka
Then: pytest tests/integration/test_kafka.py -v

Strategy:
  - Consumer subscribes first, does a dummy poll to trigger partition assignment,
    then we produce a message and verify it can be consumed.
  - Each test uses a unique group_id (with task_id) to avoid offset conflicts.
"""
import time
import uuid

import pytest

from configs.kafka_config import (
    TOPIC_AGENT_THOUGHTS,
    TOPIC_ANALYSIS_RESULTS,
    TOPIC_FINAL_REPORTS,
    TOPIC_TICKER_TASKS,
)
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.schemas import (
    AgentThought,
    AnalysisResult,
    FinalReport,
    TickerTask,
    utcnow,
)
from tests.integration.conftest import requires_kafka


def _make_ticker_task(task_id: str, ticker: str = "NVDA") -> TickerTask:
    return TickerTask(
        task_id=task_id,
        correlation_id=uuid.uuid4().hex,
        ticker=ticker,
        total_subtasks=1,
        created_at=utcnow(),
    )


def _make_analysis_result(task_id: str, ticker: str = "NVDA") -> AnalysisResult:
    return AnalysisResult(
        task_id=task_id,
        correlation_id=uuid.uuid4().hex,
        ticker=ticker,
        content="## NVDA Analysis\nTest content",
        iterations_used=3,
        timestamp=utcnow(),
    )


def _make_final_report(task_id: str) -> FinalReport:
    return FinalReport(
        task_id=task_id,
        original_query="分析 NVDA",
        tickers=["NVDA"],
        report="## 最終報告\nTest report",
        generated_at=utcnow(),
    )


def _make_agent_thought(task_id: str) -> AgentThought:
    return AgentThought(
        task_id=task_id,
        correlation_id=uuid.uuid4().hex,
        agent_role="worker",
        ticker="NVDA",
        step="THOUGHT",
        iteration=1,
        content="分析 NVDA 股價...",
        timestamp=utcnow(),
    )


def _consume_with_timeout(
    consumer: KafkaConsumer,
    model,
    match_task_id: str,
    timeout_seconds: float = 10.0,
):
    """輪詢直到找到符合 task_id 的訊息或逾時。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        msg = consumer.poll(model, timeout=1.0)
        if msg is not None and msg.task_id == match_task_id:
            return msg
    return None


@requires_kafka
class TestKafkaTickerTask:
    def test_produce_and_consume_ticker_task(self, task_id):
        group_id = f"test-{task_id}"
        task = _make_ticker_task(task_id)

        consumer = KafkaConsumer(topics=[TOPIC_TICKER_TASKS], group_id=group_id)
        # 觸發 partition assignment
        consumer.poll(TickerTask, timeout=2.0)

        producer = KafkaProducer()
        producer.produce(TOPIC_TICKER_TASKS, task, key=task_id)
        producer.flush()

        try:
            result = _consume_with_timeout(consumer, TickerTask, task_id)
        finally:
            consumer.close()

        assert result is not None
        assert result.task_id == task_id
        assert result.ticker == "NVDA"

    def test_ticker_task_fields_preserved(self, task_id):
        group_id = f"test-fields-{task_id}"
        task = _make_ticker_task(task_id, ticker="TSLA")

        consumer = KafkaConsumer(topics=[TOPIC_TICKER_TASKS], group_id=group_id)
        consumer.poll(TickerTask, timeout=2.0)

        producer = KafkaProducer()
        producer.produce(TOPIC_TICKER_TASKS, task, key=task_id)
        producer.flush()

        try:
            result = _consume_with_timeout(consumer, TickerTask, task_id)
        finally:
            consumer.close()

        assert result is not None
        assert result.ticker == "TSLA"
        assert result.total_subtasks == 1
        assert result.schema_version == "1.0"


@requires_kafka
class TestKafkaAnalysisResult:
    def test_produce_and_consume_analysis_result(self, task_id):
        group_id = f"test-ar-{task_id}"
        msg = _make_analysis_result(task_id)

        consumer = KafkaConsumer(topics=[TOPIC_ANALYSIS_RESULTS], group_id=group_id)
        consumer.poll(AnalysisResult, timeout=2.0)

        producer = KafkaProducer()
        producer.produce(TOPIC_ANALYSIS_RESULTS, msg, key=task_id)
        producer.flush()

        try:
            result = _consume_with_timeout(consumer, AnalysisResult, task_id)
        finally:
            consumer.close()

        assert result is not None
        assert result.task_id == task_id
        assert result.ticker == "NVDA"
        assert result.iterations_used == 3

    def test_content_preserved_after_serialization(self, task_id):
        group_id = f"test-content-{task_id}"
        msg = _make_analysis_result(task_id)

        consumer = KafkaConsumer(topics=[TOPIC_ANALYSIS_RESULTS], group_id=group_id)
        consumer.poll(AnalysisResult, timeout=2.0)

        producer = KafkaProducer()
        producer.produce(TOPIC_ANALYSIS_RESULTS, msg, key=task_id)
        producer.flush()

        try:
            result = _consume_with_timeout(consumer, AnalysisResult, task_id)
        finally:
            consumer.close()

        assert result is not None
        assert "## NVDA Analysis" in result.content


@requires_kafka
class TestKafkaFinalReport:
    def test_produce_and_consume_final_report(self, task_id):
        group_id = f"test-fr-{task_id}"
        msg = _make_final_report(task_id)

        consumer = KafkaConsumer(topics=[TOPIC_FINAL_REPORTS], group_id=group_id)
        consumer.poll(FinalReport, timeout=2.0)

        producer = KafkaProducer()
        producer.produce(TOPIC_FINAL_REPORTS, msg, key=task_id)
        producer.flush()

        try:
            result = _consume_with_timeout(consumer, FinalReport, task_id)
        finally:
            consumer.close()

        assert result is not None
        assert result.task_id == task_id
        assert result.tickers == ["NVDA"]
        assert "## 最終報告" in result.report


@requires_kafka
class TestKafkaAgentThought:
    def test_produce_and_consume_agent_thought(self, task_id):
        group_id = f"test-thought-{task_id}"
        msg = _make_agent_thought(task_id)

        consumer = KafkaConsumer(topics=[TOPIC_AGENT_THOUGHTS], group_id=group_id)
        consumer.poll(AgentThought, timeout=2.0)

        producer = KafkaProducer()
        producer.produce(TOPIC_AGENT_THOUGHTS, msg, key=task_id)
        producer.flush()

        try:
            result = _consume_with_timeout(consumer, AgentThought, task_id)
        finally:
            consumer.close()

        assert result is not None
        assert result.task_id == task_id
        assert result.agent_role == "worker"
        assert result.step == "THOUGHT"


@requires_kafka
class TestKafkaMultipleMessages:
    def test_produce_multiple_messages_all_consumed(self, task_id):
        """產出 3 筆訊息，全部都能被消費到。"""
        group_id = f"test-multi-{task_id}"
        tickers = ["NVDA", "TSLA", "AAPL"]
        tasks = [_make_ticker_task(task_id, t) for t in tickers]

        consumer = KafkaConsumer(topics=[TOPIC_TICKER_TASKS], group_id=group_id)
        consumer.poll(TickerTask, timeout=2.0)

        producer = KafkaProducer()
        for task in tasks:
            producer.produce(TOPIC_TICKER_TASKS, task, key=task_id)
        producer.flush()

        received = []
        deadline = time.monotonic() + 15.0
        try:
            while len(received) < 3 and time.monotonic() < deadline:
                msg = consumer.poll(TickerTask, timeout=1.0)
                if msg is not None and msg.task_id == task_id:
                    received.append(msg.ticker)
        finally:
            consumer.close()

        assert set(received) == {"NVDA", "TSLA", "AAPL"}
