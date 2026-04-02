import logging
import os
from typing import Callable

import google.generativeai as genai

from configs.prompts import CHAT_SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

_ANALYZE_STOCKS_TOOL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="analyze_stocks",
            description=(
                "呼叫時機：使用者明確或隱含地想了解特定股票的價格、走勢、新聞、或投資建議。\n"
                "不呼叫時機：使用者只是提到股票作為話題、詢問股票概念定義、或進行一般閒聊。"
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "query": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="使用者的原始輸入，包含股票代碼或公司名稱",
                    )
                },
                required=["query"],
            ),
        )
    ]
)



class ChatAgent:
    """
    前端對話路由器。

    維護多輪對話歷史，使用 Gemini Function Calling 判斷是否觸發股票分析流程：
    - 一般對話 → LLM 直接回覆
    - 股票分析意圖 → 呼叫 on_stock_query(query) callback，回傳分析報告

    on_stock_query: 接收使用者原始輸入，回傳分析報告字串（或 None 代表逾時）
    """

    def __init__(self, on_stock_query: Callable[[str], str | None]):
        self._on_stock_query = on_stock_query
        self._model = genai.GenerativeModel(
            "gemini-2.5-flash",
            tools=[_ANALYZE_STOCKS_TOOL],
            system_instruction=CHAT_SYSTEM_INSTRUCTION,
        )
        self._chat = self._model.start_chat(history=[])

    def chat(self, user_input: str) -> str:
        """
        處理一輪使用者輸入。

        回傳：LLM 回覆文字（一般對話）或分析報告（股票分析）。
        """
        response = self._chat.send_message(user_input)
        part = response.candidates[0].content.parts[0]

        if part.function_call.name == "analyze_stocks":
            query = dict(part.function_call.args).get("query", user_input)
            logger.info("[chat] analyze_stocks triggered query=%s", query)

            report = self._on_stock_query(query)
            
            return report if report else "分析逾時，請稍後再試。"

        return part.text
