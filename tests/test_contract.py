from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ballpark.contract import validate_payload
from ballpark.errors import DataContractError


def test_schema_accepts_canonical_payload(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    validate_payload(valid_payload, project_root / "schemas" / "slate.schema.json")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(status="unexpected"), "status"),
        (lambda value: value["games"][0]["weather"].pop("basis"), "weather.*basis"),
        (
            lambda value: value["games"][0]["factors"].update(
                weather_multiplier_runs=1.5
            ),
            "weather_multiplier_runs",
        ),
    ],
)
def test_schema_rejects_malformed_payloads(
    valid_payload: dict[str, object],
    project_root: Path,
    mutation: object,
    message: str,
) -> None:
    payload = deepcopy(valid_payload)
    mutation(payload)
    with pytest.raises(DataContractError, match=message):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")


def test_contract_rejects_duplicate_game_ids(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    payload = deepcopy(valid_payload)
    payload["games"].append(deepcopy(payload["games"][0]))
    with pytest.raises(DataContractError, match="duplicate game IDs"):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")


def test_contract_rejects_cross_date_game(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    payload = deepcopy(valid_payload)
    payload["games"][0]["game_date"] = "2026-08-25"
    with pytest.raises(DataContractError, match="cross-date game"):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")


def test_contract_rejects_nonempty_no_slate(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    payload = deepcopy(valid_payload)
    payload["status"] = "no_slate"
    payload["no_slate_reason"] = "No scheduled games."
    with pytest.raises(DataContractError, match="no-slate payload cannot contain games"):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")


def test_contract_rejects_empty_ready_payload(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    payload = deepcopy(valid_payload)
    payload["games"] = []
    with pytest.raises(DataContractError, match="requires at least one game"):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")


def test_contract_rejects_invalid_generated_at_format(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    payload = deepcopy(valid_payload)
    payload["generated_at"] = "not-a-timestamp"
    with pytest.raises(DataContractError, match="generated_at"):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")


def test_contract_rejects_weather_receipt_for_another_game(
    valid_payload: dict[str, object], project_root: Path
) -> None:
    payload = deepcopy(valid_payload)
    payload["games"][0]["weather"]["game_pk"] = 999999
    with pytest.raises(DataContractError, match="weather attached to the wrong game"):
        validate_payload(payload, project_root / "schemas" / "slate.schema.json")
