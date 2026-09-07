#!/usr/bin/env python3
"""Fail-closed contract checks for the untrusted-pull-request workflow.

This is intentionally dependency-free so it runs from requirements.lock.  It is
not a replacement for GitHub's workflow parser: CI and reviewers also run
actionlint.  The checks here protect the security and execution invariants that
are specific to Ballpark's verification lane and deliberately fail on a
mutated unsafe workflow.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
SHA_PIN = r"[0-9a-f]{40}"
EXPECTED_TOP_LEVEL_KEYS = ("name", "on", "permissions", "concurrency", "jobs")
EXPECTED_JOB_KEYS = ("name", "runs-on", "timeout-minutes", "permissions", "env", "steps")
EXPECTED_STEP_NAMES = (
    "Check out the exact proposed revision without persisted credentials",
    "Set up locked Python runtime",
    "Set up supported Node runtime",
    "Assert exact checkout and record event, runtime, and lock identities",
    "Install locked dependencies",
    "Verify Python quality, regressions, contracts, and artifacts",
    "Verify frontend types, units, build, and budget",
    "Install bounded browser-test runtime",
    "Verify desktop and mobile browser paths",
)
EXPECTED_RUNS = {
    "Install locked dependencies": (
        "set -euo pipefail",
        "python -m pip install --disable-pip-version-check --require-hashes -r requirements.lock",
        "python -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e .",
        "npm ci --prefix web",
    ),
    "Verify Python quality, regressions, contracts, and artifacts": (
        "set -euo pipefail",
        "python -m ruff check src tests scripts",
        "python scripts/check_verify_workflow.py",
        "python -m pytest",
        "python -m ballpark verify-artifacts",
    ),
    "Verify frontend types, units, build, and budget": (
        "set -euo pipefail",
        "npm run verify --prefix web",
    ),
    "Install bounded browser-test runtime": (
        "set -euo pipefail",
        "npx --prefix web playwright install --with-deps chromium",
    ),
    "Verify desktop and mobile browser paths": (
        "set -euo pipefail",
        "npm run test:e2e --prefix web",
    ),
}


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.MULTILINE) is not None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scope(lines: list[str], start: int, parent_indent: int) -> list[str]:
    """Return the indented YAML block following a mapping key at ``start``."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#") and _indent(line) <= parent_indent:
            break
        end += 1
    return lines[start + 1 : end]


def _direct_entries(lines: list[str], indent: int) -> list[tuple[str, str]]:
    pattern = re.compile(rf"^ {{{indent}}}([A-Za-z][\w-]*):(?:\s*(.*))?$")
    entries: list[tuple[str, str]] = []
    for line in lines:
        match = pattern.match(line)
        if match:
            entries.append((match.group(1), match.group(2).strip()))
    return entries


def _entry_value(entries: list[tuple[str, str]], key: str) -> str | None:
    values = [value for candidate, value in entries if candidate == key]
    return values[0] if len(values) == 1 else None


def _run_lines(step: list[str]) -> tuple[str, ...] | None:
    run_index = next((index for index, line in enumerate(step) if line == "        run: |"), None)
    if run_index is None:
        return None
    body = step[run_index + 1 :]
    if any(line.strip() and _indent(line) < 10 for line in body):
        return None
    return tuple(line[10:] for line in body if line.strip())


def _validate_structure(lines: list[str]) -> list[str]:
    """Validate the exact allowlisted GitHub Actions job and step graph.

    This intentionally supports only the committed workflow shape.  A YAML formatting or
    structural change fails closed and must be reviewed alongside an updated validator.  actionlint
    separately validates general GitHub Actions/YAML syntax.
    """
    errors: list[str] = []
    if any("\t" in line for line in lines):
        errors.append("tabs are not allowed in the workflow structure")

    top_entries = _direct_entries(lines, 0)
    if tuple(key for key, _ in top_entries) != EXPECTED_TOP_LEVEL_KEYS:
        errors.append("top-level workflow keys differ from the approved structure")

    jobs_index = next((index for index, line in enumerate(lines) if line == "jobs:"), None)
    if jobs_index is None:
        return [*errors, "missing jobs mapping"]
    jobs = _scope(lines, jobs_index, 0)
    job_entries = _direct_entries(jobs, 2)
    if job_entries != [("pr-verify", "")]:
        errors.append("workflow must contain exactly one unconditional pr-verify job")
        return errors

    job_start = next(
        index for index, line in enumerate(lines) if line == "  pr-verify:"
    )
    job = _scope(lines, job_start, 2)
    job_entries = _direct_entries(job, 4)
    if tuple(key for key, _ in job_entries) != EXPECTED_JOB_KEYS:
        errors.append("pr-verify job keys differ from the approved unconditional structure")
    if _entry_value(job_entries, "name") != "PR verification":
        errors.append("pr-verify must retain the stable PR verification check name")
    if _entry_value(job_entries, "runs-on") != "ubuntu-latest":
        errors.append("pr-verify must run on ubuntu-latest")
    if _entry_value(job_entries, "timeout-minutes") != "30":
        errors.append("pr-verify must retain its 30-minute timeout")

    root_permissions_index = next(
        (index for index, line in enumerate(lines) if line == "permissions:"), None
    )
    if root_permissions_index is None or _direct_entries(
        _scope(lines, root_permissions_index, 0), 2
    ) != [("contents", "read")]:
        errors.append("root permissions must be exactly contents: read")

    job_permissions_index = next(
        (index for index, line in enumerate(lines) if line == "    permissions:"), None
    )
    if job_permissions_index is None or _direct_entries(
        _scope(lines, job_permissions_index, 4), 6
    ) != [("contents", "read")]:
        errors.append("pr-verify permissions must be exactly contents: read")

    job_env_index = next((index for index, line in enumerate(lines) if line == "    env:"), None)
    if job_env_index is None or _direct_entries(_scope(lines, job_env_index, 4), 6) != [
        ("PYTHONPATH", "src")
    ]:
        errors.append("pr-verify environment must be exactly PYTHONPATH: src")

    if _has("\n".join(lines), r"(?m)^\s*(?:if|continue-on-error):"):
        errors.append("job and step conditions or continue-on-error are prohibited")

    steps_index = next((index for index, line in enumerate(lines) if line == "    steps:"), None)
    if steps_index is None:
        return [*errors, "missing pr-verify steps"]
    steps = _scope(lines, steps_index, 4)
    step_starts = [
        index for index, line in enumerate(steps) if re.match(r"^      - ", line)
    ]
    step_names: list[str] = []
    step_blocks: dict[str, list[str]] = {}
    for position, start in enumerate(step_starts):
        match = re.match(r"^      - name: (.+)$", steps[start])
        if match is None:
            errors.append("each step must use an allowlisted name mapping")
            continue
        name = match.group(1)
        end = step_starts[position + 1] if position + 1 < len(step_starts) else len(steps)
        step_names.append(name)
        step_blocks[name] = steps[start:end]
    if tuple(step_names) != EXPECTED_STEP_NAMES:
        errors.append("step names or order differ from the approved verification graph")

    expected_actions = {
        "Check out the exact proposed revision without persisted credentials": (
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            [("ref", "${{ github.event.pull_request.head.sha }}"), ("fetch-depth", "1"),
             ("persist-credentials", "false")],
        ),
        "Set up locked Python runtime": (
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            [("python-version", "\"3.12\""), ("cache", "pip"),
             ("cache-dependency-path", "requirements.lock")],
        ),
        "Set up supported Node runtime": (
            "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38",
            [("node-version", "\"22.12.0\""), ("cache", "npm"),
             ("cache-dependency-path", "web/package-lock.json")],
        ),
    }
    for name, (action, expected_with) in expected_actions.items():
        step = step_blocks.get(name, [])
        if f"        uses: {action} # v6" not in step:
            errors.append(f"{name} must use its approved full-SHA action")
        with_index = next(
            (index for index, line in enumerate(step) if line == "        with:"), None
        )
        if with_index is None or _direct_entries(step[with_index + 1 :], 10) != expected_with:
            errors.append(f"{name} inputs differ from the approved structure")

    for name, expected_run in EXPECTED_RUNS.items():
        step = step_blocks.get(name, [])
        if _run_lines(step) != expected_run:
            errors.append(f"{name} run body differs from the approved fail-closed commands")

    receipt_name = "Assert exact checkout and record event, runtime, and lock identities"
    receipt = step_blocks.get(receipt_name, [])
    receipt_env_index = next(
        (index for index, line in enumerate(receipt) if line == "        env:"), None
    )
    expected_receipt_env = [
        ("EVENT_NAME", "${{ github.event_name }}"),
        ("EVENT_REF", "${{ github.ref }}"),
        ("EVENT_MERGE_SHA", "${{ github.sha }}"),
        ("PULL_REQUEST_NUMBER", "${{ github.event.pull_request.number }}"),
        ("PULL_REQUEST_HEAD_REF", "${{ github.event.pull_request.head.ref }}"),
        ("REQUESTED_HEAD_SHA", "${{ github.event.pull_request.head.sha }}"),
    ]
    if receipt_env_index is None or _direct_entries(
        receipt[receipt_env_index + 1 :], 10
    ) != expected_receipt_env:
        errors.append("receipt event identity inputs differ from the approved structure")
    receipt_run = "\n".join(_run_lines(receipt) or ())
    for required in (
        'actual_checkout_sha="$(git rev-parse HEAD)"',
        'if [[ "$actual_checkout_sha" != "$REQUESTED_HEAD_SHA" ]]; then',
        "Event merge SHA:",
        "Requested head SHA:",
        "Actual checkout SHA:",
        "requirements.lock SHA-256:",
        "web/package-lock.json SHA-256:",
    ):
        if required not in receipt_run:
            errors.append("receipt must bind event merge/head and actual checkout identities")
            break
    return errors


def validate(workflow_path: Path) -> list[str]:
    """Return every violated PR-verification invariant for ``workflow_path``."""
    try:
        text = workflow_path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"cannot read workflow: {error}"]

    lines = text.splitlines()
    errors = _validate_structure(lines)

    required_patterns = {
        "a pull_request trigger targeting main": (
            r"^\s{2}pull_request:\s*$[\s\S]*?^\s{4}branches:\s*\[main\]\s*$"
        ),
        "bounded cancellation concurrency": (
            r"^\s{2}group:\s*pr-verification-\$\{\{ github\.event\.pull_request\.number "
            r"\}\}\s*$[\s\S]*?^\s{2}cancel-in-progress:\s*true\s*$"
        ),
        "a Node range guard": r"node_version=.*node --version[\s\S]*Unsupported Node runtime",
    }
    for label, pattern in required_patterns.items():
        if not _has(text, pattern):
            errors.append(f"missing {label}")

    forbidden_patterns = {
        "pull_request_target is unsafe for untrusted PR code": r"\bpull_request_target\b",
        "write permissions are prohibited": r"^\s+[\w-]+:\s*(?:write|write-all)\s*$",
        "Pages or OpenID permissions are prohibited": r"^\s+(?:pages|id-token):",
        "workflow environments are prohibited": r"^\s+environment:",
        "Pages configuration/deployment/upload actions are prohibited": (
            r"actions/(?:configure-pages|deploy-pages|upload-pages-artifact|upload-artifact)@"
        ),
        "publication commands are prohibited": (
            r"python -m ballpark (?:daily|restore-history|verify-public|verify-reliability)"
        ),
        "push or package publication commands are prohibited": r"(?:git push|npm publish)",
        "explicit secret interpolation is prohibited": r"\$\{\{\s*secrets\.",
        "persisted checkout credentials are prohibited": r"persist-credentials:\s*true",
        "required steps must not continue on error": r"continue-on-error:",
    }
    for label, pattern in forbidden_patterns.items():
        if _has(text, pattern):
            errors.append(label)

    allowed_actions = {"checkout", "setup-python", "setup-node"}
    for action_use in re.findall(r"^\s+uses:\s*([^\s#]+)", text, flags=re.MULTILINE):
        matched = re.fullmatch(rf"actions/([\w-]+)@({SHA_PIN})", action_use)
        if matched is None or matched.group(1) not in allowed_actions:
            errors.append(f"unapproved or non-SHA-pinned action: {action_use}")

    for action in allowed_actions:
        pattern = rf"uses:\s*actions/{action}@{SHA_PIN}(?:\s|$)"
        if not _has(text, pattern):
            errors.append(f"actions/{action} must be pinned to a full commit SHA")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", nargs="?", type=Path, default=DEFAULT_WORKFLOW)
    args = parser.parse_args()
    errors = validate(args.workflow)
    if errors:
        for error in errors:
            print(f"workflow contract violation: {error}", file=sys.stderr)
        return 1
    print(f"workflow contract passed: {args.workflow}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
