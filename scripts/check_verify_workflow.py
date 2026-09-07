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


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.MULTILINE) is not None


def validate(workflow_path: Path) -> list[str]:
    """Return every violated PR-verification invariant for ``workflow_path``."""
    try:
        text = workflow_path.read_text(encoding="utf-8")
    except OSError as error:
        return [f"cannot read workflow: {error}"]

    errors: list[str] = []

    required_patterns = {
        "a pull_request trigger targeting main": (
            r"^\s{2}pull_request:\s*$[\s\S]*?^\s{4}branches:\s*\[main\]\s*$"
        ),
        "a stable pr-verify job": r"^\s{2}pr-verify:\s*$",
        "a stable PR verification check name": r"^\s{4}name:\s*PR verification\s*$",
        "bounded cancellation concurrency": (
            r"^\s{2}group:\s*pr-verification-\$\{\{ github\.event\.pull_request\.number "
            r"\}\}\s*$[\s\S]*?^\s{2}cancel-in-progress:\s*true\s*$"
        ),
        "a bounded job timeout": r"^\s{4}timeout-minutes:\s*30\s*$",
        "root contents read permission": r"^permissions:\s*$\n^\s{2}contents:\s*read\s*$",
        "job contents read permission": r"^\s{4}permissions:\s*$\n^\s{6}contents:\s*read\s*$",
        "the exact PR-head SHA checkout": (
            r"^\s{10}ref:\s*\$\{\{ github\.event\.pull_request\.head\.sha \}\}\s*$"
        ),
        "non-persisted checkout credentials": r"^\s{10}persist-credentials:\s*false\s*$",
        "locked Python 3.12": r"^\s{10}python-version:\s*[\"']3\.12[\"']\s*$",
        "supported Node 22.12": r"^\s{10}node-version:\s*[\"']22\.12\.0[\"']\s*$",
        "a Node range guard": r"node_version=.*node --version[\s\S]*Unsupported Node runtime",
        "hash-checked Python installation": (
            r"python -m pip install --disable-pip-version-check --require-hashes "
            r"-r requirements\.lock"
        ),
        "locked frontend installation": r"npm ci --prefix web",
        "dynamic Python test discovery": r"^\s{10}python -m pytest\s*$",
        "artifact verification": r"^\s{10}python -m ballpark verify-artifacts\s*$",
        "frontend verification": r"^\s{10}npm run verify --prefix web\s*$",
        "browser verification": r"^\s{10}npm run test:e2e --prefix web\s*$",
        "fail-closed shell mode": r"set -euo pipefail",
        "event, ref, SHA, runtime, and lock receipts": (
            r"PULL_REQUEST_SHA[\s\S]*requirements\.lock SHA-256[\s\S]*"
            r"web/package-lock\.json SHA-256"
        ),
    }
    for label, pattern in required_patterns.items():
        if not _has(text, pattern):
            errors.append(f"missing {label}")

    forbidden_patterns = {
        "pull_request_target is unsafe for untrusted PR code": r"\bpull_request_target\b",
        "write permissions are prohibited": r"^\s+[\w-]+:\s*write\s*$",
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
        "required steps must not continue on error": r"continue-on-error:\s*true",
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
