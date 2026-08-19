import pytest

from jobfind.retry import QuotaExhausted, _retry_delay_seconds, retry_call


class _FakeError(Exception):
    def __init__(self, headers=None):
        super().__init__("fake error")
        self.headers = headers or {}


def test_retry_delay_honors_retry_after_header():
    error = _FakeError(headers={"Retry-After": "3"})

    delay = _retry_delay_seconds(error, attempt=0, base_delay=2.0, max_delay=60.0)

    assert delay == 3.0


def test_retry_delay_falls_back_to_bounded_exponential_backoff():
    error = _FakeError()

    for attempt in range(6):
        delay = _retry_delay_seconds(error, attempt=attempt, base_delay=2.0, max_delay=10.0)
        assert 0 <= delay <= 10.0


def _flaky(errors, result="success"):
    calls = []

    def fn():
        calls.append(1)
        if errors:
            raise errors.pop(0)
        return result

    fn.calls = calls
    return fn


def test_retry_call_recovers_after_retryable_errors(monkeypatch):
    monkeypatch.setattr("jobfind.retry.time.sleep", lambda s: None)
    fn = _flaky([_FakeError(), _FakeError()])

    result = retry_call(fn, is_retryable=lambda error: True, label="test call")

    assert result == "success"
    assert len(fn.calls) == 3


def test_retry_call_raises_immediately_when_not_retryable():
    fn = _flaky([_FakeError()])

    with pytest.raises(_FakeError):
        retry_call(fn, is_retryable=lambda error: False, label="test call")

    assert len(fn.calls) == 1


def test_retry_call_raises_quota_exhausted_without_retrying():
    fn = _flaky([_FakeError()])

    with pytest.raises(QuotaExhausted) as excinfo:
        retry_call(
            fn,
            is_retryable=lambda error: True,
            is_daily_quota_exhausted=lambda error: True,
            label="test call",
        )

    assert len(fn.calls) == 1
    assert isinstance(excinfo.value.original, _FakeError)


def test_retry_call_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("jobfind.retry.time.sleep", lambda s: None)
    fn = _flaky([_FakeError()] * 5)

    with pytest.raises(_FakeError):
        retry_call(fn, is_retryable=lambda error: True, label="test call", max_retries=2)

    assert len(fn.calls) == 3
