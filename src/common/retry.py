import logging
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


class MaxRetriesExceeded(Exception):
    """耗盡所有重試次數後拋出，供 Worker 捕捉並發送到 DLQ。"""
    pass


def with_retry(max_retries: int = 3, base_delay: float = 1.0):
    """
    指數退避 Retry 裝飾器。

    等待時間：base_delay * (2 ** attempt)
      第 1 次失敗 → 等 1s → 重試
      第 2 次失敗 → 等 2s → 重試
      第 3 次失敗 → 等 4s → 拋出 MaxRetriesExceeded
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay}s — {type(e).__name__}: {e}"
                    )
                    time.sleep(delay)

            raise MaxRetriesExceeded(
                f"{func.__name__} failed after {max_retries} retries"
            ) from last_exception

        return wrapper  # type: ignore
    return decorator
