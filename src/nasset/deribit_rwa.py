"""Curated Deribit RWA perpetual collector, isolated from crypto derivatives.

Source classification: https://support.deribit.com/hc/en-us/articles/38325634622493-RWA-Perpetual
The API is authoritative for *listed instruments*, while the curated
categories are authoritative for classifying RWA versus crypto.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from .deribit_client import DeribitClient, DeribitError

_FLOAT_FIELDS = (
    "contract_size", "tick_size", "min_trade_amount", "mark_price",
    "index_price", "bid_price", "ask_price", "last", "open_interest",
    "volume", "volume_usd", "interest_rate", "current_funding", "funding_8h",
)


def _native_float(value: Any, *, field: str, name: str) -> float | None:
    if value is None:
        return None
    if type(value) not in (float, int) or not math.isfinite(value):
        raise DeribitError(f"RWA {name}: invalid {field}")
    if field in {"mark_price", "index_price", "contract_size", "tick_size",
                 "min_trade_amount", "open_interest", "volume", "volume_usd"} and value < 0:
        raise DeribitError(f"RWA {name}: negative {field}")
    return float(value)


def collect_rwa_perpetual(
    client: DeribitClient, categories: Mapping[str, list[str]],
) -> tuple[list[dict[str, Any]], datetime]:
    """Read active listed USDC linear RWA perpetuals, preserving native units.

    Never infer a stock/share count or rewrite pre-IPO price to a post-IPO
    equivalent. A configured but unlisted ticker produces no fabricated row.
    Fail closed if a listed instrument has a missing/malformed quote.
    """
    registry = {symbol: category for category, symbols in categories.items()
                for symbol in symbols}
    if len(registry) != sum(len(v) for v in categories.values()):
        raise ValueError("duplicate RWA category ticker")
    catalog = client.instruments(kinds=("future",))
    selected: dict[str, dict[str, Any]] = {}
    for instrument in catalog:
        symbol = instrument.get("base_currency")
        if symbol not in registry:
            continue
        name = f"{symbol}_USDC-PERPETUAL"
        if instrument["instrument_name"] != name:
            continue
        if (instrument.get("kind") != "future"
                or instrument.get("settlement_currency") != "USDC"):
            raise DeribitError(f"RWA {name}: invalid instrument identity/settlement")
        if instrument.get("is_active") is False:
            continue
        selected[name] = instrument
    quotes: dict[str, dict[str, Any]] = {}
    if selected:
        response = client.call("get_book_summary_by_currency",
                               currency="USDC", kind="future")
        if not isinstance(response, list):
            raise DeribitError("RWA USDC futures summaries: expected list")
        for quote in response:
            if not isinstance(quote, dict) or not quote.get("instrument_name"):
                raise DeribitError("RWA USDC futures summaries: malformed row")
            name = quote["instrument_name"]
            if name not in selected:
                continue
            if name in quotes:
                raise DeribitError(f"RWA duplicate summary for {name}")
            quotes[name] = quote
        missing = set(selected) - set(quotes)
        if missing:
            raise DeribitError(f"RWA missing book summaries for {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    for name, instrument in sorted(selected.items()):
        symbol = instrument["base_currency"]
        category = registry[symbol]
        quote = quotes[name]
        row: dict[str, Any] = {
            "instrument_name": name,
            "base_currency": symbol,
            "category": category,
            "pricing_basis": ("pre_ipo_internal_reference" if category == "pre_ipo"
                              else "rwa_market_reference"),
            "quote_currency": "USDC",
            "settlement_currency": "USDC",
            "kind": "future",
            "instrument_type": instrument.get("instrument_type"),
            "price_index": instrument.get("price_index"),
            "expiration_timestamp": instrument.get("expiration_timestamp"),
            "is_active": instrument.get("is_active"),
        }
        for field in _FLOAT_FIELDS:
            value = (quote.get("last", quote.get("last_price")) if field == "last"
                     else quote.get(field) if field in quote
                     else instrument.get(field))
            row[field] = _native_float(value, field=field, name=name)
        if row["mark_price"] is None:
            raise DeribitError(f"RWA {name}: missing mark_price")
        rows.append(row)
    return rows, datetime.now(timezone.utc)
