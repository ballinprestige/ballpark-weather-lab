#!/usr/bin/env python3
"""Semantically validate the exact nondeploying pull-request workflow.

PyYAML is installed from .github/requirements-verify.txt with hashes.  This
validator rejects duplicate keys, aliases, merge keys, multiple documents, and
any semantic difference from the reviewed workflow template.  actionlint is a
separate generic GitHub Actions/YAML syntax check.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml
from yaml.events import AliasEvent

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"


class WorkflowLoader(yaml.SafeLoader):
    """Safe loader with YAML 1.2 booleans and no ambiguous mapping features."""


WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for character, resolvers in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[character] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.IGNORECASE), list("tTfF")
)


def _compose_node(self: WorkflowLoader, parent: object, index: object) -> yaml.Node:
    if self.check_event(AliasEvent):
        raise yaml.YAMLError("aliases are prohibited in the verification workflow")
    return yaml.SafeLoader.compose_node(self, parent, index)


def _construct_mapping(
    self: WorkflowLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise yaml.YAMLError("merge keys are prohibited in the verification workflow")
        key = self.construct_object(key_node, deep=deep)
        try:
            if key in mapping:
                raise yaml.YAMLError(f"duplicate mapping key: {key!r}")
        except TypeError as error:
            raise yaml.YAMLError("mapping keys must be scalar values") from error
        mapping[key] = self.construct_object(value_node, deep=deep)
    return mapping


WorkflowLoader.compose_node = _compose_node
WorkflowLoader.construct_mapping = _construct_mapping

EXPECTED_WORKFLOW_TEMPLATE = r"""name: PR verification

on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read

concurrency:
  group: pr-verification-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  pr-verify:
    name: PR verification
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
    env:
      PYTHONPATH: src
    steps:
      - name: Check out the exact proposed revision without persisted credentials
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 1
          persist-credentials: false

      - name: Set up locked Python runtime
        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements.lock

      - name: Set up supported Node runtime
        uses: actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38
        with:
          node-version: "22.12.0"
          cache: npm
          cache-dependency-path: web/package-lock.json

      - name: Assert exact checkout and record event, runtime, and lock identities
        shell: bash
        env:
          EVENT_NAME: ${{ github.event_name }}
          EVENT_REF: ${{ github.ref }}
          EVENT_MERGE_SHA: ${{ github.sha }}
          PULL_REQUEST_NUMBER: ${{ github.event.pull_request.number }}
          PULL_REQUEST_HEAD_REF: ${{ github.event.pull_request.head.ref }}
          REQUESTED_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: |
          set -euo pipefail
          actual_checkout_sha="$(git rev-parse HEAD)"
          if [[ "$actual_checkout_sha" != "$REQUESTED_HEAD_SHA" ]]; then
            echo "Checked-out SHA does not match requested PR head SHA" >&2
            echo "requested=$REQUESTED_HEAD_SHA actual=$actual_checkout_sha" >&2
            exit 1
          fi
          node_version="$(node --version)"
          if [[ ! "$node_version" =~ ^v22\.(1[2-9]|[2-9][0-9])\. ]]; then
            echo "Unsupported Node runtime: $node_version (need >=22.12, <23)" >&2
            exit 1
          fi
          requirements_lock_sha="$(sha256sum requirements.lock | awk '{print $1}')"
          verify_requirements_sha="$(sha256sum .github/requirements-verify.txt | awk '{print $1}')"
          package_lock_sha="$(sha256sum web/package-lock.json | awk '{print $1}')"
          {
            echo "### PR verification receipt"
            echo "- Event: \`$EVENT_NAME\`"
            echo "- Event ref: \`$EVENT_REF\`"
            echo "- Event merge SHA: \`$EVENT_MERGE_SHA\`"
            echo "- Pull request: \`#$PULL_REQUEST_NUMBER\`"
            echo "- Proposed ref: \`$PULL_REQUEST_HEAD_REF\`"
            echo "- Requested head SHA: \`$REQUESTED_HEAD_SHA\`"
            echo "- Actual checkout SHA: \`$actual_checkout_sha\`"
            echo "- Runner accepted trigger at: \`$(date -u +'%Y-%m-%dT%H:%M:%SZ')\`"
            echo "- Python: \`$(python --version)\`"
            echo "- Node: \`$node_version\`"
            echo "- requirements.lock SHA-256: \`$requirements_lock_sha\`"
            echo "- .github/requirements-verify.txt SHA-256: \`$verify_requirements_sha\`"
            echo "- web/package-lock.json SHA-256: \`$package_lock_sha\`"
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Install locked dependencies
        shell: bash
        run: |
          set -euo pipefail
          python -m pip install --disable-pip-version-check --require-hashes -r requirements.lock
          python -m pip install --disable-pip-version-check --only-binary=:all: \
            --require-hashes -r .github/requirements-verify.txt
          python -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e .
          npm ci --prefix web

      - name: Verify Python quality, regressions, contracts, and artifacts
        shell: bash
        run: |
          set -euo pipefail
          python -m ruff check src tests scripts
          python scripts/check_verify_workflow.py
          python -m pytest
          python -m ballpark verify-artifacts

      - name: Verify frontend types, units, build, and budget
        shell: bash
        run: |
          set -euo pipefail
          npm run verify --prefix web

      - name: Install bounded browser-test runtime
        shell: bash
        run: |
          set -euo pipefail
          npx --prefix web playwright install --with-deps chromium

      - name: Verify desktop and mobile browser paths
        shell: bash
        run: |
          set -euo pipefail
          npm run test:e2e --prefix web
"""


def _load_single_document(text: str) -> object:
    documents = list(yaml.load_all(text, Loader=WorkflowLoader))
    if len(documents) != 1:
        raise yaml.YAMLError("workflow must contain exactly one YAML document")
    return documents[0]


EXPECTED_WORKFLOW = _load_single_document(EXPECTED_WORKFLOW_TEMPLATE)


def _exact_value_equal(expected: object, candidate: object) -> bool:
    """Compare parsed YAML values without Python's bool/int/float coercion."""
    if type(expected) is not type(candidate):
        return False
    if isinstance(expected, dict):
        if len(expected) != len(candidate):
            return False
        unmatched = list(candidate.items())
        for expected_key, expected_value in expected.items():
            for index, (candidate_key, candidate_value) in enumerate(unmatched):
                if _exact_value_equal(expected_key, candidate_key):
                    if not _exact_value_equal(expected_value, candidate_value):
                        return False
                    unmatched.pop(index)
                    break
            else:
                return False
        return not unmatched
    if isinstance(expected, list):
        return len(expected) == len(candidate) and all(
            _exact_value_equal(expected_item, candidate_item)
            for expected_item, candidate_item in zip(expected, candidate, strict=True)
        )
    return expected == candidate


def validate(workflow_path: Path) -> list[str]:
    """Return fail-closed workflow-template validation errors for ``workflow_path``."""
    try:
        candidate = _load_single_document(workflow_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return [f"cannot securely parse workflow: {error}"]
    if not _exact_value_equal(EXPECTED_WORKFLOW, candidate):
        return ["workflow differs from the approved semantic verification template"]
    return []


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
