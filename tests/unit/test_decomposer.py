from unittest.mock import MagicMock, patch

import pytest

from src.common.schemas import TickerTask
from src.decomposer.agent import DecomposerAgent


# ── Helpers ────────────────────────────────────────────────────────────────

def _mock_gemini(response_text: str):
    """回傳一個模擬 Gemini 回應的 mock object。"""
    mock_response = MagicMock()
    mock_response.text = response_text
    return mock_response


# ── 成功路徑 ───────────────────────────────────────────────────────────────

class TestDecomposerAgentSuccess:
    def test_returns_ticker_tasks(self):
        """正常輸入應回傳對應數量的 TickerTask。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["NVDA", "TSLA"]')
            tasks, _ = DecomposerAgent().run("分析 NVDA 和 TSLA")

        assert len(tasks) == 2
        assert all(isinstance(t, TickerTask) for t in tasks)

    def test_tickers_are_uppercase(self):
        """ticker 應強制轉為大寫。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["nvda", "aapl"]')
            tasks, _ = DecomposerAgent().run("分析 nvda 和 aapl")

        tickers = [t.ticker for t in tasks]
        assert tickers == ["NVDA", "AAPL"]

    def test_all_tasks_share_same_task_id(self):
        """同一次查詢的所有 TickerTask 應共用同一個 task_id。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["NVDA", "TSLA", "AAPL"]')
            tasks, _ = DecomposerAgent().run("分析三支股票")

        task_ids = {t.task_id for t in tasks}
        assert len(task_ids) == 1

    def test_correlation_ids_are_unique(self):
        """每個 TickerTask 的 correlation_id 應各自獨立。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["NVDA", "TSLA"]')
            tasks, _ = DecomposerAgent().run("分析兩支股票")

        corr_ids = [t.correlation_id for t in tasks]
        assert len(corr_ids) == len(set(corr_ids))

    def test_total_subtasks_is_correct(self):
        """每個 TickerTask 的 total_subtasks 應等於 ticker 總數。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["NVDA", "TSLA", "AAPL"]')
            tasks, _ = DecomposerAgent().run("分析三支股票")

        assert all(t.total_subtasks == 3 for t in tasks)

    def test_duplicate_tickers_are_removed(self):
        """重複的 ticker 應去重。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["NVDA", "NVDA", "TSLA"]')
            tasks, _ = DecomposerAgent().run("重複輸入 NVDA NVDA TSLA")

        tickers = [t.ticker for t in tasks]
        assert tickers == ["NVDA", "TSLA"]

    def test_thoughts_count(self):
        """應產生 6 個 thought（2 輪 × 3 步）。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('["NVDA"]')
            _, thoughts = DecomposerAgent().run("分析 NVDA")

        assert len(thoughts) == 6

    def test_gemini_response_with_extra_text(self):
        """Gemini 回應包含多餘文字時，應仍能正確解析 JSON。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini(
                'Here are the tickers: ["NVDA", "TSLA"] as requested.'
            )
            tasks, _ = DecomposerAgent().run("幫我看看這兩支")

        assert len(tasks) == 2


# ── 失敗路徑 ───────────────────────────────────────────────────────────────

class TestDecomposerAgentFailure:
    def test_raises_on_empty_tickers(self):
        """Gemini 回傳空陣列時應 raise ValueError。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini('[]')
            with pytest.raises(ValueError, match="No tickers found"):
                DecomposerAgent().run("今天天氣真好")

    def test_raises_on_unparseable_response(self):
        """Gemini 回傳完全無法解析的文字時應 raise ValueError。"""
        with patch("src.decomposer.agent._gemini") as mock_gemini:
            mock_gemini.generate_content.return_value = _mock_gemini("I don't know any tickers.")
            with pytest.raises(ValueError, match="unparseable"):
                DecomposerAgent().run("無效輸入")
