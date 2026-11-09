"""Small process-local sliding-window limiter for the single-node deployment."""

import threading
import time
from collections import deque
from collections.abc import Callable
from math import ceil


class SlidingWindowRateLimiter:
    def __init__(
        self, limit: int, window_seconds: int, clock: Callable[[], float] = time.monotonic
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int | None:
        now = self._clock()
        with self._lock:
            for client, requests in list(self._requests.items()):
                while requests and now - requests[0] >= self.window_seconds:
                    requests.popleft()
                if not requests:
                    self._requests.pop(client, None)

            requests = self._requests.setdefault(key, deque())
            if len(requests) >= self.limit:
                return max(1, ceil(self.window_seconds - (now - requests[0])))

            requests.append(now)
            if len(self._requests) > 4096:
                for client in list(self._requests)[: len(self._requests) - 4096]:
                    self._requests.pop(client, None)
            return None
