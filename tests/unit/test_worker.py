from unittest.mock import MagicMock, patch

import pytest

from src.common.retry import MaxRetriesExceeded
from src.common.schemas import AnalysisResult, DLQMessage, TickerTask, utcnow
from src.worker.agent import WorkerAgent


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def task():
    return TickerTask(
        task_id="task-001",
        correlation_id="corr-001",
        ticker="NVDA",
        total_subtasks=1,
        created_at=utcnow(),
    )


@pytest.fixture
def price_data():
    return {
        "ticker": "NVDA",
        "current_price": 875.4,
        "change_pct": 2.3,
        "volume": 45000000,
        "52w_high": 974.0,
        "52w_low": 402.0,
    }


@pytest.fixture
def news_data():
    return [
        {"title": "NVDA surges on AI demand", "summary": "Strong results...", "sentiment": "positive"},
    ]


# ── 成功路徑 ───────────────────────────────────────────────────────────────

class TestWorkerAgentSuccess:
    def test_returns_analysis_result(self, task, price_data, news_data):
        """成功時回傳 AnalysisResult。"""
        with patch("src.worker.agent.get_stock_price", return_value=price_data), \
             patch("src.worker.agent.get_company_news", return_value=news_data), \
             patch("src.worker.agent.generate_summary", return_value="## NVDA Analysis"):

            result, thoughts = WorkerAgent().run(task)

        assert isinstance(result, AnalysisResult)
        assert result.ticker == "NVDA"
        assert result.task_id == "task-001"
        assert result.content == "## NVDA Analysis"
        assert result.iterations_used == 3

    def test_thoughts_count(self, task, price_data, news_data):
        """ReAct Loop 應產生 9 個 thought（每輪 THOUGHT + ACTION + OBSERVATION）。"""
        with patch("src.worker.agent.get_stock_price", return_value=price_data), \
             patch("src.worker.agent.get_company_news", return_value=news_data), \
             patch("src.worker.agent.generate_summary", return_value="## NVDA Analysis"):

            _, thoughts = WorkerAgent().run(task)

        assert len(thoughts) == 9

    def test_thoughts_steps_sequence(self, task, price_data, news_data):
        """thoughts 的 step 順序應為 THOUGHT → ACTION → OBSERVATION（x3）。"""
        with patch("src.worker.agent.get_stock_price", return_value=price_data), \
             patch("src.worker.agent.get_company_news", return_value=news_data), \
             patch("src.worker.agent.generate_summary", return_value="## NVDA Analysis"):

            _, thoughts = WorkerAgent().run(task)

        steps = [t.step for t in thoughts]
        assert steps == [
            "THOUGHT", "ACTION", "OBSERVATION",
            "THOUGHT", "ACTION", "OBSERVATION",
            "THOUGHT", "ACTION", "OBSERVATION",
        ]

    def test_thoughts_iterations(self, task, price_data, news_data):
        """每輪 iteration 編號應為 1、2、3。"""
        with patch("src.worker.agent.get_stock_price", return_value=price_data), \
             patch("src.worker.agent.get_company_news", return_value=news_data), \
             patch("src.worker.agent.generate_summary", return_value="## NVDA Analysis"):

            _, thoughts = WorkerAgent().run(task)

        iterations = [t.iteration for t in thoughts]
        assert iterations == [1, 1, 1, 2, 2, 2, 3, 3, 3]

    def test_thoughts_agent_role(self, task, price_data, news_data):
        """所有 thought 的 agent_role 應為 worker。"""
        with patch("src.worker.agent.get_stock_price", return_value=price_data), \
             patch("src.worker.agent.get_company_news", return_value=news_data), \
             patch("src.worker.agent.generate_summary", return_value="## NVDA Analysis"):

            _, thoughts = WorkerAgent().run(task)

        assert all(t.agent_role == "worker" for t in thoughts)


# ── 失敗路徑 ───────────────────────────────────────────────────────────────

class TestWorkerAgentFailure:
    def test_returns_dlq_on_max_retries_exceeded(self, task):
        """get_stock_price 耗盡重試後應回傳 DLQMessage。"""
        with patch("src.worker.agent.get_stock_price", side_effect=MaxRetriesExceeded("yf failed")):
            result, thoughts = WorkerAgent().run(task)

        assert isinstance(result, DLQMessage)
        assert result.original_message.ticker == "NVDA"
        assert result.retry_count == 3

    def test_dlq_contains_stack_trace(self, task):
        """DLQMessage 應包含非空的 stack_trace。"""
        with patch("src.worker.agent.get_stock_price", side_effect=MaxRetriesExceeded("yf failed")):
            result, _ = WorkerAgent().run(task)

        assert isinstance(result.stack_trace, str)
        assert len(result.stack_trace) > 0

    def test_thoughts_still_emitted_on_failure(self, task):
        """失敗時仍應有部分 thought 被記錄（在失敗點之前的）。"""
        with patch("src.worker.agent.get_stock_price", side_effect=MaxRetriesExceeded("yf failed")):
            _, thoughts = WorkerAgent().run(task)

        # iter 1 的 THOUGHT 和 ACTION 已發出，OBSERVATION 還沒到就失敗了
        assert len(thoughts) >= 2
        assert thoughts[0].step == "THOUGHT"
        assert thoughts[1].step == "ACTION"

    def test_get_company_news_failure_returns_dlq(self, task, price_data):
        """get_company_news 失敗也應回傳 DLQMessage。"""
        with patch("src.worker.agent.get_stock_price", return_value=price_data), \
             patch("src.worker.agent.get_company_news", side_effect=MaxRetriesExceeded("finnhub failed")):

            result, _ = WorkerAgent().run(task)

        assert isinstance(result, DLQMessage)
