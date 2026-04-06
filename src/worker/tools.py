import logging
import os
from datetime import datetime, timezone

import finnhub
import yfinance as yf
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from configs.prompts import WORKER_SUMMARY_PROMPT
from datetime import timedelta

load_dotenv()

logger = logging.getLogger(__name__)

# ── Clients ────────────────────────────────────────────────────────────────
_gemini = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=os.getenv("GEMINI_API_KEY"), timeout=30)
_finnhub = finnhub.Client(api_key=os.getenv("FINNHUB_API_KEY"))


# ── Tools ──────────────────────────────────────────────────────────────────

@tool
def get_stock_price(ticker: str) -> str:
    """
    取得指定股票代碼的最新價格數據，包含當前價格、漲跌幅、成交量、52 週高低點。
    請傳入標準英文股票代碼，例如 NVDA、TSLA、TSM。
    """
    df_1y = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True, timeout=10)

    if df_1y.empty:
        raise ValueError(f"No price data returned for {ticker}")

    df = df_1y.tail(2)
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

    current_price = float(latest["Close"].iloc[0]) if hasattr(latest["Close"], "iloc") else float(latest["Close"])
    prev_close = float(prev["Close"].iloc[0]) if hasattr(prev["Close"], "iloc") else float(prev["Close"])
    change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
    volume = float(latest["Volume"].iloc[0]) if hasattr(latest["Volume"], "iloc") else float(latest["Volume"])

    w52_high = float(df_1y["High"].max().iloc[0]) if hasattr(df_1y["High"].max(), "iloc") else float(df_1y["High"].max())
    w52_low = float(df_1y["Low"].min().iloc[0]) if hasattr(df_1y["Low"].min(), "iloc") else float(df_1y["Low"].min())

    result = (
        f"ticker={ticker.upper()}, current_price={round(current_price, 2)}, "
        f"change_pct={round(change_pct, 2)}%, volume={int(volume)}, "
        f"52w_high={round(w52_high, 2)}, 52w_low={round(w52_low, 2)}"
    )

    logger.info("[tools] get_stock_price: %s = $%s", ticker, round(current_price, 2))
    return result


@tool
def get_company_news(ticker: str) -> str:
    """
    取得指定股票代碼近 7 天的公司新聞（最多 5 筆），包含標題、摘要與情緒分類。
    請傳入標準英文股票代碼，例如 NVDA、TSLA、TSM。
    """

    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    raw_news = _finnhub.company_news(ticker.upper(), _from=date_from, to=date_to)

    lines = []
    for item in raw_news[:5]:
        summary = item.get("summary") or item.get("headline", "")
        sentiment = _simple_sentiment(item.get("headline", ""))
        lines.append(
            f"- [{sentiment}] {item.get('headline', '')} | {summary[:200]}"
        )

    result = "\n".join(lines) if lines else "（近期無相關新聞）"

    logger.info("[tools] get_company_news: %s = %d articles", ticker, len(lines))
    return result


@tool
def generate_summary(ticker: str, price_data: str, news_data: str) -> str:
    """
    根據股價數據與新聞資料，產生 Markdown 格式的單股分析摘要。
    price_data: get_stock_price 的回傳結果。
    news_data: get_company_news 的回傳結果。
    """
    prompt = WORKER_SUMMARY_PROMPT.format(
        ticker=ticker,
        price_data=price_data,
        news_data=news_data,
    )

    response = _gemini.invoke(prompt)
    summary = response.content.strip()

    logger.info("[tools] generate_summary: %s done (%d chars)", ticker, len(summary))
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
