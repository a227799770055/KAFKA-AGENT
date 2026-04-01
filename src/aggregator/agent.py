import logging
import os

import google.generativeai as genai
from dotenv import load_dotenv

from configs.prompts import AGGREGATOR_PROMPT
from src.common.redis_client import RedisClient
from src.common.schemas import AgentThought, AnalysisResult, FinalReport, utcnow

load_dotenv()

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_gemini = genai.GenerativeModel("gemini-2.5-flash")


class AggregatorAgent:
    """
    ReAct Loop Aggregator Agent。

    每次 run() 接收一筆 AnalysisResult，從 Redis 讀取狀態後判斷是否所有 subtask 完成。
    注意：push_result / increment_completed 由 Worker 負責，Aggregator 只負責讀取。

    Iter 1: 確認是否所有 subtask 完成
    Iter 2: 嘗試取得 synthesis_lock（防止重複合成）
    Iter 3: 呼叫 Gemini 產生橫向對比報告

    回傳 FinalReport（合成完成）或 None（尚未完成 / 未取到鎖）。
    """

    def run(
        self,
        result: AnalysisResult,
        redis: RedisClient,
    ) -> tuple[FinalReport | None, list[AgentThought]]:
        task_id = result.task_id
        thoughts: list[AgentThought] = []

        def _thought(iteration: int, step: str, content: str) -> None:
            t = AgentThought(
                task_id=task_id,
                correlation_id=result.correlation_id,
                agent_role="aggregator",
                ticker=result.ticker,
                step=step,
                iteration=iteration,
                content=content,
                timestamp=utcnow(),
            )
            thoughts.append(t)
            logger.debug("[aggregator] iter=%d step=%s task_id=%s", iteration, step, task_id)

        # ── Iteration 1: 確認是否所有 subtask 完成 ────────────────────
        _thought(1, "THOUGHT", f"Checking if all subtasks are done for task_id={task_id}.")
        _thought(1, "ACTION", "check_completion")

        meta = redis.get_task_meta(task_id)
        total = int(meta["total"]) if meta else result.total_subtasks
        completed = redis.get_completed(task_id)

        _thought(1, "OBSERVATION", f"completed={completed} total={total}")

        if completed < total:
            logger.info(
                "[aggregator] waiting ticker=%s completed=%d/%d",
                result.ticker, completed, total,
            )
            return None, thoughts

        # ── Iteration 2: 嘗試取得 synthesis_lock ──────────────────────
        _thought(2, "THOUGHT", "All subtasks done. Attempting to acquire synthesis lock.")
        _thought(2, "ACTION", "acquire_synthesis_lock")

        locked = redis.acquire_synthesis_lock(task_id)

        _thought(2, "OBSERVATION", f"lock_acquired={locked}")

        if not locked:
            logger.info("[aggregator] lock not acquired, skipping task_id=%s", task_id)
            return None, thoughts

        # ── Iteration 3: 呼叫 Gemini 產生對比報告 ─────────────────────
        _thought(3, "THOUGHT", "Lock acquired. Reading all results and generating final report.")
        _thought(3, "ACTION", "generate_final_report")

        query = meta["query"] if meta else ""
        raw_results = redis.get_results(task_id)
        tickers = redis.get_tickers(task_id)

        report_text = self._generate_report(query, raw_results)

        _thought(3, "OBSERVATION", f"report generated ({len(report_text)} chars)")

        final_report = FinalReport(
            task_id=task_id,
            original_query=query,
            tickers=sorted(tickers),
            report=report_text,
            generated_at=utcnow(),
        )

        logger.info("[aggregator] final report generated task_id=%s tickers=%s", task_id, tickers)
        return final_report, thoughts

    def _generate_report(self, query: str, raw_results: list[str]) -> str:
        """呼叫 Gemini 產生橫向對比報告。"""
        analyses = "\n\n---\n\n".join(raw_results)
        prompt = AGGREGATOR_PROMPT.format(query=query, analyses=analyses)
        response = _gemini.generate_content(prompt)
        return response.text.strip()
