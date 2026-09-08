"""Bounded external freshness and dead-man-monitor support."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from ballpark.runtime import parse_stamp
from ballpark.runtime_app import _validate_payload

_MAX_BODY_BYTES = 2 * 1024 * 1024
_PING_ENV = "BALLPARK_MONITOR_PING_URL"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


HttpGet = Callable[[str, float], HttpResponse]


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> Request | None:
        raise RuntimeError("redirects are not allowed for runtime monitoring")


def _default_get(url: str, timeout_seconds: float) -> HttpResponse:
    request = Request(url, method="GET", headers={"User-Agent": "ballpark-runtime-monitor/1"})
    deadline = time.monotonic() + timeout_seconds

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise RuntimeError("monitor request exceeded its total deadline")
        return value

    def set_read_timeout(response: Any) -> None:
        """Reapply the remaining total deadline before each socket-backed read."""
        try:
            response.fp.raw._sock.settimeout(remaining())
        except AttributeError as exc:
            raise RuntimeError("monitor transport cannot enforce a total deadline") from exc

    try:
        with build_opener(_RejectRedirects()).open(request, timeout=remaining()) as response:
            chunks, size = [], 0
            while True:
                set_read_timeout(response)
                reader = getattr(response, "read1", response.read)
                chunk = reader(min(64 * 1024, _MAX_BODY_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > _MAX_BODY_BYTES:
                    raise RuntimeError("monitor response exceeds its byte limit")
            remaining()
            return HttpResponse(int(response.status), b"".join(chunks))
    except (HTTPError, URLError, OSError) as exc:
        if isinstance(exc, TimeoutError) or time.monotonic() >= deadline:
            raise RuntimeError("monitor request exceeded its total deadline") from exc
        raise RuntimeError("monitor request failed") from exc


def _json_response(response: HttpResponse, name: str) -> dict[str, Any]:
    if response.status != 200:
        raise RuntimeError(f"{name} returned HTTP {response.status}")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} is not an object")
    return value


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.fragment:
        raise ValueError("runtime URL must be an absolute http or https URL without a fragment")
    return value.rstrip("/") + "/"


def _ping_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ValueError(f"{_PING_ENV} must be an absolute https URL without a fragment")
    return value


def expected_new_york_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo("America/New_York")).date()


def _source_issues(payload: dict[str, Any]) -> list[str]:
    health = payload.get("health")
    if not isinstance(health, dict):
        return ["payload health is missing"]
    issues: list[str] = []
    weather = health.get("weather")
    if isinstance(weather, dict) and weather.get("state") == "unavailable":
        issues.append("weather source is unavailable")
    odds = health.get("odds")
    if isinstance(odds, dict) and odds.get("acquisition_status") in {
        "schema_error",
        "transport_error",
    }:
        issues.append("sportsbook source attempt failed")
    return issues


def monitor_runtime(
    base_url: str,
    *,
    expected_date: date,
    timeout_seconds: float = 10,
    max_source_attempt_age_seconds: int = 600,
    max_publication_age_seconds: int = 600,
    max_maintenance_age_seconds: int = 600,
    max_run_seconds: float = 60,
    ping_url: str | None = None,
    get: HttpGet = _default_get,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check public liveness, worker readiness and release binding before one secret ping."""
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("monitor timeout must be between 1 and 30 seconds")
    if not 60 <= max_source_attempt_age_seconds <= 86_400:
        raise ValueError("source attempt age must be between 60 and 86400 seconds")
    if not 60 <= max_publication_age_seconds <= 86_400:
        raise ValueError("publication age must be between 60 and 86400 seconds")
    if not 60 <= max_maintenance_age_seconds <= 86_400:
        raise ValueError("maintenance age must be between 60 and 86400 seconds")
    if not 5 <= max_run_seconds <= 300:
        raise ValueError("monitor run budget must be between 5 and 300 seconds")
    base = _base_url(base_url)
    observed_now = (now or datetime.now(UTC)).astimezone(UTC)
    monitor = {"state": "not_configured"}
    run_deadline = time.monotonic() + max_run_seconds

    def fetch_response(path: str) -> HttpResponse:
        remaining = run_deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("monitor run exceeded its total deadline")
        response = get(urljoin(base, path), min(timeout_seconds, remaining))
        if time.monotonic() > run_deadline:
            raise RuntimeError("monitor run exceeded its total deadline")
        return response

    def fetch(path: str, name: str) -> dict[str, Any]:
        return _json_response(fetch_response(path), name)

    try:
        live = fetch("healthz", "liveness")
        if live.get("state") != "live":
            raise RuntimeError("liveness state is not live")
        readiness = fetch("readiness", "readiness")
        if readiness.get("state") != "ready":
            raise RuntimeError("worker readiness is not ready")
        release = fetch("data/release.json", "release")
        payload_response = fetch_response("data/data.json")
        payload = _json_response(payload_response, "payload")
        _validate_payload(payload)
        digest = hashlib.sha256(payload_response.body).hexdigest()
        observed_date = payload.get("date")
        if observed_date != expected_date.isoformat() or release.get("date") != observed_date:
            raise RuntimeError("published slate date is not the expected New York date")
        if release.get("payload_sha256") != digest:
            raise RuntimeError("release receipt does not bind payload bytes")
        published_at = parse_stamp(str(payload.get("generated_at")))
        if published_at > observed_now + timedelta(minutes=5):
            raise RuntimeError("published payload timestamp is in the future")
        if observed_now - published_at > timedelta(seconds=max_publication_age_seconds):
            raise RuntimeError("published payload is stale")
        source_issues = _source_issues(payload)
        source_health = fetch("source-health", "source health")
        sportsbook = source_health.get("sportsbook")
        if (
            source_health.get("state") != "available"
            or source_health.get("date") != expected_date.isoformat()
            or not isinstance(sportsbook, dict)
            or sportsbook.get("latest_status")
            not in {"observed", "no_quote", "schema_error", "transport_error"}
        ):
            raise RuntimeError("source freshness summary is invalid")
        latest_attempted_at = parse_stamp(str(sportsbook.get("latest_attempted_at")))
        if latest_attempted_at > observed_now + timedelta(minutes=5):
            raise RuntimeError("sportsbook source attempt is in the future")
        if observed_now - latest_attempted_at > timedelta(seconds=max_source_attempt_age_seconds):
            source_issues.append("sportsbook source attempt is stale")
        if sportsbook["latest_status"] in {"schema_error", "transport_error"}:
            source_issues.append("sportsbook source attempt failed")
        maintenance = source_health.get("maintenance")
        if (
            not isinstance(maintenance, dict)
            or maintenance.get("state") not in {"succeeded", "failed"}
        ):
            raise RuntimeError("maintenance freshness summary is invalid")
        maintenance_at = parse_stamp(str(maintenance.get("observed_at")))
        if maintenance_at > observed_now + timedelta(minutes=5):
            raise RuntimeError("publication maintenance timestamp is in the future")
        if observed_now - maintenance_at > timedelta(seconds=max_maintenance_age_seconds):
            source_issues.append("publication maintenance is stale")
        if maintenance["state"] == "failed":
            source_issues.append("publication maintenance failed")
        source_issues = list(dict.fromkeys(source_issues))
        if source_issues:
            return {
                "state": "source_degraded",
                "date": observed_date,
                "payload_sha256": digest,
                "problems": source_issues,
                "monitor": monitor,
            }
    except (RuntimeError, TypeError, ValueError) as exc:
        return {"state": "unhealthy", "problems": [str(exc)], "monitor": monitor}
    if ping_url is None:
        return {
            "state": "monitor_not_configured",
            "date": observed_date,
            "payload_sha256": digest,
            "problems": [],
            "monitor": monitor,
        }
    try:
        _ping_url(ping_url)
        remaining = run_deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("monitor run exceeded its total deadline")
        response = get(ping_url, min(timeout_seconds, remaining))
        if time.monotonic() > run_deadline:
            raise RuntimeError("monitor run exceeded its total deadline")
        if response.status < 200 or response.status >= 300:
            raise RuntimeError("monitor ping returned a non-success status")
    except (RuntimeError, TypeError, ValueError):
        return {
            "state": "monitor_ping_failed",
            "date": observed_date,
            "payload_sha256": digest,
            "problems": ["external monitor heartbeat was not accepted"],
            "monitor": {"state": "ping_failed"},
        }
    return {
        "state": "ready",
        "date": observed_date,
        "payload_sha256": digest,
        "problems": [],
        "monitor": {"state": "pinged"},
    }


def monitor_from_environment(
    base_url: str,
    *,
    expected_date: date,
    timeout_seconds: float,
    max_source_attempt_age_seconds: int = 600,
    max_publication_age_seconds: int = 600,
    max_maintenance_age_seconds: int = 600,
    max_run_seconds: float = 60,
    get: HttpGet = _default_get,
    now: datetime | None = None,
) -> dict[str, Any]:
    return monitor_runtime(
        base_url,
        expected_date=expected_date,
        timeout_seconds=timeout_seconds,
        max_source_attempt_age_seconds=max_source_attempt_age_seconds,
        max_publication_age_seconds=max_publication_age_seconds,
        max_maintenance_age_seconds=max_maintenance_age_seconds,
        max_run_seconds=max_run_seconds,
        ping_url=os.environ.get(_PING_ENV),
        get=get,
        now=now,
    )
