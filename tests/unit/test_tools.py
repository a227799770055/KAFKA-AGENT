from unittest.mock import patch

import pandas as pd
import pytest


class TestGetStockPrice:
    def _make_mock_data(self, ticker: str, closes, highs, lows, volumes):
        dates = pd.date_range("2025-01-01", periods=len(closes))
        df = pd.DataFrame({
            ("Close", ticker): closes,
            ("High", ticker): highs,
            ("Low", ticker): lows,
            ("Volume", ticker): volumes,
        }, index=dates)
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        return df

    def test_returns_correct_format(self):
        """get_stock_price 回傳包含所有欄位的格式化字串。"""
        mock_data = self._make_mock_data(
            "NVDA",
            closes=[160.0, 165.0, 168.0, 170.0, 175.0],
            highs=[162.0, 167.0, 170.0, 172.0, 177.0],
            lows=[158.0, 163.0, 166.0, 168.0, 173.0],
            volumes=[1e8, 1.1e8, 1.2e8, 1.3e8, 1.4e8],
        )

        with patch("src.worker.tools.yf.download", return_value=mock_data):
            from src.worker.tools import get_stock_price
            result = get_stock_price.invoke("NVDA")

        assert "ticker=NVDA" in result
        assert "current_price=" in result
        assert "change_pct=" in result
        assert "volume=" in result
        assert "52w_high=" in result
        assert "52w_low=" in result

    def test_ticker_is_uppercase(self):
        """回傳字串中的 ticker 應為大寫。"""
        mock_data = self._make_mock_data(
            "aapl",
            closes=[150.0, 152.0, 153.0, 154.0, 155.0],
            highs=[152.0, 154.0, 155.0, 156.0, 157.0],
            lows=[148.0, 150.0, 151.0, 152.0, 153.0],
            volumes=[5e7, 5.1e7, 5.2e7, 5.3e7, 5.4e7],
        )

        with patch("src.worker.tools.yf.download", return_value=mock_data):
            from src.worker.tools import get_stock_price
            result = get_stock_price.invoke("aapl")

        assert "ticker=AAPL" in result


class TestGetCompanyNews:
    def test_returns_news_lines(self):
        """get_company_news 回傳包含新聞標題的多行字串。"""
        mock_news = [
            {"headline": "NVDA surges on AI demand", "summary": "NVIDIA shares rose sharply..."},
            {"headline": "Market update", "summary": "Stocks mixed today..."},
        ]

        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = mock_news
            from src.worker.tools import get_company_news
            result = get_company_news.invoke("NVDA")

        assert "NVDA surges on AI demand" in result
        assert "Market update" in result

    def test_sentiment_in_output(self):
        """每行新聞應包含情緒標籤 [positive/negative/neutral]。"""
        mock_news = [{"headline": "NVDA beats earnings", "summary": "Strong results..."}]

        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = mock_news
            from src.worker.tools import get_company_news
            result = get_company_news.invoke("NVDA")

        assert "[neutral]" in result or "[positive]" in result or "[negative]" in result

    def test_sentiment_positive(self):
        """標題含正面關鍵字應分類為 positive。"""
        mock_news = [{"headline": "NVDA surges to record high", "summary": ""}]

        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = mock_news
            from src.worker.tools import get_company_news
            result = get_company_news.invoke("NVDA")

        assert "[positive]" in result

    def test_empty_news(self):
        """無新聞時回傳預設訊息。"""
        with patch("src.worker.tools._finnhub") as mock_client:
            mock_client.company_news.return_value = []
            from src.worker.tools import get_company_news
            result = get_company_news.invoke("NVDA")

        assert "近期無相關新聞" in result
