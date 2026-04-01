from unittest.mock import MagicMock, patch

import pytest


class TestGetStockPrice:
    def test_returns_correct_format(self):
        """Mock 模式下 get_stock_price 回傳正確格式。"""
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.iloc.__getitem__ = MagicMock(side_effect=lambda i: MagicMock(
            **{"__getitem__": MagicMock(return_value=MagicMock(
                **{"iloc.__getitem__": MagicMock(return_value=175.0 if i == -1 else 165.0)}
            ))}
        ))

        # 用較簡單的方式 mock yfinance.download
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2025-01-01", periods=5)
        mock_data = pd.DataFrame({
            ("Close", "NVDA"): [160.0, 165.0, 168.0, 170.0, 175.0],
            ("High", "NVDA"):  [162.0, 167.0, 170.0, 172.0, 177.0],
            ("Low", "NVDA"):   [158.0, 163.0, 166.0, 168.0, 173.0],
            ("Volume", "NVDA"): [1e8, 1.1e8, 1.2e8, 1.3e8, 1.4e8],
        }, index=dates)
        mock_data.columns = pd.MultiIndex.from_tuples(mock_data.columns)

        with patch("src.worker.tools.yf.download", return_value=mock_data):
            from src.worker.tools import get_stock_price
            result = get_stock_price("NVDA")

        assert result["ticker"] == "NVDA"
        assert isinstance(result["current_price"], float)
        assert isinstance(result["change_pct"], float)
        assert isinstance(result["volume"], int)
        assert "52w_high" in result
        assert "52w_low" in result

    def test_ticker_is_uppercase(self):
        """ticker 欄位應為大寫。"""
        import pandas as pd

        dates = pd.date_range("2025-01-01", periods=5)
        mock_data = pd.DataFrame({
            ("Close", "AAPL"): [150.0, 152.0, 153.0, 154.0, 155.0],
            ("High", "AAPL"):  [152.0, 154.0, 155.0, 156.0, 157.0],
            ("Low", "AAPL"):   [148.0, 150.0, 151.0, 152.0, 153.0],
            ("Volume", "AAPL"): [5e7, 5.1e7, 5.2e7, 5.3e7, 5.4e7],
        }, index=dates)
        mock_data.columns = pd.MultiIndex.from_tuples(mock_data.columns)

        with patch("src.worker.tools.yf.download", return_value=mock_data):
            from src.worker.tools import get_stock_price
            result = get_stock_price("aapl")

        assert result["ticker"] == "AAPL"


class TestGetCompanyNews:
    def test_returns_at_least_one_article(self):
        """get_company_news 回傳至少 1 筆新聞。"""
        mock_news = [
            {
                "headline": "NVDA surges on AI demand",
                "summary": "NVIDIA shares rose sharply...",
            },
            {
                "headline": "Market update",
                "summary": "Stocks mixed today...",
            },
        ]

        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = mock_news
            from src.worker.tools import get_company_news
            result = get_company_news("NVDA", days=7)

        assert len(result) >= 1

    def test_correct_format(self):
        """每筆新聞應包含 title、summary、sentiment 欄位。"""
        mock_news = [{"headline": "NVDA beats earnings", "summary": "Strong results..."}]

        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = mock_news
            from src.worker.tools import get_company_news
            result = get_company_news("NVDA")

        assert "title" in result[0]
        assert "summary" in result[0]
        assert result[0]["sentiment"] in {"positive", "negative", "neutral"}

    def test_sentiment_positive(self):
        """標題含正面關鍵字應分類為 positive。"""
        mock_news = [{"headline": "NVDA surges to record high", "summary": ""}]

        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = mock_news
            from src.worker.tools import get_company_news
            result = get_company_news("NVDA")

        assert result[0]["sentiment"] == "positive"
