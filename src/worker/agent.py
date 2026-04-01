import logging
import traceback

from src.common.retry import MaxRetriesExceeded
from src.common.schemas import (
    AgentThought,
    AnalysisResult,
    DLQMessage,
    TickerTask,
    utcnow,
)
from src.worker.tools import generate_summary, get_company_news, get_stock_price

logger = logging.getLogger(__name__)


class WorkerAgent:
    """
    ReAct Loop Worker Agent。

    每次 run() 對應一個 TickerTask，執行 3 個固定迭代：
      Iter 1: get_stock_price
      Iter 2: get_company_news
      Iter 3: generate_summary

    回傳 AnalysisResult（成功）或 DLQMessage（失敗）。
    thoughts 紀錄每步的 THOUGHT / ACTION / OBSERVATION，
    由呼叫方發布到 agent-thoughts topic。
    """

    def run(
        self, task: TickerTask
    ) -> tuple[AnalysisResult | DLQMessage, list[AgentThought]]:
        thoughts: list[AgentThought] = []

        def _thought(iteration: int, step, content: str) -> AgentThought:
            t = AgentThought(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                agent_role="worker",
                ticker=task.ticker,
                step=step,
                iteration=iteration,
                content=content,
                timestamp=utcnow(),
            )
            thoughts.append(t)
            logger.debug("[worker] iter=%d step=%s ticker=%s", iteration, step, task.ticker)
            return t

        try:
            # ── Iteration 1: 取得股價 ──────────────────────────────────────
            _thought(1, "THOUGHT", f"I need to fetch current stock price for {task.ticker}.")
            _thought(1, "ACTION", "get_stock_price")

            price_data = get_stock_price(task.ticker)

            _thought(1, "OBSERVATION", f"price={price_data['current_price']} change={price_data['change_pct']}%")

            # ── Iteration 2: 取得新聞 ──────────────────────────────────────
            _thought(2, "THOUGHT", f"I need recent news to understand market sentiment for {task.ticker}.")
            _thought(2, "ACTION", "get_company_news")

            news_data = get_company_news(task.ticker)

            _thought(2, "OBSERVATION", f"fetched {len(news_data)} articles")

            # ── Iteration 3: 產生摘要 ──────────────────────────────────────
            _thought(3, "THOUGHT", "I have price and news data. Generating Markdown summary.")
            _thought(3, "ACTION", "generate_summary")

            summary = generate_summary(task.ticker, price_data, news_data)

            _thought(3, "OBSERVATION", f"summary generated ({len(summary)} chars)")

            result = AnalysisResult(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                ticker=task.ticker,
                content=summary,
                iterations_used=3,
                timestamp=utcnow(),
            )
            logger.info("[worker] completed ticker=%s task_id=%s", task.ticker, task.task_id)
            return result, thoughts

        except MaxRetriesExceeded as exc:
            logger.error(
                "[worker] MaxRetriesExceeded ticker=%s task_id=%s error=%s",
                task.ticker, task.task_id, exc,
            )
            dlq = DLQMessage(
                original_message=task,
                error_type=type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__,
                stack_trace=traceback.format_exc(),
                retry_count=3,
                failed_at=utcnow(),
            )
            return dlq, thoughts
