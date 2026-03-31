import logging
import os
import sys


def setup_logging() -> None:
    """
    設定結構化 JSON logging。
    所有 Agent 在啟動時呼叫一次，統一 log 格式。
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=log_level,
        format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )
