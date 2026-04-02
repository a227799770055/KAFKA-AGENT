import logging
import os
import sys


def setup_logging() -> None:
    """
    設定結構化 JSON logging。
    所有 Agent 在啟動時呼叫一次，統一 log 格式。

    環境變數：
      LOG_LEVEL  — 預設 INFO
      LOG_FILE   — 若設定，同時寫入指定檔案路徑（例如 /app/logs/worker.log）
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = '{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}'
    datefmt = "%Y-%m-%dT%H:%M:%SZ"

    formatter = logging.Formatter(fmt, datefmt=datefmt)
    root = logging.getLogger()
    root.setLevel(log_level)

    # stdout handler（原有行為）
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # 檔案 handler（可選）
    log_file = os.getenv("LOG_FILE")
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
