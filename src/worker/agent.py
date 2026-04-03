import logging
import os
import traceback

from typing import Annotated, Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from configs.prompts import WORKER_SYSTEM_PROMPT
from src.common.schemas import (
    AgentThought,
    AnalysisResult,
    DLQMessage,
    TickerTask,
    utcnow,
)
from src.worker.tools import generate_summary, get_company_news, get_stock_price

logger = logging.getLogger(__name__)

_TOOLS = [get_stock_price, get_company_news, generate_summary]


# ── State ──────────────────────────────────────────────────────────────────

class WorkerState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Nodes ──────────────────────────────────────────────────────────────────

def _build_llm():
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GEMINI_API_KEY"),
    ).bind_tools(_TOOLS)


def llm_node(state: WorkerState) -> dict:
    llm = _build_llm()
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


# ── Router ─────────────────────────────────────────────────────────────────

def route(state: WorkerState) -> Literal["tools", "__end__"]:
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "tools"
    return "__end__"


# ── Graph ──────────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(WorkerState)
    graph.add_node("llm", llm_node)
    graph.add_node("tools", ToolNode(_TOOLS))

    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route, {
        "tools": "tools",
        "__end__": END,
    })
    graph.add_edge("tools", "llm")

    return graph.compile()


class WorkerAgent:
    """
    LangGraph ReAct Worker Agent。

    每次 run() 對應一個 TickerTask，由 LLM 自主依序呼叫：
      get_stock_price → get_company_news → generate_summary

    回傳 AnalysisResult（成功）或 DLQMessage（失敗）。
    thoughts 從 messages 中擷取，由呼叫方發布到 agent-thoughts topic。
    """

    def __init__(self):
        self._agent = build_graph()

    def run(
        self, task: TickerTask
    ) -> tuple[AnalysisResult | DLQMessage, list[AgentThought]]:
        thoughts: list[AgentThought] = []

        try:
            result = self._agent.invoke({
                "messages": [
                    SystemMessage(content=WORKER_SYSTEM_PROMPT),
                    {"role": "user", "content": f"請分析股票：{task.ticker}"},
                ]
            })

            for msg in result["messages"]:
                logger.debug("[worker] message type=%s content=%s", type(msg).__name__, msg)

            thoughts = _extract_thoughts(result["messages"], task)
            summary = _extract_summary(result["messages"])

            analysis = AnalysisResult(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                ticker=task.ticker,
                content=summary,
                iterations_used=len([m for m in result["messages"] if isinstance(m, ToolMessage)]),
                timestamp=utcnow(),
            )
            logger.info("[worker] completed ticker=%s task_id=%s", task.ticker, task.task_id)
            return analysis, thoughts

        except Exception as exc:
            logger.error(
                "[worker] failed ticker=%s task_id=%s error=%s",
                task.ticker, task.task_id, exc,
            )
            dlq = DLQMessage(
                original_message=task,
                error_type=type(exc).__name__,
                stack_trace=traceback.format_exc(),
                retry_count=0,
                failed_at=utcnow(),
            )
            return dlq, thoughts


# ── 輔助函式 ───────────────────────────────────────────────────────────────

def _extract_thoughts(messages: list, task: TickerTask) -> list[AgentThought]:
    """從 LangGraph messages 中建立 AgentThought 列表。"""
    thoughts = []
    iteration = 0

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            iteration += 1
            for tc in msg.tool_calls:
                thoughts.append(AgentThought(
                    task_id=task.task_id,
                    correlation_id=task.correlation_id,
                    agent_role="worker",
                    ticker=task.ticker,
                    step="ACTION",
                    iteration=iteration,
                    content=f"{tc['name']}({tc['args']})",
                    timestamp=utcnow(),
                ))
        elif isinstance(msg, ToolMessage):
            thoughts.append(AgentThought(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                agent_role="worker",
                ticker=task.ticker,
                step="OBSERVATION",
                iteration=iteration,
                content=msg.content[:300],
                timestamp=utcnow(),
            ))

    return thoughts


def _extract_summary(messages: list) -> str:
    """取最後一則 AIMessage 的文字內容作為分析報告。"""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, list):
                return "".join(
                    part["text"] for part in content
                    if isinstance(part, dict) and "text" in part
                )
            return content
    return "（無法產生分析報告）"
