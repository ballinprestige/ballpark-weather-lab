from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import pytest

import ballpark.publication as publication_module
from ballpark.errors import DataContractError, PublicVerificationError
from ballpark.publication import (
    atomic_write,
    canonical_json_bytes,
    publish_payload,
    restore_public_history,
    sha256_bytes,
    verify_public_release,
)
from tests.support import valid_payload_document


class RouteClient:
    def __init__(self, routes: dict[str, bytes | Exception]):
        self.routes = routes
        self.calls: list[str] = []
        self.urls: list[str] = []

    def get_bytes(self, url: str) -> bytes:
        path = urlparse(url).path
        self.urls.append(url)
        self.calls.append(path)
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value


class SequencedClient:
    def __init__(self, routes: dict[str, list[bytes]]):
        self.routes = routes
        self.indices: defaultdict[str, int] = defaultdict(int)

    def get_bytes(self, url: str) -> bytes:
        path = urlparse(url).path
        values = self.routes[path]
        index = self.indices[path]
        self.indices[path] += 1
        return values[min(index, len(values) - 1)]


def test_canonical_json_is_stable_and_newline_terminated() -> None:
    left = canonical_json_bytes({"b": 2, "a": 1})
    right = canonical_json_bytes({"a": 1, "b": 2})
    assert left == right == b'{"a":1,"b":2}\n'


def test_atomic_write_preserves_existing_file_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "data.json"
    destination.write_bytes(b"last-good")

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(publication_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        atomic_write(destination, b"new-release")

    assert destination.read_bytes() == b"last-good"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_publish_payload_writes_receipt_archive_and_sorted_deduplicated_index(
    valid_payload: dict[str, object], tmp_path: Path
) -> None:
    output = tmp_path / "site"
    first_release = publish_payload(output, valid_payload)
    first_bytes = canonical_json_bytes(valid_payload)

    assert (output / "data" / "data.json").read_bytes() == first_bytes
    assert first_release["payload_sha256"] == sha256_bytes(first_bytes)
    assert json.loads((output / "data" / "release.json").read_text()) == first_release
    assert (output / "archive" / "2026-08-26.json").read_bytes() == first_bytes

    older = valid_payload_document(slate_date="2026-08-25")
    older["generated_at"] = "2026-08-25T16:00:00Z"
    publish_payload(output, older)
    replacement = deepcopy(valid_payload)
    replacement["generated_at"] = "2026-08-26T17:00:00Z"
    replacement_release = publish_payload(output, replacement)

    index = json.loads((output / "archive" / "index.json").read_text())
    assert [row["date"] for row in index["dates"]] == ["2026-08-26", "2026-08-25"]
    assert len([row for row in index["dates"] if row["date"] == "2026-08-26"]) == 1
    assert index["dates"][0]["payload_sha256"] == replacement_release["payload_sha256"]
    assert list(output.rglob("*.tmp")) == []


def test_publish_rejects_unreadable_archive_index(
    valid_payload: dict[str, object], tmp_path: Path
) -> None:
    index = tmp_path / "site" / "archive" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text("not-json", encoding="utf-8")
    with pytest.raises(DataContractError, match="archive index is unreadable"):
        publish_payload(tmp_path / "site", valid_payload)


def test_public_date_and_hash_readback_succeeds(valid_payload: dict[str, object]) -> None:
    payload_bytes = canonical_json_bytes(valid_payload)
    release = {
        "date": valid_payload["date"],
        "payload_sha256": sha256_bytes(payload_bytes),
    }
    client = RouteClient(
        {
            "/demo/data/release.json": canonical_json_bytes(release),
            "/demo/data/data.json": payload_bytes,
        }
    )

    result = verify_public_release(
        "https://example.invalid/demo/",
        release,
        client=client,
        attempts=1,
        delay_seconds=0,
    )

    assert result["state"] == "verified"
    assert result["date"] == "2026-08-26"
    assert result["payload_sha256"] == release["payload_sha256"]
    assert result["attempt"] == 1
    assert len(client.urls) == 2
    assert all("?release=" in url for url in client.urls)


def test_public_readback_retries_then_accepts_matching_date_and_hash(
    monkeypatch: pytest.MonkeyPatch, valid_payload: dict[str, object]
) -> None:
    payload_bytes = canonical_json_bytes(valid_payload)
    expected = {
        "date": valid_payload["date"],
        "payload_sha256": sha256_bytes(payload_bytes),
    }
    stale = {**expected, "date": "2026-08-25"}
    client = SequencedClient(
        {
            "/data/release.json": [canonical_json_bytes(stale), canonical_json_bytes(expected)],
            "/data/data.json": [payload_bytes],
        }
    )
    delays: list[float] = []
    monkeypatch.setattr(publication_module.time, "sleep", delays.append)

    result = verify_public_release(
        "https://example.invalid/",
        expected,
        client=client,
        attempts=2,
        delay_seconds=0.25,
    )

    assert result["attempt"] == 2
    assert delays == [0.25]


def test_public_readback_failure_is_bounded(
    monkeypatch: pytest.MonkeyPatch, valid_payload: dict[str, object]
) -> None:
    payload_bytes = canonical_json_bytes(valid_payload)
    expected = {
        "date": valid_payload["date"],
        "payload_sha256": sha256_bytes(payload_bytes),
    }
    release_bytes = canonical_json_bytes(expected)
    client = SequencedClient(
        {
            "/data/release.json": [release_bytes],
            "/data/data.json": [b"wrong-payload"],
        }
    )
    delays: list[float] = []
    monkeypatch.setattr(publication_module.time, "sleep", delays.append)

    with pytest.raises(PublicVerificationError, match="after 3 bounded attempts"):
        verify_public_release(
            "https://example.invalid/",
            expected,
            client=client,
            attempts=3,
            delay_seconds=0.1,
        )

    assert client.indices["/data/release.json"] == 3
    assert client.indices["/data/data.json"] == 3
    assert delays == [0.1, 0.1]


@pytest.mark.parametrize("attempts", [0, 61])
def test_public_readback_rejects_unbounded_attempt_counts(attempts: int) -> None:
    with pytest.raises(ValueError, match="attempts must be between 1 and 60"):
        verify_public_release(
            "https://example.invalid/",
            {"payload_sha256": "a" * 64, "date": "2026-08-26"},
            client=RouteClient({}),
            attempts=attempts,
            delay_seconds=0,
        )


def test_restore_history_obeys_maximum_and_accepts_only_matching_hashes(
    tmp_path: Path,
) -> None:
    good = canonical_json_bytes(valid_payload_document(slate_date="2026-08-26"))
    bad = canonical_json_bytes(valid_payload_document(slate_date="2026-08-25"))
    ignored = canonical_json_bytes(valid_payload_document(slate_date="2026-08-24"))
    index = {
        "schema_version": 1,
        "updated_at": "2026-08-26T16:00:00Z",
        "dates": [
            {"date": "2026-08-26", "payload_sha256": sha256_bytes(good)},
            {"date": "2026-08-25", "payload_sha256": "0" * 64},
            {"date": "2026-08-24", "payload_sha256": sha256_bytes(ignored)},
        ],
    }
    client = RouteClient(
        {
            "/archive/index.json": canonical_json_bytes(index),
            "/archive/2026-08-26.json": good,
            "/archive/2026-08-25.json": bad,
            "/archive/2026-08-24.json": ignored,
        }
    )

    result = restore_public_history(
        "https://example.invalid/",
        tmp_path / "site",
        client=client,
        maximum_dates=2,
    )

    assert result == {"state": "restored", "restored": 1, "reason": None}
    assert client.calls == [
        "/archive/index.json",
        "/archive/2026-08-26.json",
        "/archive/2026-08-25.json",
    ]
    assert (tmp_path / "site" / "archive" / "2026-08-26.json").read_bytes() == good
    assert not (tmp_path / "site" / "archive" / "2026-08-25.json").exists()
    assert not (tmp_path / "site" / "archive" / "2026-08-24.json").exists()
    restored_index = json.loads(
        (tmp_path / "site" / "archive" / "index.json").read_text(encoding="utf-8")
    )
    assert [row["date"] for row in restored_index["dates"]] == ["2026-08-26"]


def test_restore_history_network_failure_is_non_blocking(tmp_path: Path) -> None:
    client = RouteClient({"/archive/index.json": OSError("offline")})
    result = restore_public_history(
        "https://example.invalid/", tmp_path / "site", client=client, maximum_dates=2
    )
    assert result["state"] == "not_available"
    assert result["restored"] == 0
    assert not (tmp_path / "site").exists()


def test_restore_history_skips_archive_path_traversal(tmp_path: Path) -> None:
    content = b"untrusted-history\n"
    index = {
        "schema_version": 1,
        "updated_at": "2026-08-26T16:00:00Z",
        "dates": [{"date": "../../escape", "payload_sha256": sha256_bytes(content)}],
    }
    client = RouteClient(
        {
            "/archive/index.json": canonical_json_bytes(index),
            "/escape.json": content,
        }
    )
    output = tmp_path / "site"
    result = restore_public_history(
        "https://example.invalid/", output, client=client, maximum_dates=1
    )
    assert result == {"state": "restored", "restored": 0, "reason": None}
    assert client.calls == ["/archive/index.json"]
    assert not (tmp_path / "escape.json").exists()


def test_restore_history_rejects_negative_maximum(tmp_path: Path) -> None:
    client = RouteClient(
        {
            "/archive/index.json": canonical_json_bytes(
                {"schema_version": 1, "updated_at": None, "dates": []}
            )
        }
    )
    with pytest.raises(ValueError, match="maximum_dates"):
        restore_public_history(
            "https://example.invalid/", tmp_path / "site", client=client, maximum_dates=-1
        )
