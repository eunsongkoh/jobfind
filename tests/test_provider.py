import httpx
import pytest

from jobfind.scoring.provider import (
    FallbackProvider,
    GoogleAIProvider,
    OpenAICompatibleProvider,
    _RateLimiter,
    _is_retryable,
    _retry_delay_seconds,
)


class _FakeError(Exception):
    def __init__(self, status_code=None, headers=None):
        super().__init__("fake error")
        self.status_code = status_code
        self.headers = headers or {}


def test_is_retryable_for_rate_limit_and_server_errors():
    assert _is_retryable(_FakeError(status_code=429))
    assert _is_retryable(_FakeError(status_code=503))


def test_is_retryable_false_for_client_errors():
    assert not _is_retryable(_FakeError(status_code=400))
    assert not _is_retryable(_FakeError(status_code=401))
    assert not _is_retryable(ValueError("not an http error"))


def test_is_retryable_true_for_transport_errors():
    assert _is_retryable(httpx.ConnectError("connection refused"))


def test_retry_delay_honors_retry_after_header():
    error = _FakeError(status_code=429, headers={"Retry-After": "3"})

    delay = _retry_delay_seconds(error, attempt=0, base_delay=2.0, max_delay=60.0)

    assert delay == 3.0


def test_retry_delay_falls_back_to_bounded_exponential_backoff():
    error = _FakeError(status_code=503)

    for attempt in range(6):
        delay = _retry_delay_seconds(error, attempt=attempt, base_delay=2.0, max_delay=10.0)
        assert 0 <= delay <= 10.0


def test_rate_limiter_throttles_once_limit_reached(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("jobfind.scoring.provider.time.monotonic", lambda: clock[0])
    sleeps = []
    monkeypatch.setattr("jobfind.scoring.provider.time.sleep", lambda s: sleeps.append(s))

    limiter = _RateLimiter(requests_per_minute=2)
    limiter.wait()
    clock[0] = 1.0
    limiter.wait()
    clock[0] = 2.0
    limiter.wait()

    assert sleeps == [58.0]


class _FlakyClient:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    class _Interactions:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls += 1
            if self.outer.errors:
                raise self.outer.errors.pop(0)
            return "success"

    @property
    def interactions(self):
        return self._Interactions(self)


def _provider_with_fake_client(errors):
    provider = GoogleAIProvider(api_key="test-key", model="fake-model")
    provider.client = _FlakyClient(errors)
    return provider


def test_create_with_retry_recovers_after_retryable_errors(monkeypatch):
    monkeypatch.setattr("jobfind.scoring.provider.time.sleep", lambda s: None)
    provider = _provider_with_fake_client([_FakeError(status_code=429), _FakeError(status_code=503)])

    result = provider._create_with_retry({})

    assert result == "success"
    assert provider.client.calls == 3


def test_create_with_retry_raises_immediately_on_non_retryable_error(monkeypatch):
    monkeypatch.setattr("jobfind.scoring.provider.time.sleep", lambda s: None)
    provider = _provider_with_fake_client([_FakeError(status_code=400)])

    with pytest.raises(_FakeError):
        provider._create_with_retry({})

    assert provider.client.calls == 1


def test_create_with_retry_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("jobfind.scoring.provider.time.sleep", lambda s: None)
    monkeypatch.setattr("jobfind.scoring.provider._MAX_RETRIES", 2)
    provider = _provider_with_fake_client([_FakeError(status_code=429)] * 5)

    with pytest.raises(_FakeError):
        provider._create_with_retry({})

    assert provider.client.calls == 3


class _FlakyChatClient:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    class _Completions:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls += 1
            if self.outer.errors:
                raise self.outer.errors.pop(0)
            return "success"

    class _Chat:
        def __init__(self, outer):
            self.outer = outer

        @property
        def completions(self):
            return _FlakyChatClient._Completions(self.outer)

    @property
    def chat(self):
        return self._Chat(self)


def _openai_compatible_provider_with_fake_client(errors):
    provider = OpenAICompatibleProvider(api_key="test-key", model="fake-model", base_url="http://fake")
    provider.client = _FlakyChatClient(errors)
    return provider


def test_openai_compatible_create_with_retry_recovers_after_retryable_errors(monkeypatch):
    monkeypatch.setattr("jobfind.scoring.provider.time.sleep", lambda s: None)
    provider = _openai_compatible_provider_with_fake_client(
        [_FakeError(status_code=429), _FakeError(status_code=503)]
    )

    result = provider._create_with_retry({})

    assert result == "success"
    assert provider.client.calls == 3


def test_openai_compatible_create_with_retry_raises_immediately_on_non_retryable_error(monkeypatch):
    monkeypatch.setattr("jobfind.scoring.provider.time.sleep", lambda s: None)
    provider = _openai_compatible_provider_with_fake_client([_FakeError(status_code=400)])

    with pytest.raises(_FakeError):
        provider._create_with_retry({})

    assert provider.client.calls == 1


class _StubProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0
        self.last_call = None

    def complete(self, system_prompt, user_prompt, *, temperature=0.0, max_tokens=200, response_format=None):
        self.calls += 1
        self.last_call = (system_prompt, user_prompt, temperature, max_tokens, response_format)
        if self.error is not None:
            raise self.error
        return self.result


def test_fallback_provider_uses_primary_when_it_succeeds():
    primary = _StubProvider(result="primary-output")
    secondary = _StubProvider(result="secondary-output")
    provider = FallbackProvider(primary, secondary)

    result = provider.complete("system", "user")

    assert result == "primary-output"
    assert primary.calls == 1
    assert secondary.calls == 0


def test_fallback_provider_calls_secondary_when_primary_exhausted():
    primary = _StubProvider(error=RuntimeError("exhausted"))
    secondary = _StubProvider(result="secondary-output")
    provider = FallbackProvider(primary, secondary)

    result = provider.complete("system", "user")

    assert result == "secondary-output"
    assert primary.calls == 1
    assert secondary.calls == 1


def test_fallback_provider_forwards_identical_arguments_to_secondary():
    primary = _StubProvider(error=RuntimeError("exhausted"))
    secondary = _StubProvider(result="ok")
    provider = FallbackProvider(primary, secondary)
    schema = {"type": "object", "properties": {}}

    provider.complete("sys prompt", "user prompt", temperature=0.0, max_tokens=500, response_format=schema)

    assert secondary.last_call == ("sys prompt", "user prompt", 0.0, 500, schema)


def test_fallback_provider_raises_if_secondary_also_fails():
    primary = _StubProvider(error=RuntimeError("primary down"))
    secondary = _StubProvider(error=RuntimeError("secondary down"))
    provider = FallbackProvider(primary, secondary)

    with pytest.raises(RuntimeError, match="secondary down"):
        provider.complete("system", "user")
