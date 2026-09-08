"""Reduce a registry tag-list JSON document without retaining tag names."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewed-revision", required=True)
    args = parser.parse_args()
    try:
        value = json.load(sys.stdin, object_pairs_hook=_unique)
        tags = value["Tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError
        if len(set(tags)) != len(tags):
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        print('{"state":"indeterminate"}')
        return 2
    canonical = "\n".join(sorted(tags)).encode()
    print(
        json.dumps(
            {
                "state": "observed",
                "tag_count": len(tags),
                "tag_set_sha256": hashlib.sha256(canonical).hexdigest(),
                "reviewed_revision_tag_present": args.reviewed_revision in tags,
            },
            sort_keys=True,
        )
    )
    return 0


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
