"""Downloading ČSFD pages through scrape.do (https://scrape.do)."""

import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .parse import is_not_found

BASE_URL = "https://www.csfd.cz"
API_URL = "https://api.scrape.do/"


class NotFound(Exception):
    pass


class FetchError(Exception):
    pass


class Blocked(FetchError):
    """ČSFD served an anti-bot challenge instead of the page. Stops the run, so no credits are wasted."""


def ratings_url(user_id, page=1):
    return f"{BASE_URL}/uzivatel/{user_id}/hodnoceni/" + (f"?page={page}" if page > 1 else "")


class RateLimiter:
    """Spaces requests at least 1/rate seconds apart, across all threads."""

    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            wait_until = max(now, self.next_time)
            self.next_time = wait_until + self.interval
        if wait_until > now:
            time.sleep(wait_until - now)


class ScrapeDoFetcher:
    def __init__(self, token, rate=2.0, timeout=90, retries=3):
        self.token = token
        self.limiter = RateLimiter(rate)
        self.timeout = timeout
        self.retries = retries
        self.lock = threading.Lock()
        self.requests = 0
        self.credits_used = 0
        self.credits_remaining = None

    def get(self, url):
        last_error = None
        for attempt in range(self.retries):
            if attempt:
                time.sleep(5 * attempt)
            self.limiter.wait()
            try:
                status, body = self._request(url)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = e
                continue
            if status == 404 or (status == 200 and is_not_found(body)):
                raise NotFound(url)
            if status == 200:
                if "anubis_challenge" in body or "Making sure you&#39;re not a bot" in body:
                    raise Blocked(url)
                return body
            last_error = FetchError(f"HTTP {status} for {url}")
            if status in (401, 403):
                break
        raise FetchError(f"Giving up on {url}: {last_error}")

    def _request(self, url):
        query = urllib.parse.urlencode({"token": self.token, "url": url, "geoCode": "cz"})
        request = urllib.request.Request(f"{API_URL}?{query}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._count(response.headers)
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            self._count(e.headers)
            return e.code, e.read().decode("utf-8", "replace")

    def _count(self, headers):
        with self.lock:
            self.requests += 1
            cost = headers.get("scrape.do-request-cost")
            remaining = headers.get("scrape.do-remaining-credits")
            if cost and cost.isdigit():
                self.credits_used += int(cost)
            if remaining and remaining.isdigit():
                self.credits_remaining = int(remaining)
