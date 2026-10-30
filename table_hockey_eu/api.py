"""
Polite client for the public table-hockey.eu JSON API.

Only the anonymous read endpoints are used. robots.txt allows /api/* but not
/swagger, and the authenticated endpoints (/api/tournaments, /ithf-export)
are out of scope.
"""

from __future__ import annotations

import logging
import time

import requests

BASE_URL = "https://table-hockey.eu"
USER_AGENT = "table-hockey-h2h-scraper/1.0 (+https://github.com/amundfylling/table-hockey-eu-fetch)"

log = logging.getLogger(__name__)


class NotFound(Exception):
    """The API answered 404 (for example an unlisted tournament id)."""


class ApiClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        retries: int = 4,
        backoff: float = 2.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.request_count = 0
        self._last_request = 0.0

    def _wait(self) -> None:
        delay = self._last_request + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()

    def get(self, path: str):
        url = f"{self.base_url}{path}"
        for attempt in range(self.retries + 1):
            self._wait()
            self.request_count += 1
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                error: str = str(exc)
            else:
                if response.status_code == 404:
                    raise NotFound(url)
                if response.status_code < 400:
                    return response.json()
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                error = f"HTTP {response.status_code}"
            if attempt == self.retries:
                raise RuntimeError(f"GET {url} failed after {attempt + 1} attempts: {error}")
            wait = self.backoff * 2**attempt
            log.warning("GET %s failed (%s), retrying in %.0fs", url, error, wait)
            time.sleep(wait)
        raise AssertionError("unreachable")

    def tournaments(self) -> list[dict]:
        return self.get("/api/tournaments/all")

    def tournament(self, tournament_id: int) -> dict:
        return self.get(f"/api/tournaments/{tournament_id}")

    def stage(self, stage_id: int) -> dict:
        return self.get(f"/api/stages/{stage_id}")
