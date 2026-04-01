from unittest.mock import MagicMock, patch

import pytest

from src.common.schemas import AnalysisResult, FinalReport, utcnow
from src.aggregator.agent import AggregatorAgent


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def result():
    return AnalysisResult(
        task_id="task-001",
        correlation_id="corr-001",
        ticker="NVDA",
        content="## NVDA Analysis\nStrong results...",
        iterations_used=3,
        timestamp=utcnow(),
    )


@pytest.fixture
def redis_mock():
    """模擬 RedisClient，預設為「所有 subtask 已完成、成功取鎖」的情境。"""
    mock = MagicMock()
    mock.get_task_meta.return_value = {"total": "2", "query": "分析 NVDA 和 TSLA"}
    mock.get_completed.return_value = 2
    mock.acquire_synthesis_lock.return_value = True
    mock.get_results.return_value = [
        "## NVDA Analysis\nStrong results...",
        "## TSLA Analysis\nMixed results...",
    ]
    mock.get_tickers.return_value = ["NVDA", "TSLA"]
    return mock


# ── 成功路徑：合成完成 ─────────────────────────────────────────────────────

class TestAggregatorAgentSuccess:
    def test_returns_final_report(self, result, redis_mock):
        """所有條件滿足時應回傳 FinalReport。"""
        with patch("src.aggregator.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = MagicMock(text="## 股票對比分析報告\n...")
            final_report, _ = AggregatorAgent().run(result, redis_mock)

        assert isinstance(final_report, FinalReport)

    def test_final_report_fields(self, result, redis_mock):
        """FinalReport 應包含正確的 task_id、query、tickers。"""
        with patch("src.aggregator.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = MagicMock(text="## 報告")
            final_report, _ = AggregatorAgent().run(result, redis_mock)

        assert final_report.task_id == "task-001"
        assert final_report.original_query == "分析 NVDA 和 TSLA"
        assert set(final_report.tickers) == {"NVDA", "TSLA"}

    def test_report_content(self, result, redis_mock):
        """FinalReport.report 應包含 Gemini 回傳的文字。"""
        with patch("src.aggregator.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = MagicMock(text="## 股票對比分析報告\nNVDA vs TSLA")
            final_report, _ = AggregatorAgent().run(result, redis_mock)

        assert "## 股票對比分析報告" in final_report.report

    def test_thoughts_count_when_synthesized(self, result, redis_mock):
        """合成完成時應產生 9 個 thought（3 輪 × 3 步）。"""
        with patch("src.aggregator.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = MagicMock(text="## 報告")
            _, thoughts = AggregatorAgent().run(result, redis_mock)

        assert len(thoughts) == 9

    def test_thoughts_agent_role(self, result, redis_mock):
        """所有 thought 的 agent_role 應為 aggregator。"""
        with patch("src.aggregator.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = MagicMock(text="## 報告")
            _, thoughts = AggregatorAgent().run(result, redis_mock)

        assert all(t.agent_role == "aggregator" for t in thoughts)


# ── 尚未完成：等待更多 subtask ─────────────────────────────────────────────

class TestAggregatorAgentWaiting:
    def test_returns_none_when_not_completed(self, result, redis_mock):
        """completed < total 時應回傳 None。"""
        redis_mock.get_completed.return_value = 1  # 只完成 1/2

        final_report, _ = AggregatorAgent().run(result, redis_mock)

        assert final_report is None

    def test_thoughts_count_when_waiting(self, result, redis_mock):
        """尚未完成時只有 Iter 1 的 3 個 thought。"""
        redis_mock.get_completed.return_value = 1

        _, thoughts = AggregatorAgent().run(result, redis_mock)

        assert len(thoughts) == 3

    def test_gemini_not_called_when_waiting(self, result, redis_mock):
        """尚未完成時不應呼叫 Gemini。"""
        redis_mock.get_completed.return_value = 1

        with patch("src.aggregator.agent._gemini") as mock_gemini:
            AggregatorAgent().run(result, redis_mock)

        mock_gemini.generate_content.assert_not_called()


# ── 取鎖失敗：另一個 Aggregator 已在合成 ──────────────────────────────────

class TestAggregatorAgentLockFailed:
    def test_returns_none_when_lock_not_acquired(self, result, redis_mock):
        """取鎖失敗時應回傳 None。"""
        redis_mock.acquire_synthesis_lock.return_value = False

        final_report, _ = AggregatorAgent().run(result, redis_mock)

        assert final_report is None

    def test_thoughts_count_when_lock_failed(self, result, redis_mock):
        """取鎖失敗時只有 Iter 1 + Iter 2 的 6 個 thought。"""
        redis_mock.acquire_synthesis_lock.return_value = False

        _, thoughts = AggregatorAgent().run(result, redis_mock)

        assert len(thoughts) == 6

    def test_gemini_not_called_when_lock_failed(self, result, redis_mock):
        """取鎖失敗時不應呼叫 Gemini。"""
        redis_mock.acquire_synthesis_lock.return_value = False

        with patch("src.aggregator.agent._gemini") as mock_gemini:
            AggregatorAgent().run(result, redis_mock)

        mock_gemini.generate_content.assert_not_called()
