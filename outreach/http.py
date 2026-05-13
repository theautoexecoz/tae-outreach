import time
from urllib.parse import urlparse
import httpx
from outreach.config import USER_AGENT, RATE_LIMIT_RPS


class DomainRateLimiter:
    def __init__(self, rps: float = RATE_LIMIT_RPS):
        self._min_interval = 1.0 / rps if rps > 0 else 0
        self._last_request: dict[str, float] = {}

    def wait(self, url: str):
        domain = urlparse(url).netloc
        now = time.monotonic()
        last = self._last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request[domain] = time.monotonic()


_limiter = DomainRateLimiter()


def http_get(url: str, headers: dict | None = None, timeout: int = 20) -> httpx.Response:
    _limiter.wait(url)
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers=h)
        r.raise_for_status()
        return r
