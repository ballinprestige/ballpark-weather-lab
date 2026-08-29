from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from urllib.parse import urlparse

import pytest

from ballpark.errors import PublicVerificationError
from ballpark.publication import canonical_json_bytes, sha256_bytes
from ballpark.reliability import verify_publication_streak
from tests.support import valid_payload_document


class RouteClient:
    def __init__(self, routes: dict[str, bytes | Exception]):
        self.routes = routes
        self.calls: list[str] = []

    def get_bytes(self, url: str) -> bytes:
        path = urlparse(url).path
        self.calls.append(path)
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value


class FlakyIndexClient(RouteClient):
    def __init__(self, routes: dict[str, bytes | Exception]):
        super().__init__(routes)
        self.index_failures_remaining = 1

    def get_bytes(self, url: str) -> bytes:
        path = urlparse(url).path
        if path.endswith("/archive/index.json") and self.index_failures_remaining:
            self.calls.append(path)
            self.index_failures_remaining -= 1
            raise OSError("transient")
        return super().get_bytes(url)


def receipt_for(slate_date: date, *, generated_at: str | None = None) -> tuple[dict, bytes]:
    generated_at = generated_at or f"{slate_date.isoformat()}T16:00:00Z"
    payload = valid_payload_document(slate_date=slate_date.isoformat())
    payload["generated_at"] = generated_at
    content = canonical_json_bytes(payload)
    return (
        {
            "date": slate_date.isoformat(),
            "generated_at": generated_at,
            "payload_sha256": sha256_bytes(content),
            "status": "ready",
            "game_count": 1,
        },
        content,
    )


def client_for(
    rows_and_content: list[tuple[dict, bytes]], *, prefix: str = ""
) -> RouteClient:
    rows = [row for row, _content in rows_and_content]
    routes: dict[str, bytes | Exception] = {
        f"{prefix}/archive/index.json": canonical_json_bytes(
            {"schema_version": 1, "dates": rows}
        )
    }
    for row, content in rows_and_content:
        routes[f"{prefix}/archive/{row['date']}.json"] = content
    return RouteClient(routes)


def test_two_day_streak_is_reported_as_provisional() -> None:
    ending = date(2026, 8, 28)
    client = client_for(
        [receipt_for(ending), receipt_for(ending - timedelta(days=1))],
        prefix="/demo",
    )

    result = verify_publication_streak(
        "https://example.invalid/demo/", ending, client=client
    )

    assert result["state"] == "provisional"
    assert result["same_day_archive_gate_met"] is False
    assert result["progress"] == "2/7"
    assert result["stopped_at"] == "2026-08-26"
    assert "not yet proven" in result["summary"]
    assert [row["date"] for row in result["verified_receipts"]] == [
        "2026-08-28",
        "2026-08-27",
    ]


def test_seven_day_streak_meets_evidence_gate() -> None:
    ending = date(2026, 8, 28)
    receipts = [receipt_for(ending - timedelta(days=offset)) for offset in range(7)]

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client_for(receipts)
    )

    assert result["state"] == "gate_met"
    assert result["same_day_archive_gate_met"] is True
    assert result["progress"] == "7/7"
    assert result["stopped_at"] is None
    assert result["stop_reason"] is None


def test_missing_current_receipt_reports_zero_without_claiming_reliability() -> None:
    ending = date(2026, 8, 28)
    client = client_for([receipt_for(ending - timedelta(days=1))])

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client
    )

    assert result["progress"] == "0/7"
    assert result["same_day_archive_gate_met"] is False
    assert result["stopped_at"] == ending.isoformat()
    assert "missing" in result["stop_reason"]
    assert client.calls == ["/archive/index.json"]


def test_hash_mismatch_stops_the_streak() -> None:
    ending = date(2026, 8, 28)
    row, content = receipt_for(ending)
    row["payload_sha256"] = "0" * 64
    client = client_for([(row, content)])

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client
    )

    assert result["progress"] == "0/7"
    assert "do not match" in result["stop_reason"]


@pytest.mark.parametrize(
    ("generated_at", "expected_progress"),
    [
        ("2026-08-29T03:59:59Z", "1/7"),
        ("2026-08-29T04:00:00Z", "0/7"),
    ],
)
def test_same_day_uses_new_york_calendar_boundary(
    generated_at: str, expected_progress: str
) -> None:
    ending = date(2026, 8, 28)
    client = client_for([receipt_for(ending, generated_at=generated_at)])

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client
    )

    assert result["progress"] == expected_progress


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"generated_at": "not-a-time"}, "invalid generated_at"),
        ({"status": "unknown"}, "invalid publication status"),
        ({"game_count": -1}, "invalid game count"),
    ],
)
def test_malformed_receipt_stops_the_streak(mutation: dict, reason: str) -> None:
    ending = date(2026, 8, 28)
    row, content = receipt_for(ending)
    row.update(mutation)

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client_for([(row, content)])
    )

    assert result["progress"] == "0/7"
    assert reason in result["stop_reason"]


def test_payload_metadata_must_match_receipt() -> None:
    ending = date(2026, 8, 28)
    row, content = receipt_for(ending)
    payload = json.loads(content)
    payload["date"] = "2026-08-27"
    altered = canonical_json_bytes(payload)
    row["payload_sha256"] = sha256_bytes(altered)

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client_for([(row, altered)])
    )

    assert result["progress"] == "0/7"
    assert "payload date" in result["stop_reason"]


def test_schema_invalid_archive_does_not_advance_the_gate() -> None:
    ending = date(2026, 8, 28)
    row, content = receipt_for(ending)
    payload = json.loads(content)
    del payload["health"]
    altered = canonical_json_bytes(payload)
    row["payload_sha256"] = sha256_bytes(altered)

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client_for([(row, altered)])
    )

    assert result["progress"] == "0/7"
    assert "publication contract" in result["stop_reason"]


def test_duplicate_receipt_date_is_not_silently_deduplicated() -> None:
    ending = date(2026, 8, 28)
    row, content = receipt_for(ending)
    client = client_for([(row, content), (deepcopy(row), content)])

    result = verify_publication_streak(
        "https://example.invalid/", ending, client=client
    )

    assert result["progress"] == "0/7"
    assert "duplicate" in result["stop_reason"]
    assert client.calls == ["/archive/index.json"]


def test_malformed_or_unavailable_index_is_a_verification_error() -> None:
    ending = date(2026, 8, 28)
    malformed = RouteClient({"/archive/index.json": b"[]"})
    with pytest.raises(PublicVerificationError, match="index is malformed"):
        verify_publication_streak("https://example.invalid/", ending, client=malformed)

    offline = RouteClient({"/archive/index.json": OSError("offline")})
    with pytest.raises(PublicVerificationError, match="could not be read"):
        verify_publication_streak(
            "https://example.invalid/",
            ending,
            client=offline,
            attempts=1,
            delay_seconds=0,
        )


def test_public_reads_retry_within_a_bounded_attempt_count() -> None:
    ending = date(2026, 8, 28)
    original = client_for([receipt_for(ending)])
    client = FlakyIndexClient(original.routes)

    result = verify_publication_streak(
        "https://example.invalid/",
        ending,
        client=client,
        attempts=2,
        delay_seconds=0,
    )

    assert result["progress"] == "1/7"
    assert client.calls.count("/archive/index.json") == 2


def test_public_url_requires_http() -> None:
    with pytest.raises(ValueError, match="http or https"):
        verify_publication_streak(
            "file:///tmp/site", date(2026, 8, 28), client=RouteClient({})
        )


@pytest.mark.parametrize(
    ("attempts", "delay_seconds", "message"),
    [(0, 0, "attempts"), (11, 0, "attempts"), (1, -1, "delay"), (1, 11, "delay")],
)
def test_retry_bounds_are_enforced(
    attempts: int, delay_seconds: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        verify_publication_streak(
            "https://example.invalid/",
            date(2026, 8, 28),
            client=RouteClient({}),
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
