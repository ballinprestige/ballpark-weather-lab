from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_verify_workflow import validate

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "verify.yml"


def test_candidate_pr_workflow_satisfies_security_and_execution_contract() -> None:
    assert validate(WORKFLOW) == []


def test_contract_rejects_pull_request_target_and_persisted_checkout(tmp_path: Path) -> None:
    unsafe = tmp_path / "verify.yml"
    unsafe.write_text(
        WORKFLOW.read_text(encoding="utf-8")
        .replace("pull_request:\n", "pull_request_target:\n", 1)
        .replace("persist-credentials: false", "persist-credentials: true", 1),
        encoding="utf-8",
    )

    errors = validate(unsafe)

    assert errors == ["workflow differs from the approved semantic verification template"]


def test_contract_rejects_an_unpinned_or_unapproved_action(tmp_path: Path) -> None:
    unsafe = tmp_path / "verify.yml"
    unsafe.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace(
            "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38",
            "actions/setup-node@v6",
            1,
        ),
        encoding="utf-8",
    )

    errors = validate(unsafe)

    assert errors == ["workflow differs from the approved semantic verification template"]


def test_validator_command_fails_nonzero_for_an_unsafe_workflow(tmp_path: Path) -> None:
    unsafe = tmp_path / "verify.yml"
    unsafe.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace(
            "python -m pytest", "python -m pytest -k never-runs", 1
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "scripts/check_verify_workflow.py", str(unsafe)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "approved semantic verification template" in completed.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace(
            "    name: PR verification", "    if: false\n    name: PR verification", 1
        ),
        lambda text: text.replace(
            "          python -m pytest",
            "          if false; then\n            python -m pytest\n          fi",
            1,
        ),
        lambda text: text.replace(
            "        shell: bash\n        run: |\n          set -euo pipefail\n"
            "          python -m ruff",
            "        shell: bash\n        continue-on-error: ${{ true }}\n        run: |\n"
            "          set -euo pipefail\n          python -m ruff",
            1,
        ),
        lambda text: text
        + "\n  privileged-bypass:\n    runs-on: ubuntu-latest\n    permissions: write-all\n"
        + "    steps:\n      - run: gh api --method POST /repos/example/example/issues\n",
        lambda text: text.replace(
            "    name: PR verification", "    if : false\n    name: PR verification", 1
        ),
        lambda text: text.replace(
            "        shell: bash\n        run: |\n          set -euo pipefail\n"
            "          python -m ruff",
            "        shell: bash\n        if : false\n        run: |\n"
            "          set -euo pipefail\n          python -m ruff",
            1,
        ),
        lambda text: text.replace("        shell: bash", "        shell: bash {0} || true", 1),
        lambda text: text.replace(
            "    steps:\n",
            "    steps:\n      -\n        run: echo 'BASH_ENV=attacker' >> \"$GITHUB_ENV\"\n",
            1,
        ),
        lambda text: text.replace(
            '          actual_checkout_sha="$(git rev-parse HEAD)"',
            '          actual_checkout_sha="$(git rev-parse HEAD)"\n'
            '          actual_checkout_sha="$REQUESTED_HEAD_SHA"',
            1,
        ),
        lambda text: text.replace(
            "\npermissions:\n",
            "\n  push:\n    branches: [main]\n\npermissions:\n",
            1,
        ),
        lambda text: text.replace(
            "types: [opened, synchronize, reopened, ready_for_review]",
            "types: [opened, reopened, ready_for_review]",
            1,
        ),
        lambda text: text
        + "\n  attacker :\n    runs-on: ubuntu-latest\n    permissions : write-all\n"
        + "    steps:\n      - run: gh api --method POST /repos/example/example/issues\n",
        lambda text: text.replace(
            "name: PR verification", "name: duplicate\nname: PR verification", 1
        ),
        lambda text: text.replace(
            "permissions:\n  contents: read",
            "permissions: &readonly\n  contents: read\nreused-permissions: *readonly",
            1,
        ),
    ],
    ids=(
        "skipped-job",
        "conditional-pytest",
        "expression-continue-on-error",
        "write-all-job",
        "spaced-job-if",
        "spaced-step-if",
        "custom-shell-swallow",
        "bare-leading-step",
        "receipt-overwrite",
        "extra-push-trigger",
        "missing-synchronize-trigger",
        "spaced-write-all-job",
        "duplicate-key",
        "alias",
    ),
)
def test_contract_rejects_semantic_bypass_mutations(tmp_path: Path, mutation: object) -> None:
    unsafe = tmp_path / "verify.yml"
    unsafe.write_text(mutation(WORKFLOW.read_text(encoding="utf-8")), encoding="utf-8")

    errors = validate(unsafe)

    assert errors


def test_fail_closed_command_stops_after_an_intentional_failure() -> None:
    command = (
        [
            "cmd",
            "/d",
            "/s",
            "/c",
            "cmd /c exit 23 && echo unreachable",
        ]
        if os.name == "nt"
        else [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            "python -c \"raise SystemExit(23)\"; echo unreachable",
        ]
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 23
    assert "unreachable" not in completed.stdout
