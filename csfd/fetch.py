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


class ChallengeNotSolved(FetchError):
    """A rendered page kept showing the challenge after every attempt; the user is tried again later."""


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
    """render: "auto" switches to scrape.do's headless browser (5 credits a page instead of 1) once ČSFD serves its
    Anubis bot challenge (on since 19 Oct 2026), and tries a plain request again every probe_seconds to switch back
    when the challenge is off; "always" and "never" force the mode."""

    def __init__(self, token, rate=2.0, timeout=90, retries=3, render="auto", render_attempts=10, max_challenges=20,
                 challenge_credit=5, probe_seconds=120):
        self.token = token
        self.render_mode = render
        self.rendering = render == "always"
        self.limiter = RateLimiter(rate)
        self.timeout = timeout
        self.retries = retries
        # Each rendered page that still shows the challenge adds one, each page that comes through takes
        # challenge_credit off; the run stops as blocked when the count reaches max_challenges. A single page is given
        # up (and its user tried again later) after render_attempts tries.
        self.render_attempts = render_attempts
        self.max_challenges = max_challenges
        self.challenge_credit = challenge_credit
        self.challenges = 0
        self.probe_seconds = probe_seconds
        self.next_probe = 0.0
        self.lock = threading.Lock()
        self.requests = 0
        self.credits_used = 0
        self.credits_remaining = None

    def get(self, url):
        if self._should_probe():
            self.limiter.wait()
            try:
                status, body = self._request(url, render=False)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                status, body = None, ""
            if status == 200 and not is_challenge(body) and not is_not_found(body):
                with self.lock:
                    if self.rendering:
                        log.info("ČSFD serves pages without the bot challenge again, stopping rendering")
                        self.rendering = False
                return body
        last_error = None
        failures = 0
        waits = 0
        while failures < self.retries:
            self.limiter.wait()
            try:
                # A rendered page sometimes comes back before the browser solved the challenge; each retry waits longer.
                status, body = self._request(url, wait_ms=1500 * waits if self.rendering else 0)
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
                    waits += 1
                    self._challenged(url, waits)
                    log.info("Rendered page still shows the bot challenge (attempt %s of %s): %s", waits,
                             self.render_attempts, url)
                    time.sleep(waits)
                elif self.render_mode == "auto":
                    with self.lock:
                        if not self.rendering:
                            log.info("ČSFD serves a bot challenge, switching to rendering in a browser")
                            self.rendering = True
                            self.next_probe = time.monotonic() + self.probe_seconds
                else:
                    raise Blocked(url)
                continue
            last_error = FetchError(f"HTTP {status} for {url}")
            if status in (401, 403):
                break
            failures += 1
            time.sleep(5 * failures)
        raise FetchError(f"Giving up on {url}: {last_error}")

    def _should_probe(self):
        """True for one request every probe_seconds while rendering in auto mode."""
        if not self.rendering or self.render_mode != "auto":
            return False
        with self.lock:
            now = time.monotonic()
            if now < self.next_probe:
                return False
            self.next_probe = now + self.probe_seconds
            return True

    def _challenged(self, url, attempts):
        with self.lock:
            self.challenges += 1
            if self.challenges >= self.max_challenges:
                raise Blocked(url)
        if attempts >= self.render_attempts:
            raise ChallengeNotSolved(url)

    def _passed(self):
        with self.lock:
            self.challenges = max(0, self.challenges - self.challenge_credit)

    def _request(self, url, wait_ms=0, render=None):
        params = {"token": self.token, "url": url, "geoCode": "cz"}
        if self.rendering if render is None else render:
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
