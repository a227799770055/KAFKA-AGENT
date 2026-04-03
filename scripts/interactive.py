"""
互動式 CLI 入口。
啟動後持續等待使用者輸入，支援一般對話與股票分析兩種模式。

使用方式：
  PYTHONPATH=. python scripts/interactive.py

輸入 'exit' 或按 Ctrl+C 結束。
"""
import logging
import time

from configs.kafka_config import TICKER_TASKS_PARTITIONS, TOPIC_AGENT_THOUGHTS, TOPIC_FINAL_REPORTS, TOPIC_TICKER_TASKS
from src.chat.agent import ChatAgent
from src.common.kafka_wrapper import KafkaConsumer, KafkaProducer
from src.common.logging_config import setup_logging
from src.common.redis_client import RedisClient
from src.common.schemas import FinalReport
from src.decomposer.agent import DecomposerAgent

setup_logging()
logger = logging.getLogger(__name__)

POLL_TIMEOUT = 2.0
MAX_WAIT = 120.0


def run_stock_analysis(
    query: str,
    producer: KafkaProducer,
    redis: RedisClient,
    agent: DecomposerAgent,
) -> str | None:
    """
    執行完整的股票分析流程。
    回傳分析報告字串，或 None（逾時 / 無法識別）。
    """
    try:
        tasks, thoughts = agent.run(query)
    except ValueError as e:
        return f"無法識別股票代碼，請確認輸入的股票名稱或代號。\n原因：{e}"

    task_id = tasks[0].task_id
    tickers = [t.ticker for t in tasks]

    redis.init_task(task_id=task_id, total=len(tasks), query=query)

    consumer = KafkaConsumer(
        topics=[TOPIC_FINAL_REPORTS],
        group_id=f"cli-{task_id}",
    )
    consumer.poll(FinalReport, timeout=2.0)  # 觸發 partition assignment

    for thought in thoughts:
        producer.produce(TOPIC_AGENT_THOUGHTS, thought, key=task_id)
    for i, task in enumerate(tasks):
        producer.produce(TOPIC_TICKER_TASKS, task, key=None, partition=i % TICKER_TASKS_PARTITIONS)
    producer.flush()

    print(f"\n分析中：{tickers}，請稍候...\n")

    start = time.monotonic()
    report = None

    try:
        while True:
            if time.monotonic() - start > MAX_WAIT:
                break
            msg = consumer.poll(FinalReport, timeout=POLL_TIMEOUT)
            if msg and msg.task_id == task_id:
                report = msg
                break
    finally:
        consumer.close()

    return report.report if report else None


def main() -> None:
    print("=== Kafka AI Agent ===")
    print("你可以直接對話，或詢問股票分析（例如：最近 NVDA 表現如何？）")
    print("輸入 'exit' 結束。\n")

    producer = KafkaProducer()
    redis = RedisClient()
    decomposer = DecomposerAgent()

    def on_stock_query(query: str) -> str | None:
        return run_stock_analysis(query, producer, redis, decomposer)

    chat_agent = ChatAgent(on_stock_query=on_stock_query)

    try:
        while True:
            try:
                user_input = input("你> ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() == "exit":
                break

            response = chat_agent.chat(user_input)
            print(f"\nAI> {response}\n")

    except KeyboardInterrupt:
        pass
    finally:
        producer.flush()
        print("\n已結束。")


if __name__ == "__main__":
    main()
