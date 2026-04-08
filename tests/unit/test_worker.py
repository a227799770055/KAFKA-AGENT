from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.common.schemas import AnalysisResult, DLQMessage, TickerTask, utcnow
from src.worker.agent import WorkerAgent


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def task():
    return TickerTask(
        task_id="task-001",
        correlation_id="corr-001",
        ticker="NVDA",
        total_subtasks=1,
        created_at=utcnow(),
    )


def _make_agent(invoke_return=None, invoke_side_effect=None) -> WorkerAgent:
    """建立 WorkerAgent 並將 _agent 替換成 mock，避免真實 LLM 呼叫。"""
    agent = WorkerAgent.__new__(WorkerAgent)
    mock_graph = MagicMock()
    if invoke_side_effect:
        mock_graph.invoke.side_effect = invoke_side_effect
    else:
        mock_graph.invoke.return_value = invoke_return
    agent._agent = mock_graph
    return agent


def _success_messages() -> dict:
    """
    模擬 3 輪 ReAct loop 的正常訊息序列：
      iter1: get_stock_price  → ACTION + OBSERVATION
      iter2: get_company_news → ACTION + OBSERVATION
      iter3: generate_summary → ACTION + OBSERVATION
      最後一則 AIMessage 含最終報告文字。
    """
    return {
        "messages": [
            AIMessage(content="", tool_calls=[{
                "name": "get_stock_price", "args": {"ticker": "NVDA"}, "id": "tc1", "type": "tool_call",
            }]),
            ToolMessage(
                content="ticker=NVDA, current_price=875.4, change_pct=2.3%, volume=45000000, 52w_high=974.0, 52w_low=402.0",
                tool_call_id="tc1",
            ),
            AIMessage(content="", tool_calls=[{
                "name": "get_company_news", "args": {"ticker": "NVDA"}, "id": "tc2", "type": "tool_call",
            }]),
            ToolMessage(
                content="- [positive] NVDA surges on AI demand | Strong results...",
                tool_call_id="tc2",
            ),
            AIMessage(content="", tool_calls=[{
                "name": "generate_summary",
                "args": {"ticker": "NVDA", "price_data": "...", "news_data": "..."},
                "id": "tc3",
                "type": "tool_call",
            }]),
            ToolMessage(content="## NVDA Analysis", tool_call_id="tc3"),
            AIMessage(content="## NVDA Analysis"),
        ]
    }


# ── 成功路徑 ───────────────────────────────────────────────────────────────

class TestWorkerAgentSuccess:
    def test_returns_analysis_result(self, task):
        """成功時回傳 AnalysisResult，ticker 與 task_id 正確。"""
        agent = _make_agent(invoke_return=_success_messages())
        result, _ = agent.run(task)

        assert isinstance(result, AnalysisResult)
        assert result.ticker == "NVDA"
        assert result.task_id == "task-001"
        assert result.content == "## NVDA Analysis"
        assert result.iterations_used == 3

    def test_thoughts_count(self, task):
        """ReAct Loop 應產生 6 個 thought（每輪 ACTION + OBSERVATION x3）。"""
        agent = _make_agent(invoke_return=_success_messages())
        _, thoughts = agent.run(task)

        assert len(thoughts) == 6

    def test_thoughts_steps_sequence(self, task):
        """thoughts 的 step 順序應為 ACTION → OBSERVATION（x3）。"""
        agent = _make_agent(invoke_return=_success_messages())
        _, thoughts = agent.run(task)

        steps = [t.step for t in thoughts]
        assert steps == [
            "ACTION", "OBSERVATION",
            "ACTION", "OBSERVATION",
            "ACTION", "OBSERVATION",
        ]

    def test_thoughts_iterations(self, task):
        """每輪 iteration 編號應為 1、2、3。"""
        agent = _make_agent(invoke_return=_success_messages())
        _, thoughts = agent.run(task)

        iterations = [t.iteration for t in thoughts]
        assert iterations == [1, 1, 2, 2, 3, 3]

    def test_thoughts_agent_role(self, task):
        """所有 thought 的 agent_role 應為 worker。"""
        agent = _make_agent(invoke_return=_success_messages())
        _, thoughts = agent.run(task)

        assert all(t.agent_role == "worker" for t in thoughts)


# ── 失敗路徑 ───────────────────────────────────────────────────────────────

class TestWorkerAgentFailure:
    def test_returns_dlq_on_exception(self, task):
        """_agent.invoke 拋出例外時應回傳 DLQMessage。"""
        agent = _make_agent(invoke_side_effect=Exception("yf failed"))
        result, _ = agent.run(task)

        assert isinstance(result, DLQMessage)
        assert result.original_message.ticker == "NVDA"
        assert result.error_type == "Exception"

    def test_dlq_contains_stack_trace(self, task):
        """DLQMessage 應包含非空的 stack_trace。"""
        agent = _make_agent(invoke_side_effect=Exception("yf failed"))
        result, _ = agent.run(task)

        assert isinstance(result, DLQMessage)
        assert isinstance(result.stack_trace, str)
        assert len(result.stack_trace) > 0

    def test_thoughts_empty_on_immediate_failure(self, task):
        """invoke 直接失敗時 thoughts 為空（還未產生任何訊息）。"""
        agent = _make_agent(invoke_side_effect=Exception("yf failed"))
        _, thoughts = agent.run(task)

        assert thoughts == []

    def test_dlq_preserves_original_task(self, task):
        """DLQMessage 應保留原始 TickerTask 的 task_id 與 ticker。"""
        agent = _make_agent(invoke_side_effect=Exception("something failed"))
        result, _ = agent.run(task)

        assert isinstance(result, DLQMessage)
        assert result.original_message.task_id == "task-001"
        assert result.original_message.ticker == "NVDA"
