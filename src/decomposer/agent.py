import json
import logging
import os
import uuid

import google.generativeai as genai
from dotenv import load_dotenv

from configs.prompts import DECOMPOSER_PROMPT
from src.common.schemas import AgentThought, TickerTask, utcnow

load_dotenv()

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_gemini = genai.GenerativeModel("gemini-2.5-flash")


class DecomposerAgent:
    """
    ReAct Loop Decomposer Agent。

    接收自然語言查詢，透過 Gemini 解析出股票代碼清單，
    建立 TickerTask 列表供 main.py 發布到 Kafka。

    Iter 1: 呼叫 Gemini 解析 tickers
    Iter 2: 建立 TickerTask 物件清單
    """

    def run(
        self, query: str
    ) -> tuple[list[TickerTask], list[AgentThought]]:
        task_id = str(uuid.uuid4())
        thoughts: list[AgentThought] = []

        def _thought(iteration: int, step: str, content: str) -> None:
            t = AgentThought(
                task_id=task_id,
                correlation_id=task_id,  # Decomposer 本身沒有 subtask，用 task_id 代替
                agent_role="decomposer",
                ticker=None,
                step=step,
                iteration=iteration,
                content=content,
                timestamp=utcnow(),
            )
            thoughts.append(t)
            logger.debug("[decomposer] iter=%d step=%s", iteration, step)

        # ── Iteration 1: 解析 tickers ──────────────────────────────────
        _thought(1, "THOUGHT", f"I need to extract stock tickers from the query: {query!r}")
        _thought(1, "ACTION", "call_gemini_decomposer")

        tickers = self._parse_tickers(query)

        _thought(1, "OBSERVATION", f"extracted tickers: {tickers}")

        # ── Iteration 2: 建立 TickerTask 清單 ─────────────────────────
        _thought(2, "THOUGHT", f"Creating {len(tickers)} TickerTask(s) with shared task_id={task_id}")
        _thought(2, "ACTION", "build_ticker_tasks")

        tasks = [
            TickerTask(
                task_id=task_id,
                correlation_id=str(uuid.uuid4()),
                ticker=ticker,
                total_subtasks=len(tickers),
                created_at=utcnow(),
            )
            for ticker in tickers
        ]

        _thought(2, "OBSERVATION", f"built {len(tasks)} tasks: {[t.ticker for t in tasks]}")

        logger.info("[decomposer] query=%r tickers=%s task_id=%s", query, tickers, task_id)
        return tasks, thoughts

    def _parse_tickers(self, query: str) -> list[str]:
        """呼叫 Gemini 解析股票代碼，去重並回傳大寫清單。"""
        prompt = DECOMPOSER_PROMPT.format(query=query)
        response = _gemini.generate_content(prompt)
        text = response.text.strip()

        try:
            tickers = json.loads(text)
        except json.JSONDecodeError:
            # Gemini 有時會在 JSON 外包多餘文字，嘗試擷取第一個 [...] 區塊
            start = text.find("[")
            end = text.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError(f"Gemini returned unparseable response: {text!r}")
            tickers = json.loads(text[start:end])

        if not isinstance(tickers, list) or len(tickers) == 0:
            raise ValueError(f"No tickers found in query: {query!r}")

        # 去重（保持順序）、確保大寫
        seen = {}
        return [seen.setdefault(t.upper(), t.upper()) for t in tickers if t.upper() not in seen]
