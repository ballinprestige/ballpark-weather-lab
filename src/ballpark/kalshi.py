"""Bounded public Kalshi MLB-total reader; a supplemental exchange lane."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ballpark.http import HttpClient

API = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXMLBTOTAL"
_ET = ZoneInfo("America/New_York")
_EVENT = re.compile(r"^KXMLBTOTAL-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})([A-Z]{3})([A-Z]{3})$")
_TITLE = re.compile(r"^Over\s+(\d+\.5)\s+runs scored$", re.I)
_MONTHS = {name: number for number, name in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_KALSHI_TEAM = {"ARI": "AZ", "OAK": "ATH"}


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_start(ticker: Any) -> tuple[datetime, str, str] | None:
    match = _EVENT.fullmatch(str(ticker or ""))
    if not match:
        return None
    try:
        local = datetime(2000 + int(match[1]), _MONTHS[match[2]], int(match[3]), int(match[4]), int(match[5]), tzinfo=_ET)
    except (KeyError, ValueError):
        return None
    return local.astimezone(UTC), match[6], match[7]


def _phase(game: dict[str, Any], at: datetime) -> tuple[str, str | None]:
    status = str(game.get("game_status") or "").lower()
    if any(word in status for word in ("postponed", "cancelled", "canceled")):
        return "unknown", "official game is postponed or cancelled"
    if "final" in status:
        return "final", "official game is final"
    if any(word in status for word in ("in progress", "live", "delayed", "suspended")):
        return "in_progress", None
    try:
        return ("pregame" if at < _utc(str(game["game_time"])) else "after_scheduled_start"), None
    except (KeyError, TypeError, ValueError):
        return "unknown", "official scheduled start is invalid"


def unavailable_market(game: dict[str, Any], observed_at: datetime, reason: str) -> dict[str, Any]:
    phase, status_reason = _phase(game, observed_at)
    return {
        "state": "unavailable", "reason": status_reason or reason, "provider": "Kalshi", "provider_url": API, "slate_date": _utc(str(game["game_time"])).astimezone(_ET).date().isoformat(),
        "event_ticker": None, "market_ticker": None, "game_pk": int(game["game_pk"]), "game_time": game.get("game_time"),
        "market_type": "total", "period": "full_game", "game_phase": phase, "quote_type": "contract_ask", "condition": None, "price_format": "contract_cents", "currency": "USD", "line": None,
        "over_ask_dollars": None, "under_ask_dollars": None, "over_ask_cents": None, "under_ask_cents": None,
        "over_ask_size": None, "under_ask_size": None, "source_updated_at": None, "observed_at": _stamp(observed_at),
        "raw_sha256": None, "snapshot_id": None, "active": False, "source_schema_version": "kalshi-public-v2", "failure_reason": None,
    }


def _ask(value: Any) -> Decimal | None:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal if Decimal("0") < decimal < Decimal("1") else None


def _size(value: Any) -> Decimal | None:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal if decimal > 0 else None


def _line(event_ticker: str, market: dict[str, Any]) -> dict[str, Any] | None:
    title = _TITLE.fullmatch(str(market.get("title") or ""))
    if not title or market.get("status") != "active" or market.get("event_ticker") != event_ticker:
        return None
    ticker = str(market.get("ticker") or "")
    if not ticker.startswith(event_ticker + "-") or market.get("strike_type") != "greater":
        return None
    try:
        line = Decimal(title[1])
        if Decimal(str(market.get("floor_strike"))) != line:
            return None
    except (InvalidOperation, ValueError):
        return None
    over, under = _ask(market.get("yes_ask_dollars")), _ask(market.get("no_ask_dollars"))
    # NO ask is supplied by the reciprocal positive YES bid.
    over_size, under_size = _size(market.get("yes_ask_size_fp")), _size(market.get("yes_bid_size_fp"))
    if None in (over, under, over_size, under_size):
        return None
    return {"market_ticker": ticker, "line": float(line), "condition": f"Over {line} runs scored", "over_ask_dollars": format(over, ".4f"), "under_ask_dollars": format(under, ".4f"), "over_ask_cents": int((over * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "under_ask_cents": int((under * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "over_ask_size": format(over_size, "f"), "under_ask_size": format(under_size, "f")}


def orderbook_validates(quote: dict[str, Any], orderbook: dict[str, Any]) -> bool:
    """Confirm both supplied asks from positive reciprocal best bids."""
    levels = orderbook.get("orderbook_fp", {}) if isinstance(orderbook, dict) else {}

    def best(values: Any) -> tuple[Decimal, Decimal] | None:
        options: list[tuple[Decimal, Decimal]] = []
        for level in values or []:
            if not isinstance(level, list) or len(level) != 2:
                continue
            price, size = _ask(level[0]), _size(level[1])
            if price is not None and size is not None:
                options.append((price, size))
        return max(options, default=None, key=lambda value: value[0])

    yes_bid, no_bid = best(levels.get("yes_dollars")), best(levels.get("no_dollars"))
    if yes_bid is None or no_bid is None:
        return False
    return (
        Decimal("1") - no_bid[0] == Decimal(str(quote["over_ask_dollars"]))
        and Decimal("1") - yes_bid[0] == Decimal(str(quote["under_ask_dollars"]))
    )


def normalize_markets(game: dict[str, Any], event: dict[str, Any], markets: list[dict[str, Any]], *, observed_at: datetime, raw_bytes: bytes | None = None) -> dict[str, Any]:
    """Accept only a one-to-one exact event and one full-game active line."""
    observed_at = _utc(observed_at)
    missing = unavailable_market(game, observed_at, "no active two-sided Kalshi half-run market")
    phase, reason = _phase(game, observed_at)
    parsed = _event_start(event.get("event_ticker"))
    if reason:
        return {**missing, "reason": reason}
    team_pair = (_KALSHI_TEAM.get(str(game.get("away_team")), game.get("away_team")), _KALSHI_TEAM.get(str(game.get("home_team")), game.get("home_team")))
    if not parsed or parsed[0] != _utc(str(game.get("game_time"))) or parsed[1:] != team_pair:
        return {**missing, "reason": "Kalshi event does not exactly match official teams and scheduled start"}
    lines = [line for market in markets if isinstance(market, dict) if (line := _line(str(event["event_ticker"]), market))]
    if not lines:
        return {**missing, "reason": "no active two-sided Kalshi half-run market"}
    line = min(
        lines,
        key=lambda value: (
            abs(value["over_ask_cents"] - 50) + abs(value["under_ask_cents"] - 50),
            value["market_ticker"],
        ),
    )
    raw = raw_bytes if raw_bytes is not None else json.dumps({"event": event, "markets": markets}, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "state": "available", "reason": "source quote-update time is not supplied", "provider": "Kalshi", "provider_url": API, "slate_date": _utc(str(game["game_time"])).astimezone(_ET).date().isoformat(),
        "event_ticker": str(event["event_ticker"]), "market_ticker": line["market_ticker"], "game_pk": int(game["game_pk"]), "game_time": game.get("game_time"),
        "market_type": "total", "period": "full_game", "game_phase": phase, "quote_type": "contract_ask", "condition": line["condition"], "price_format": "contract_cents", "currency": "USD", **line,
        "source_updated_at": None, "observed_at": _stamp(observed_at), "raw_sha256": digest,
        "snapshot_id": hashlib.sha256(f"{digest}:{game['game_pk']}:{line['market_ticker']}".encode()).hexdigest(), "active": True, "source_schema_version": "kalshi-public-v2", "failure_reason": None,
    }


class KalshiSnapshotCache:
    """Durable last-good exchange evidence. Error responses never replace it."""
    def __init__(self, path: Path) -> None:
        self.path, self.quotes = path, {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.quotes = {str(key): quote for key, quote in value.get("quotes", {}).items() if isinstance(quote, dict)}
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    def get(self, game: dict[str, Any], at: datetime, reason: str) -> dict[str, Any] | None:
        quote = self.quotes.get(str(game["game_pk"]))
        if not quote or quote.get("state") != "available":
            return None
        try:
            age = (_utc(at) - _utc(str(quote["observed_at"]))).total_seconds()
        except (KeyError, ValueError):
            return None
        if not 0 <= age <= 86400:
            return None
        return {**quote, "reason": "source quote-update time is not supplied; retained after fetch failure", "failure_reason": reason}

    def accept(self, quote: dict[str, Any]) -> None:
        if quote.get("state") != "available":
            return
        existing = self.quotes.get(str(quote["game_pk"]))
        if existing and _utc(str(existing["observed_at"])) > _utc(str(quote["observed_at"])):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        acquired = False
        for _ in range(20):
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor); acquired = True; break
            except FileExistsError:
                time.sleep(0.05)
        if not acquired:
            return
        fd, temporary = tempfile.mkstemp(prefix=".kalshi-", dir=self.path.parent)
        try:
            try:
                disk = json.loads(self.path.read_text(encoding="utf-8"))
                disk_quotes = disk.get("quotes", {})
                latest = disk_quotes.get(str(quote["game_pk"])) if isinstance(disk_quotes, dict) else None
                if isinstance(latest, dict) and _utc(str(latest["observed_at"])) > _utc(str(quote["observed_at"])):
                    quote = latest
                if isinstance(disk_quotes, dict):
                    self.quotes.update(disk_quotes)
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                pass
            self.quotes[str(quote["game_pk"])] = dict(quote)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "quotes": self.quotes}, handle, sort_keys=True)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
            if acquired and lock.exists():
                lock.unlink()


class KalshiExchangeProvider:
    def __init__(self, client: HttpClient, *, cache_path: Path) -> None:
        self.client, self.cache = client, KalshiSnapshotCache(cache_path)

    def _json(self, route: str, params: dict[str, Any]) -> tuple[Any, bytes]:
        raw = self.client.get_bytes(f"{API}{route}?{urlencode(params)}")
        return json.loads(raw), raw

    def fetch(self, schedule: list[dict[str, Any]], *, observed_at: datetime) -> dict[int, dict[str, Any]]:
        observed_at = _utc(observed_at)
        results = {int(game["game_pk"]): unavailable_market(game, observed_at, "no exact Kalshi event") for game in schedule}
        try:
            events, raw_pages, cursor = [], [], None
            for _ in range(3):
                parameters: dict[str, Any] = {"series_ticker": SERIES, "limit": 100}
                if cursor:
                    parameters["cursor"] = cursor
                body, page_raw = self._json("/events", parameters)
                page = body.get("events") if isinstance(body, dict) else None
                if not isinstance(page, list):
                    raise ValueError("events response has no event list")
                events.extend(page); raw_pages.append(page_raw)
                cursor = body.get("cursor")
                if not cursor:
                    break
            events_raw = b"\n".join(raw_pages)
        except Exception as exc:
            reason = f"Kalshi public market request failed: {type(exc).__name__}"
            return {int(game["game_pk"]): self.cache.get(game, observed_at, reason) or {**results[int(game["game_pk"])], "reason": reason} for game in schedule}
        used: set[str] = set()
        for game in schedule:
            team_pair = (_KALSHI_TEAM.get(str(game.get("away_team")), game.get("away_team")), _KALSHI_TEAM.get(str(game.get("home_team")), game.get("home_team")))
            matches = [event for event in events if isinstance(event, dict) and (parsed := _event_start(event.get("event_ticker"))) and parsed[0] == _utc(str(game.get("game_time"))) and parsed[1:] == team_pair]
            if len(matches) != 1 or str(matches[0]["event_ticker"]) in used:
                continue
            event = matches[0]; used.add(str(event["event_ticker"]))
            try:
                body, markets_raw = self._json("/markets", {"event_ticker": event["event_ticker"], "limit": 100})
                markets = body.get("markets") if isinstance(body, dict) else None
                if not isinstance(markets, list): raise ValueError("markets response has no market list")
                quote = normalize_markets(game, event, markets, observed_at=observed_at, raw_bytes=events_raw + b"\n" + markets_raw)
                if cursor and quote["state"] == "unavailable":
                    quote["failure_reason"] = "Kalshi event pagination reached its three-page safety limit"
                if quote["state"] == "available":
                    orderbook, orderbook_raw = self._json(
                        f"/markets/{quote['market_ticker']}/orderbook", {}
                    )
                    if not orderbook_validates(quote, orderbook):
                        quote = unavailable_market(
                            game, observed_at, "Kalshi orderbook does not confirm the two supplied asks"
                        )
                    else:
                        # The public API has no quote-update timestamp.  Record completion of
                        # this paired market/orderbook retrieval, never the pipeline start time.
                        quote["observed_at"] = _stamp(datetime.now(UTC))
                        quote["raw_sha256"] = hashlib.sha256(
                            events_raw + b"\n" + markets_raw + b"\n" + orderbook_raw
                        ).hexdigest()
                        quote["snapshot_id"] = hashlib.sha256(
                            f"{quote['raw_sha256']}:{game['game_pk']}:{quote['market_ticker']}".encode()
                        ).hexdigest()
                results[int(game["game_pk"])] = quote
                self.cache.accept(quote)
            except Exception as exc:
                reason = f"Kalshi public market request failed: {type(exc).__name__}"
                results[int(game["game_pk"])] = self.cache.get(game, observed_at, reason) or {**results[int(game["game_pk"])], "reason": reason}
        return results
