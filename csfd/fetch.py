"""Downloading ČSFD pages through scrape.do (https://scrape.do)."""

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .parse import is_not_found

BASE_URL = "https://www.csfd.cz"
API_URL = "https://api.scrape.do/"

log = logging.getLogger("csfd")


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


def is_challenge(body):
    return "anubis_challenge" in body or "Making sure you&#39;re not a bot" in body


class ScrapeDoFetcher:
    """render: "auto" switches to scrape.do's headless browser (5 credits a page instead of 1) for the rest of the
    run once ČSFD serves its Anubis bot challenge (on since 19 Oct 2026); "always" and "never" force the mode."""

    def __init__(self, token, rate=2.0, timeout=90, retries=3, render="auto", max_challenges=5):
        self.token = token
        self.render_mode = render
        self.rendering = render == "always"
        self.limiter = RateLimiter(rate)
        self.timeout = timeout
        self.retries = retries
        # Rendered pages that still show the challenge add one, pages that come through take one off; the run stops
        # as blocked when it reaches max_challenges.
        self.max_challenges = max_challenges
        self.challenges = 0
        self.lock = threading.Lock()
        self.requests = 0
        self.credits_used = 0
        self.credits_remaining = None

    def get(self, url):
        last_error = None
        failures = 0
        waits = 0
        while failures < self.retries:
            self.limiter.wait()
            try:
                # A rendered page sometimes comes back before the browser solved the challenge; wait longer on a retry.
                status, body = self._request(url, wait_ms=min(3000 * waits, 10000) if self.rendering else 0)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = e
                failures += 1
                time.sleep(5 * failures)
                continue
            if status == 404 or (status == 200 and is_not_found(body)):
                self._passed()
                raise NotFound(url)
            if status == 200:
                if not is_challenge(body):
                    self._passed()
                    return body
                if self.rendering:
                    self._challenged(url)
                    waits += 1
                elif self.render_mode == "auto":
                    with self.lock:
                        if not self.rendering:
                            log.info("ČSFD serves a bot challenge, switching to rendering in a browser")
                            self.rendering = True
                else:
                    raise Blocked(url)
                continue
            last_error = FetchError(f"HTTP {status} for {url}")
            if status in (401, 403):
                break
            failures += 1
            time.sleep(5 * failures)
        raise FetchError(f"Giving up on {url}: {last_error}")

    def _challenged(self, url):
        with self.lock:
            self.challenges += 1
            if self.challenges >= self.max_challenges:
                raise Blocked(url)
        log.info("Rendered page still shows the bot challenge (%s of %s), retrying", self.challenges,
                 self.max_challenges)

    def _passed(self):
        with self.lock:
            self.challenges = max(0, self.challenges - 1)

    def _request(self, url, wait_ms=0):
        params = {"token": self.token, "url": url, "geoCode": "cz"}
        if self.rendering:
            params["render"] = "true"
        if wait_ms:
            params["customWait"] = str(wait_ms)
        query = urllib.parse.urlencode(params)
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
