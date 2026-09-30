import threading
import time
import random
import logging

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> bool:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


class RateLimiter:
    """Global message rate limiter."""

    def __init__(self, max_per_minute: int = 20):
        self.bucket = TokenBucket(rate=max_per_minute / 60.0, capacity=max_per_minute)
        self.session_cooldowns: dict[str, float] = {}
        self.lock = threading.Lock()

    def can_send(self, session_key: str, cooldown: float = 30) -> bool:
        now = time.monotonic()
        with self.lock:
            if session_key in self.session_cooldowns:
                if now - self.session_cooldowns[session_key] < cooldown:
                    return False
            if not self.bucket.acquire():
                return False
            self.session_cooldowns[session_key] = now
            return True


def random_delay(min_s: float, max_s: float):
    """Sleep for a random duration to simulate human behavior."""
    delay = random.uniform(min_s, max_s)
    logger.debug(f"Anti-detect delay: {delay:.1f}s")
    time.sleep(delay)


class CircuitBreaker:
    """Circuit breaker for external calls."""

    def __init__(self, threshold: int = 3, cooldown: float = 300):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.last_failure_time = 0.0
        self.open = False

    def record_success(self):
        self.failures = 0
        self.open = False

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.monotonic()
        if self.failures >= self.threshold:
            self.open = True
            logger.warning(f"Circuit breaker opened after {self.failures} failures")

    def is_open(self) -> bool:
        if self.open:
            if time.monotonic() - self.last_failure_time > self.cooldown:
                self.open = False
                self.failures = 0
                logger.info("Circuit breaker reset")
                return False
            return True
        return False
