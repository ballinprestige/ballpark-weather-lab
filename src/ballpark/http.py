from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass
class HttpClient:
    """Small, bounded HTTP client shared by public-data adapters."""

    connect_timeout: float = 3.0
    read_timeout: float = 8.0
    attempts: int = 2
    maximum_bytes: int = 5 * 1024 * 1024
    user_agent: str = "ballpark-weather-lab/1.0 (+https://github.com/)"

    def __post_init__(self) -> None:
        retry = Retry(
            total=max(0, self.attempts - 1),
            connect=max(0, self.attempts - 1),
            read=max(0, self.attempts - 1),
            status=max(0, self.attempts - 1),
            backoff_factor=0.6,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            # Provider-controlled Retry-After values are intentionally ignored so a daily run
            # cannot exceed its bounded network budget.
            respect_retry_after_header=False,
            raise_on_status=False,
        )
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": self.user_agent, "Accept": "application/json"})

    def _get_bytes(self, url: str, *, params: dict[str, Any] | None = None) -> bytes:
        response = self.session.get(
            url,
            params=params,
            timeout=(self.connect_timeout, self.read_timeout),
            stream=True,
        )
        try:
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > self.maximum_bytes:
                raise ValueError("HTTP response exceeds the size limit")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.maximum_bytes:
                    raise ValueError("HTTP response exceeds the size limit")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        return json.loads(self._get_bytes(url, params=params))

    def get_bytes(self, url: str) -> bytes:
        return self._get_bytes(url)
