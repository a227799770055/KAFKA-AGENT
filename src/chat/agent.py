import logging
import os
from typing import Annotated, Callable, Literal

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from configs.prompts import CHAT_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────────────────

class ChatState(TypedDict):
    messages: Annotated[list, add_messages]   # 對話歷史，add_messages 自動 append
    response: str                             # 最終回傳給使用者的文字
    _on_stock_query: Callable                 # callback，注入後固定不變


# ── LLM + Tool 定義 ────────────────────────────────────────────────────────

@tool
def analyze_stocks(query: str) -> str:
    """
    呼叫時機：使用者明確或隱含地想了解特定股票的價格、走勢、新聞、或投資建議。
    不呼叫時機：使用者只是提到股票作為話題、詢問股票概念定義、或進行一般閒聊。
    """
    return query  # 實際執行由 analyze_node 負責，這裡只是宣告


def _build_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GEMINI_API_KEY"),  # langchain 接受此參數
    ).bind_tools([analyze_stocks])


# ── Nodes ──────────────────────────────────────────────────────────────────

def intent_node(state: ChatState) -> dict:
    """呼叫 Gemini，判斷意圖：一般對話或股票分析。"""
    llm = _build_llm()
    from langchain_core.messages import SystemMessage
    messages = [SystemMessage(content=CHAT_SYSTEM_INSTRUCTION)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}


def analyze_node(state: ChatState) -> dict:
    """取出 tool_call 的 query，執行 Kafka pipeline，回傳報告。"""
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    query = tool_call["args"]["query"]
    tool_call_id = tool_call["id"]

    logger.info("[chat] analyze_stocks triggered query=%s", query)

    report = state["_on_stock_query"](query)
    result = report if report else "分析逾時，請稍後再試。"

    # 補上 ToolMessage 讓 Gemini 知道 tool 執行完畢
    return {
        "messages": [ToolMessage(content="分析完成，報告已顯示給使用者。", tool_call_id=tool_call_id)],
        "response": result,
    }


def respond_node(state: ChatState) -> dict:
    """一般對話，直接取 LLM 回覆文字。"""
    last_msg = state["messages"][-1]
    return {"response": last_msg.content}


# ── 路由邏輯 ───────────────────────────────────────────────────────────────

def route(state: ChatState) -> Literal["analyze", "respond"]:
    last_msg = state["messages"][-1]
    if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
        return "analyze"
    return "respond"


# ── Graph 組裝 ────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("intent", intent_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("respond", respond_node)

    graph.set_entry_point("intent")
    graph.add_conditional_edges("intent", route, {
        "analyze": "analyze",
        "respond": "respond",
    })
    graph.add_edge("analyze", END)
    graph.add_edge("respond", END)

    return graph.compile()


# ── 公開介面 ───────────────────────────────────────────────────────────────

class ChatAgent:
    """
    前端對話路由器（LangGraph 實作）。

    維護多輪對話歷史（messages），使用 Gemini Tool Use 判斷是否觸發股票分析：
    - 一般對話 → LLM 直接回覆
    - 股票分析意圖 → 呼叫 on_stock_query(query) callback，回傳分析報告

    on_stock_query: 接收使用者原始輸入，回傳分析報告字串（或 None 代表逾時）
    """

    def __init__(self, on_stock_query: Callable[[str], str | None]):
        self._graph = build_graph()
        self._on_stock_query = on_stock_query
        self._messages = []   # 跨輪維護對話歷史

    def chat(self, user_input: str) -> str:
        self._messages.append(HumanMessage(content=user_input))

        result = self._graph.invoke({
            "messages": self._messages,
            "response": "",
            "_on_stock_query": self._on_stock_query,
        })

        # 更新歷史（取 graph 執行後的完整 messages）
        self._messages = result["messages"]

        return result["response"]
