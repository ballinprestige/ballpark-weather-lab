from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ballpark.errors import BallparkError, PublicVerificationError
from ballpark.paths import ProjectPaths


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _default_date() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ballpark",
        description="Build and verify the standalone Ballpark Weather Lab publication.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("build", "daily"):
        command = commands.add_parser(name)
        command.add_argument("--date", type=_date, default=_default_date())
        command.add_argument("--fixture", type=Path)
        command.add_argument("--output", type=Path)
        command.add_argument("--generated-at")
    commands.choices["daily"].add_argument("--skip-web", action="store_true")

    refresh = commands.add_parser("refresh-exchange")
    refresh.add_argument("--payload", type=Path, required=True)
    refresh.add_argument("--output", type=Path, required=True)
    refresh.add_argument("--generated-at")

    verify = commands.add_parser("verify-public")
    verify.add_argument("--url", required=True)
    expected = verify.add_mutually_exclusive_group(required=True)
    expected.add_argument("--receipt", type=Path)
    expected.add_argument("--expected-sha")
    verify.add_argument("--expected-date", type=_date)
    verify.add_argument("--attempts", type=int, default=12)
    verify.add_argument("--delay", type=float, default=10.0)

    restore = commands.add_parser("restore-history")
    restore.add_argument("--url", required=True)
    restore.add_argument("--output", type=Path)
    restore.add_argument("--maximum-dates", type=int, default=120)

    reliability = commands.add_parser("verify-reliability")
    reliability.add_argument("--url", required=True)
    reliability.add_argument("--ending-date", type=_date, default=_default_date())
    reliability.add_argument("--attempts", type=int, default=3)
    reliability.add_argument("--delay", type=float, default=1.0)

    worker = commands.add_parser("runtime-worker")
    worker.add_argument("--state-dir", type=Path, required=True)
    worker.add_argument("--cache-dir", type=Path, required=True)
    worker.add_argument("--publication-dir", type=Path, required=True)
    worker.add_argument("--fixture", type=Path)
    worker.add_argument("--date", type=_date, default=_default_date())
    worker.add_argument("--once", action="store_true")

    probe = commands.add_parser("runtime-probe")
    probe.add_argument("--state-dir", type=Path, required=True)
    probe.add_argument("--max-heartbeat-age-seconds", type=int, default=180)
    probe.add_argument("--max-job-lag-seconds", type=int, default=30)

    server = commands.add_parser("runtime-server")
    server.add_argument("--state-dir", type=Path, required=True)
    server.add_argument("--publication-dir", type=Path, required=True)
    server.add_argument("--web-dir", type=Path, required=True)
    server.add_argument("--port", type=int, default=8080)
    server.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")

    commands.add_parser("verify-artifacts")
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _run_web_build(paths: ProjectPaths) -> dict[str, object]:
    from ballpark.artifacts import sha256_file

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise BallparkError(
            "npm was not found; install Node.js 22.x before running the daily build"
        )
    subprocess.run([npm, "run", "build"], cwd=paths.web, check=True)
    release_path = paths.web / "dist" / "data" / "release.json"
    payload_path = paths.web / "dist" / "data" / "data.json"
    if not release_path.is_file() or not payload_path.is_file():
        raise BallparkError("frontend build omitted the release receipt or payload")
    release = json.loads(release_path.read_text(encoding="utf-8"))
    actual_hash = sha256_file(payload_path)
    if release.get("payload_sha256") != actual_hash:
        raise BallparkError("frontend distribution payload hash differs from its release receipt")
    return {"state": "built", "path": str(paths.web / "dist"), "payload_sha256": actual_hash}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        paths = ProjectPaths.discover(Path(__file__).resolve())
        if args.command == "verify-artifacts":
            from ballpark.artifacts import verify_artifacts
            from ballpark.geometry_artifact import verify_exported_geometry

            receipt = verify_artifacts(paths)
            verify_exported_geometry(paths.root)
            _print(receipt.as_dict())
            return 0

        if args.command == "runtime-worker":
            from ballpark.runtime_app import run_fixture_worker, run_live_worker

            result = (
                run_fixture_worker(
                    paths,
                    fixture=args.fixture.resolve(),
                    target_date=args.date,
                    state_dir=args.state_dir.resolve(),
                    cache_dir=args.cache_dir.resolve(),
                    publication_dir=args.publication_dir.resolve(),
                    once=args.once,
                )
                if args.fixture
                else run_live_worker(
                    paths,
                    state_dir=args.state_dir.resolve(),
                    cache_dir=args.cache_dir.resolve(),
                    publication_dir=args.publication_dir.resolve(),
                    once=args.once,
                )
            )
            _print(result)
            return 0

        if args.command == "runtime-probe":
            from ballpark.runtime_app import runtime_readiness

            result = runtime_readiness(
                args.state_dir.resolve(),
                heartbeat_seconds=args.max_heartbeat_age_seconds,
                job_lag_seconds=args.max_job_lag_seconds,
            )
            _print(result)
            return 0 if result["state"] == "ready" else 3

        if args.command == "runtime-server":
            from ballpark.runtime_server import serve

            serve(
                web_dir=args.web_dir.resolve(),
                publication_dir=args.publication_dir.resolve(),
                state_dir=args.state_dir.resolve(),
                host=args.host,
                port=args.port,
            )
            return 0

        if args.command in {"build", "daily"}:
            from ballpark.pipeline import DailyPipeline

            output = (args.output or (paths.web / "public")).resolve()
            payload, release = DailyPipeline(paths).build_and_publish(
                args.date,
                output,
                fixture_path=args.fixture.resolve() if args.fixture else None,
                generated_at=args.generated_at,
            )
            result: dict[str, object] = {
                "state": "published-locally",
                "date": payload["date"],
                "status": payload["status"],
                "game_count": len(payload["games"]),
                "output": str(output),
                "release": release,
            }
            if args.command == "daily" and not args.skip_web:
                result["web"] = _run_web_build(paths)
            _print(result)
            return 0

        if args.command == "refresh-exchange":
            from ballpark.contract import validate_payload
            from ballpark.http import HttpClient
            from ballpark.kalshi import KalshiExchangeProvider
            from ballpark.publication import publish_payload

            payload = json.loads(args.payload.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
                raise ValueError("--payload must contain a daily slate object")
            existing_generated_at = payload.get("generated_at")
            if not isinstance(existing_generated_at, str):
                raise ValueError("--payload must retain its original generated_at")
            if args.generated_at is not None and args.generated_at != existing_generated_at:
                raise ValueError(
                    "refresh-exchange cannot replace generated_at; "
                    "it only refreshes exchange evidence"
                )
            observed_at = datetime.now(UTC)
            quotes = KalshiExchangeProvider(
                HttpClient(), cache_path=paths.root / ".ballpark-cache" / "kalshi-exchange.json"
            ).fetch(payload["games"], observed_at=observed_at)
            for game in payload["games"]:
                game["exchange_market"] = quotes[int(game["game_pk"])]
            states = [quote["state"] for quote in quotes.values()]
            payload["health"]["exchange_markets"] = {
                "state": "available"
                if states and all(state == "observed_unknown_age" for state in states)
                else "partial"
                if "observed_unknown_age" in states
                else "unavailable",
                "source": "Kalshi public market-data API",
                "observed_unknown_age_games": states.count("observed_unknown_age"),
                "unavailable_games": states.count("unavailable"),
                "optional": True,
            }
            validate_payload(payload, paths.schemas / "slate.schema.json")
            release = publish_payload(args.output.resolve(), payload)
            _print(
                {
                    "state": "published-locally",
                    "output": str(args.output.resolve()),
                    "release": release,
                }
            )
            return 0

        if args.command == "verify-public":
            from ballpark.publication import verify_public_release
            from ballpark.stdlib_http import StdlibBytesClient

            if args.receipt:
                expected_release = json.loads(args.receipt.read_text(encoding="utf-8"))
            else:
                if args.expected_date is None:
                    raise ValueError("--expected-date is required with --expected-sha")
                expected_release = {
                    "date": args.expected_date.isoformat(),
                    "payload_sha256": args.expected_sha,
                }
            _print(
                verify_public_release(
                    args.url,
                    expected_release,
                    client=StdlibBytesClient(),
                    attempts=args.attempts,
                    delay_seconds=args.delay,
                )
            )
            return 0

        if args.command == "restore-history":
            from ballpark.http import HttpClient
            from ballpark.publication import restore_public_history

            output = (args.output or (paths.web / "public")).resolve()
            _print(
                restore_public_history(
                    args.url,
                    output,
                    client=HttpClient(),
                    maximum_dates=args.maximum_dates,
                )
            )
            return 0

        if args.command == "verify-reliability":
            from ballpark.reliability import verify_publication_streak
            from ballpark.stdlib_http import StdlibBytesClient

            _print(
                verify_publication_streak(
                    args.url,
                    args.ending_date,
                    client=StdlibBytesClient(),
                    schema_path=paths.schemas / "slate.schema.json",
                    attempts=args.attempts,
                    delay_seconds=args.delay,
                )
            )
            return 0
    except subprocess.CalledProcessError as exc:
        print(f"error: frontend build failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1
    except (BallparkError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    raise PublicVerificationError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
