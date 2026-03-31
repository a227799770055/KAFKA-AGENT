import time

import pytest

from src.common.retry import MaxRetriesExceeded, with_retry


def test_raises_after_max_retries():
    """重試 3 次後仍失敗，應拋出 MaxRetriesExceeded。"""
    call_count = 0

    @with_retry(max_retries=3, base_delay=0.01)
    def always_fails():
        nonlocal call_count
        call_count += 1
        raise ValueError("always fail")

    with pytest.raises(MaxRetriesExceeded):
        always_fails()

    assert call_count == 3


def test_exponential_backoff_delays():
    """延遲時間應符合指數退避：1x、2x、4x base_delay。"""
    delays = []
    original_sleep = time.sleep

    def mock_sleep(seconds):
        delays.append(seconds)

    @with_retry(max_retries=3, base_delay=1.0)
    def always_fails():
        raise RuntimeError("fail")

    time.sleep = mock_sleep
    try:
        always_fails()
    except MaxRetriesExceeded:
        pass
    finally:
        time.sleep = original_sleep

    assert len(delays) == 3
    assert delays[0] == pytest.approx(1.0)
    assert delays[1] == pytest.approx(2.0)
    assert delays[2] == pytest.approx(4.0)


def test_no_retry_on_success():
    """成功時不應重試，只執行一次。"""
    call_count = 0

    @with_retry(max_retries=3, base_delay=0.01)
    def always_succeeds():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = always_succeeds()

    assert result == "ok"
    assert call_count == 1
