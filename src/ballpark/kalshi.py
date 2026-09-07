"""Optional Kalshi full-game exchange quotes; never sportsbook/American odds."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

_TITLE = re.compile(r"^Over\s+(\d+\.5)\s+runs scored$", re.I)


def normalize_markets(game: dict[str, Any], event: dict[str, Any], markets: list[dict[str, Any]], *, observed_at: datetime) -> dict[str, Any]:
    """Normalize one exact scheduled game; unknown source quote age is explicit."""
    unavailable = {"state": "unavailable", "reason": "no active two-sided Kalshi half-run market", "provider": "Kalshi exchange", "price_format": "contract_cents", "source_updated_at": None, "observed_at": observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")}
    if event.get("status") not in {"open", "active"}:
        return unavailable
    lines = []
    for market in markets:
        match = _TITLE.match(str(market.get("title") or ""))
        if market.get("status") != "active" or not match:
            continue
        try:
            yes, no = Decimal(str(market["yes_ask_dollars"])), Decimal(str(market["no_ask_dollars"]))
        except Exception:
            continue
        if not (Decimal("0") < yes < Decimal("1") and Decimal("0") < no < Decimal("1")):
            continue
        raw = hashlib.sha256(json.dumps(market, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        lines.append({"market_ticker": market.get("ticker"), "line": float(match.group(1)), "over_ask_cents": int(yes * 100), "under_ask_cents": int(no * 100), "raw_sha256": raw})
    if len(lines) != 1:
        return {**unavailable, "reason": "ambiguous or incomplete Kalshi half-run markets" if lines else unavailable["reason"]}
    line = lines[0]
    return {"state": "observed_unknown_age", "reason": "source quote-update time is not supplied", "provider": "Kalshi exchange", "event_ticker": event.get("event_ticker"), "game_pk": game.get("game_pk"), "game_time": game.get("game_time"), "period": "full_game", "condition": f"Over {line['line']} runs scored", "price_format": "contract_cents", "currency": "USD", "over_ask_cents": line["over_ask_cents"], "under_ask_cents": line["under_ask_cents"], "source_updated_at": None, "observed_at": unavailable["observed_at"], "raw_sha256": line["raw_sha256"], "snapshot_id": hashlib.sha256(f"{line['raw_sha256']}:{game.get('game_pk')}".encode()).hexdigest()}
