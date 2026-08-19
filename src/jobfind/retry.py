"""Retry-with-backoff for flaky network calls (LLM providers, Sheets API).

One shared implementation instead of copy-pasting the same backoff loop at
every call site.
"""

import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY_SECONDS = 2.0
DEFAULT_MAX_DELAY_SECONDS = 60.0


class QuotaExhausted(Exception):
    """A hard, non-retryable quota error (e.g. a daily request/token cap),
    as opposed to a transient rate limit. No backoff schedule here can
    outlast a multi-hour quota reset, so `retry_call` raises this immediately
    instead of retrying — retrying would just burn attempts, and against a
    request-count quota, more of the already-exhausted quota, for nothing."""

    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original


def status_code_of(error: Exception) -> int | None:
    return getattr(error, "status_code", None) or getattr(error, "code", None)


def _retry_delay_seconds(error: Exception, attempt: int, base_delay: float, max_delay: float) -> float:
    headers = getattr(error, "headers", None) or getattr(getattr(error, "response", None), "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        try:
            return min(float(retry_after), max_delay)
        except ValueError:
            pass

    # Full jitter: uniform in [0, backoff] rather than backoff * [0.5, 1.5],
    # so many callers retrying at once after a shared 429 don't stay correlated.
    backoff = min(base_delay * (2**attempt), max_delay)
    return random.uniform(0, backoff)


def retry_call(
    fn: Callable[[], T],
    *,
    is_retryable: Callable[[Exception], bool],
    label: str,
    is_daily_quota_exhausted: Callable[[Exception], bool] = lambda error: False,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
) -> T:
    """Calls `fn()`, retrying on `is_retryable` failures with exponential
    backoff and full jitter.

    Raises `QuotaExhausted` immediately, without retrying, when
    `is_daily_quota_exhausted` matches the error — a daily cap won't clear
    within this function's backoff window, so retrying it is pure waste.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as error:
            if is_daily_quota_exhausted(error):
                logger.warning("%s hit a daily quota (%s), not retrying", label, error)
                raise QuotaExhausted(error) from error
            if not is_retryable(error) or attempt == max_retries:
                raise
            delay = _retry_delay_seconds(error, attempt, base_delay, max_delay)
            logger.warning(
                "%s failed (%s), retrying in %.1fs [attempt %d/%d]",
                label,
                status_code_of(error) or type(error).__name__,
                delay,
                attempt + 1,
                max_retries,
            )
            last_error = error
            time.sleep(delay)
    # Unreachable: the loop above always returns or raises.
    raise last_error  # type: ignore[misc]
