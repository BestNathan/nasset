"""Deribit public REST adapter: explicit identity joins, no storage or publishing.

All methods are injectable and testable without network access. API currency is
a settlement currency for derivatives, NOT the configured base/underlying.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping
import time
import math

import requests

API_ROOT = "https://www.deribit.com/api/v2/public/"
RETRYABLE = {429, 500, 502, 503, 504}


class DeribitError(RuntimeError):
    """API or market-data contract failure; never publish partial snapshots."""


@dataclass(frozen=True)
class MarketSnapshot:
    captured_at: datetime
    instruments: tuple[dict[str, Any], ...]
    options: tuple[dict[str, Any], ...]
    futures: tuple[dict[str, Any], ...]
    indices: tuple[dict[str, Any], ...]


class DeribitClient:
    def __init__(self, *, transport: Callable[[str, Mapping[str, Any]], Any] | None = None,
                 session: requests.Session | None = None, retries: int = 3):
        self._session = session or requests.Session()
        self._transport = transport
        self._retries = retries

    def call(self, method: str, **params: Any) -> Any:
        if self._transport is not None:
            result = self._transport(method, params)
            if result is None:
                raise DeribitError(f"{method}: null result")
            return result
        for attempt in range(self._retries + 1):
            try:
                response = self._session.get(API_ROOT + method, params=params, timeout=20)
                if response.status_code in RETRYABLE and attempt < self._retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("error") is not None or "result" not in body:
                    raise DeribitError(f"{method}: invalid response or exchange error: {body!r}")
                return body["result"]
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == self._retries:
                    raise DeribitError(f"{method}: transport retries exhausted") from exc
                time.sleep(min(2 ** attempt, 8))
            except (requests.HTTPError, ValueError) as exc:
                raise DeribitError(f"{method}: HTTP/JSON error") from exc
        raise AssertionError("unreachable")

    def volatility_candles(self, currency: str, *, start_ms: int, end_ms: int,
                           resolution: str = "3600") -> tuple[dict[str, Any], ...]:
        """Fetch native Deribit DVOL OHLC candles for an explicit supported asset.

        Deribit returns [timestamp_ms, open, high, low, close] tuples. A zero-
        candle result is not evidence of a valid market snapshot; fail closed.
        The caller owns the supported-currency allowlist (BTC/ETH in v1).
        """
        if not currency or start_ms >= end_ms or resolution != "3600":
            raise ValueError("invalid DVOL request interval/resolution")
        # Deribit source range bounds are inclusive. Our archive contract
        # is the half-open last-closed-hour interval [start_ms, end_ms).
        # Request one millisecond before the next hour so an opening candle
        # for the *current* incomplete hour cannot enter the snapshot.
        result = self.call("get_volatility_index_data", currency=currency,
                           start_timestamp=start_ms, end_timestamp=end_ms - 1,
                           resolution=resolution)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise DeribitError(f"DVOL {currency}: malformed result")
        if result.get("continuation") is not None:
            raise DeribitError(f"DVOL {currency}: truncated response; pagination required")
        rows = []
        seen = set()
        for candle in result["data"]:
            if (not isinstance(candle, (list, tuple)) or len(candle) != 5
                    or not isinstance(candle[0], int)
                    or any(type(v) not in (int, float) for v in candle[1:])):
                raise DeribitError(f"DVOL {currency}: invalid OHLC candle")
            timestamp = candle[0]
            opening, high, low, closing = candle[1:]
            if (not all(math.isfinite(v) and v >= 0 for v in (opening, high, low, closing))
                    or low > high or not low <= opening <= high
                    or not low <= closing <= high):
                raise DeribitError(f"DVOL {currency}: invalid OHLC values")
            if not start_ms <= timestamp < end_ms:
                raise DeribitError(
                    f"DVOL {currency}: candle {timestamp} outside closed interval "
                    f"[{start_ms}, {end_ms})"
                )
            if timestamp in seen:
                raise DeribitError(f"DVOL {currency}: duplicate candle {timestamp}")
            seen.add(timestamp)
            rows.append(dict(base_currency=currency, event_timestamp=timestamp,
                             open=candle[1], high=candle[2], low=candle[3],
                             close=candle[4]))
        if not rows:
            raise DeribitError(f"DVOL {currency}: no candles for requested hour")
        return tuple(sorted(rows, key=lambda row: row["event_timestamp"]))

    def historical_volatility(self, currency: str) -> tuple[dict[str, Any], ...]:
        """Read native historical volatility observations without inventing timestamps.

        The endpoint returns [timestamp_ms, volatility] pairs. This adapter
        deliberately returns the full source series: the archive layer must
        determine genuinely new events before creating immutable daily parts.
        """
        if currency not in {"BTC", "ETH"}:
            raise ValueError(f"historical volatility unsupported asset: {currency}")
        result = self.call("get_historical_volatility", currency=currency)
        if not isinstance(result, list):
            raise DeribitError(f"historical volatility {currency}: expected list")
        seen: dict[int, float] = {}
        for observation in result:
            if (not isinstance(observation, (list, tuple)) or len(observation) != 2
                    or type(observation[0]) is not int
                    or type(observation[1]) not in (int, float)):
                raise DeribitError(f"historical volatility {currency}: invalid observation")
            timestamp, value = observation
            if timestamp <= 0 or not math.isfinite(value) or value < 0:
                raise DeribitError(f"historical volatility {currency}: invalid value")
            if timestamp in seen and seen[timestamp] != value:
                raise DeribitError(f"historical volatility {currency}: conflicting timestamp")
            seen[timestamp] = value
        return tuple(dict(base_currency=currency, event_timestamp=timestamp,
                          volatility=value) for timestamp, value in sorted(seen.items()))

    def delivery_prices(self, index_name: str, *, page_size: int = 1000,
                        max_pages: int = 100) -> tuple[dict[str, Any], ...]:
        """Read all native finalized delivery events for one exact index name.

        Deribit's offset/count pagination returns data and records_total. Native
        `date` is a YYYY-MM-DD calendar date, NOT an epoch timestamp.
        Never confuse this finalized settlement series with the estimated
        delivery price in get_index_price. A genuinely empty series is valid.
        """
        if (not isinstance(index_name, str) or not index_name
                or not all(c.isascii() and (c.islower() or c.isdigit() or c == "_")
                           for c in index_name)):
            raise ValueError("invalid exact delivery index_name")
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("invalid delivery page_size")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("invalid delivery max_pages")
        events: dict[str, float] = {}
        total: int | None = None
        offset = 0
        for _ in range(max_pages):
            result = self.call("get_delivery_prices", index_name=index_name,
                               offset=offset, count=page_size)
            if (not isinstance(result, dict) or not isinstance(result.get("data"), list)
                    or type(result.get("records_total")) is not int
                    or result["records_total"] < 0):
                raise DeribitError(f"delivery {index_name}: malformed pagination")
            if total is None:
                total = result["records_total"]
            elif result["records_total"] != total:
                raise DeribitError(f"delivery {index_name}: source pagination changed")
            data = result["data"]
            if len(data) > page_size or offset + len(data) > total:
                raise DeribitError(f"delivery {index_name}: inconsistent page size")
            if offset < total and not data:
                raise DeribitError(f"delivery {index_name}: incomplete page")
            for item in data:
                # The native Deribit "date" is a YYYY-MM-DD calendar date,
                # not a millisecond epoch. Never manufacture an intraday
                # settlement timestamp or lose the source's date-only precision.
                if (not isinstance(item, dict) or type(item.get("date")) is not str
                        or type(item.get("delivery_price")) not in (int, float)
                        or not math.isfinite(item["delivery_price"])
                        or item["delivery_price"] < 0):
                    raise DeribitError(f"delivery {index_name}: invalid event")
                try:
                    stamp = date.fromisoformat(item["date"])
                except ValueError as exc:
                    raise DeribitError(f"delivery {index_name}: invalid date") from exc
                if stamp.isoformat() != item["date"]:
                    raise DeribitError(f"delivery {index_name}: invalid native date format")
                price = item["delivery_price"]
                stamp = item["date"]
                if stamp in events and events[stamp] != price:
                    raise DeribitError(f"delivery {index_name}: conflicting event")
                events[stamp] = price
            offset += len(data)
            if offset == total:
                return tuple(dict(index_name=index_name, event_date=stamp,
                                  delivery_price=price)
                             for stamp, price in sorted(events.items()))
        raise DeribitError(f"delivery {index_name}: pagination limit exceeded")

    def instruments(self, *, kinds: tuple[str, ...] = ("option", "future")) -> tuple[dict[str, Any], ...]:
        found: dict[str, dict[str, Any]] = {}
        for kind in kinds:
            data = self.call("get_instruments", currency="any", kind=kind, expired="false")
            if not isinstance(data, list):
                raise DeribitError(f"get_instruments({kind}): expected list")
            for item in data:
                if not isinstance(item, dict) or not item.get("instrument_name"):
                    raise DeribitError(f"get_instruments({kind}): malformed instrument")
                name = item["instrument_name"]
                if name in found and found[name] != item:
                    raise DeribitError(f"conflicting instrument metadata: {name}")
                found[name] = item
        return tuple(found.values())

    def snapshot(self, underlyings: tuple[str, ...], *,
                 include_options: bool = True, include_futures: bool = True,
                 include_indices: bool = True) -> MarketSnapshot:
        """Collect all relevant settlement partitions, joining exact instrument IDs.

        If any expected instrument is absent from the summary, abort the entire
        snapshot. A listed-but-inactive contract must be handled explicitly by a
        later coverage policy, never silently reported as a complete snapshot.
        """
        kinds = tuple(k for k, on in (("option", include_options), ("future", include_futures)) if on)
        if not kinds and not include_indices:
            raise ValueError("at least one dataset is required")
        # Index-only collection still needs contract metadata to discover real
        # price_index identifiers, but must not fetch any book summaries.
        catalog = self.instruments(kinds=kinds or ("option", "future"))
        scope = set(underlyings)
        selected = {i["instrument_name"]: i for i in catalog
                    if i.get("base_currency") in scope and (not kinds or i.get("kind") in kinds)}
        for asset in underlyings:
            if not any(i.get("base_currency") == asset for i in selected.values()):
                raise DeribitError(f"no listed contracts for configured underlying {asset}")
        quotes: dict[str, dict[str, Any]] = {}
        for kind in kinds:
            settlement_set = {i.get("settlement_currency") for i in selected.values()
                              if i.get("kind") == kind}
            if None in settlement_set or not settlement_set:
                raise DeribitError(f"{kind}: missing settlement currencies")
            for currency in sorted(settlement_set):
                result = self.call("get_book_summary_by_currency", currency=currency, kind=kind)
                if not isinstance(result, list):
                    raise DeribitError(f"get_book_summary_by_currency({currency}, {kind}): expected list")
                for quote in result:
                    if not isinstance(quote, dict) or not quote.get("instrument_name"):
                        raise DeribitError("malformed book summary")
                    name = quote["instrument_name"]
                    if name not in selected or selected[name].get("kind") != kind:
                        continue
                    if name in quotes and quotes[name] != quote:
                        raise DeribitError(f"conflicting book summaries for {name}")
                    quotes[name] = quote
        # Index-only discovery intentionally fetches instrument metadata but no
        # book summaries. Coverage applies only to requested quote kinds.
        missing = set(selected) - set(quotes) if kinds else set()
        if missing:
            raise DeribitError(f"missing book summaries for {len(missing)} listed instruments: "
                               + ", ".join(sorted(missing)[:5]))
        options: list[dict[str, Any]] = []
        futures: list[dict[str, Any]] = []
        for name, instrument in sorted(selected.items()):
            if not kinds:
                continue
            row = {**instrument, **quotes[name]}
            # Metadata identity wins over potentially incomplete quote fields.
            row.update({k: instrument[k] for k in ("instrument_name", "kind", "base_currency",
                        "settlement_currency") if k in instrument})
            (options if instrument["kind"] == "option" else futures).append(row)
        indices: list[dict[str, Any]] = []
        if include_indices:
            names = {i.get("price_index") for i in selected.values() if i.get("price_index")}
            # underlying_index can refer to a *future*, not a price index;
            # never blindly pass it to get_index_price.
            if not names:
                raise DeribitError("no index names discovered from instrument metadata")
            for name in sorted(names):
                value = self.call("get_index_price", index_name=name)
                if not isinstance(value, dict) or not isinstance(value.get("index_price"), (int, float)):
                    raise DeribitError(f"get_index_price({name}): invalid result")
                indices.append({"index_name": name, **value})
        return MarketSnapshot(
            captured_at=datetime.now(timezone.utc), instruments=tuple(selected.values()),
            options=tuple(options), futures=tuple(futures), indices=tuple(indices),
        )
