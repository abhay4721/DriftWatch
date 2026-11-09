from driftwatch.rate_limit import SlidingWindowRateLimiter


def test_sliding_window_limits_then_allows_after_expiration() -> None:
    now = [100.0]
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])

    assert limiter.retry_after("client-a") is None
    assert limiter.retry_after("client-a") is None
    assert limiter.retry_after("client-a") == 60
    assert limiter.retry_after("client-b") is None

    now[0] += 60
    assert limiter.retry_after("client-a") is None


def test_rate_limit_is_independent_per_client() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=30, clock=lambda: 10.0)

    assert limiter.retry_after("client-a") is None
    assert limiter.retry_after("client-a") == 30
    assert limiter.retry_after("client-b") is None
