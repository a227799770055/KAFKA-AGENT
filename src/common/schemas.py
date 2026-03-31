from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class TickerTask(BaseModel):
    """Decomposer 發布到 ticker-tasks 的任務訊息。"""
    schema_version: str = "1.0"
    task_id: str          # 整個查詢的 ID，所有 subtask 共用
    correlation_id: str   # 單一 subtask 的追蹤 ID
    ticker: str           # 股票代碼，強制大寫
    total_subtasks: int   # 此次查詢共有幾個 ticker
    created_at: datetime

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, v: str) -> str:
        return v.upper()


class AnalysisResult(BaseModel):
    """Worker 發布到 analysis-results 的分析結果。"""
    schema_version: str = "1.0"
    task_id: str
    correlation_id: str
    ticker: str
    content: str         # Markdown 格式的單股分析報告
    iterations_used: int
    timestamp: datetime


class AgentThought(BaseModel):
    """所有 Agent 發布到 agent-thoughts 的 ReAct 思考碎片。"""
    task_id: str
    correlation_id: str
    agent_role: Literal["decomposer", "worker", "aggregator"]
    ticker: Optional[str] = None
    step: Literal["THOUGHT", "ACTION", "OBSERVATION"]
    iteration: int
    content: str
    timestamp: datetime


class FinalReport(BaseModel):
    """Aggregator 發布到 final-reports 的最終對比報告。"""
    schema_version: str = "1.0"
    task_id: str
    original_query: str
    tickers: list[str]
    report: str          # Markdown 格式的橫向對比報告
    generated_at: datetime


class DLQMessage(BaseModel):
    """Worker 耗盡重試後發布到 ticker-tasks.DLQ 的失敗訊息。"""
    original_message: TickerTask
    error_type: str
    stack_trace: str
    retry_count: int
    failed_at: datetime


def utcnow() -> datetime:
    """取得當前 UTC 時間，供各 Agent 建立訊息時使用。"""
    return datetime.now(timezone.utc)
