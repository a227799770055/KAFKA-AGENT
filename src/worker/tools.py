import logging
import os
from datetime import datetime, timezone

import finnhub
import google.generativeai as genai
import yfinance as yf
from dotenv import load_dotenv

from configs.prompts import WORKER_SUMMARY_PROMPT
from src.common.retry import with_retry

load_dotenv()

logger = logging.getLogger(__name__)

# ── Clients ────────────────────────────────────────────────────────────────
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_gemini = genai.GenerativeModel("gemini-2.5-flash")
_finnhub = finnhub.Client(api_key=os.getenv("FINNHUB_API_KEY"))


# ── Tools ──────────────────────────────────────────────────────────────────

@with_retry(max_retries=3)
def get_stock_price(ticker: str) -> dict:
    """
    使用 yfinance 取得股票數據。
    用 download() 取得 OHLCV，用 fast_info 取得 52 週高低點。

    回傳格式：
    {
      "ticker": "NVDA",
      "current_price": 875.4,
      "change_pct": 2.3,
      "volume": 45000000,
      "52w_high": 974.0,
      "52w_low": 402.0
    }
    """
    df = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)

    if df.empty:
        raise ValueError(f"No price data returned for {ticker}")

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

    current_price = float(latest["Close"].iloc[0]) if hasattr(latest["Close"], "iloc") else float(latest["Close"])
    prev_close = float(prev["Close"].iloc[0]) if hasattr(prev["Close"], "iloc") else float(prev["Close"])
    change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
    volume = float(latest["Volume"].iloc[0]) if hasattr(latest["Volume"], "iloc") else float(latest["Volume"])

    # 52 週高低點用 1y 數據計算
    df_1y = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
    w52_high = float(df_1y["High"].max().iloc[0]) if hasattr(df_1y["High"].max(), "iloc") else float(df_1y["High"].max())
    w52_low = float(df_1y["Low"].min().iloc[0]) if hasattr(df_1y["Low"].min(), "iloc") else float(df_1y["Low"].min())

    result = {
        "ticker": ticker.upper(),
        "current_price": round(current_price, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(volume),
        "52w_high": round(w52_high, 2),
        "52w_low": round(w52_low, 2),
    }

    logger.info(f"[tools] get_stock_price: {ticker} = ${result['current_price']}")
    return result


@with_retry(max_retries=3)
def get_company_news(ticker: str, days: int = 7) -> list[dict]:
    """
    使用 Finnhub 取得近期公司新聞。

    回傳格式：
    [{"title": "...", "summary": "...", "sentiment": "positive|neutral|negative"}]
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    raw_news = _finnhub.company_news(ticker.upper(), _from=date_from, to=date_to)

    news = []
    for item in raw_news[:5]:  # 最多取 5 筆
        summary = item.get("summary") or item.get("headline", "")
        news.append({
            "title": item.get("headline", ""),
            "summary": summary[:300],  # 避免 prompt 過長
            "sentiment": _simple_sentiment(item.get("headline", "")),
        })

    logger.info(f"[tools] get_company_news: {ticker} = {len(news)} articles")
    return news


def generate_summary(ticker: str, price_data: dict, news_data: list[dict]) -> str:
    """
    呼叫 Gemini 產出 Markdown 格式的單股分析摘要。
    """
    prompt = WORKER_SUMMARY_PROMPT.format(
        ticker=ticker,
        price_data=price_data,
        news_data=news_data,
    )

    response = _gemini.generate_content(prompt)
    summary = response.text.strip()

    logger.info(f"[tools] generate_summary: {ticker} done ({len(summary)} chars)")
    return summary


# ── 內部輔助 ───────────────────────────────────────────────────────────────

def _simple_sentiment(text: str) -> str:
    """根據標題關鍵字做簡單的情緒分類。"""
    text_lower = text.lower()
    positive_words = {"surge", "soar", "beat", "gain", "rise", "jump", "up", "high", "record", "growth"}
    negative_words = {"fall", "drop", "miss", "lose", "down", "cut", "low", "crash", "decline", "loss"}

    pos = sum(1 for w in positive_words if w in text_lower)
    neg = sum(1 for w in negative_words if w in text_lower)

    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"
