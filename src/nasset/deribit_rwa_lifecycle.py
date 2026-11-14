"""Versioned RWA identity, lifecycle and verified price-basis continuity.

Exchange catalogs prove only *observed* presence/absence, not legal listing
or delisting time. Price rebases require a sourced corporate-action declaration
AND a catalog-observed successor; price jumps never infer corporate actions.
Raw Parquet prices remain immutable and are never silently adjusted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
import json
import math
import re

from .deribit_client import DeribitError

_VERSION = 1
_NAME = re.compile(r"^[A-Z0-9]+_USDC-PERPETUAL$")
_KINDS = {"price_rebase"}
_STATUSES = {"active", "inactive", "missing_unconfirmed", "delisted_inferred"}
_SPEC_KEYS = ("base_currency", "settlement_currency", "kind", "instrument_type",
              "product_group", "price_index", "contract_size", "tick_size",
              "min_trade_amount", "expiration_timestamp")
_ACTION_KEYS = {"id", "kind", "from_instrument", "to_instrument",
                "effective_at", "price_divisor", "quantity_multiplier",
                "source_url", "target_pricing_basis"}


def _stamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be UTC ISO string")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid UTC timestamp: {value!r}") from exc
    if date.tzinfo is None or date.utcoffset().total_seconds() != 0:
        raise ValueError("event timestamp must be UTC aware")
    return date.astimezone(timezone.utc)


def validate_actions(actions: Any) -> tuple[dict[str, Any], ...]:
    """Reject unverified conversions, loops, name conflicts and invalid factors."""
    if not isinstance(actions, list):
        raise ValueError("RWA corporate_actions must be an array")
    seen_ids: set[str] = set()
    sources: set[str] = set()
    targets: set[str] = set()
    result = []
    for item in actions:
        if not isinstance(item, dict) or set(item) != _ACTION_KEYS:
            raise ValueError("RWA action requires complete sourced conversion fields")
        src, dst = item["from_instrument"], item["to_instrument"]
        if (not isinstance(src, str) or not isinstance(dst, str)
                or not _NAME.fullmatch(src) or not _NAME.fullmatch(dst)
                or src == dst or src in sources or dst in targets):
            raise ValueError("invalid or duplicate RWA conversion instrument")
        event_id = item["id"]
        if (not isinstance(event_id, str) or not re.fullmatch(r"[a-z0-9_-]{3,100}", event_id)
                or event_id in seen_ids or item["kind"] not in _KINDS):
            raise ValueError("invalid/duplicate RWA action identifier or kind")
        _stamp(item["effective_at"])
        if (not isinstance(item["source_url"], str)
                or not item["source_url"].startswith("https://")
                or not isinstance(item["target_pricing_basis"], str)
                or not item["target_pricing_basis"]):
            raise ValueError("RWA conversion requires a source URL and target price basis")
        try:
            a = Decimal(str(item["price_divisor"]))
            b = Decimal(str(item["quantity_multiplier"]))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("invalid RWA conversion factor") from exc
        if (type(item["price_divisor"]) not in (int, float)
                or type(item["quantity_multiplier"]) not in (int, float)
                or not a.is_finite() or not b.is_finite() or a <= 0 or b <= 0
                or abs(a - b) > Decimal("0.000000001")):
            raise ValueError("RWA price divisor must equal position quantity multiplier")
        seen_ids.add(event_id)
        sources.add(src)
        targets.add(dst)
        result.append(dict(item))
    # Each successor may itself be a source, but never form an identity cycle.
    successor = {x["from_instrument"]: x["to_instrument"] for x in result}
    for start in successor:
        visited = set()
        current = start
        while current in successor:
            if current in visited:
                raise ValueError("cyclic RWA identity/conversion chain")
            visited.add(current)
            current = successor[current]
    return tuple(result)


def _spec(item: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: item.get(key) for key in _SPEC_KEYS}
    if result["settlement_currency"] != "USDC" or result["kind"] != "future":
        raise DeribitError("invalid RWA lifecycle contract spec")
    if result["base_currency"] is None:
        raise DeribitError("RWA lifecycle spec lacks base currency")
    for key in ("contract_size", "tick_size", "min_trade_amount"):
        value = result[key]
        if value is not None and (type(value) not in (int, float)
                                  or not math.isfinite(value) or value < 0):
            raise DeribitError(f"invalid RWA lifecycle spec {key}")
    for key in ("base_currency", "settlement_currency", "kind", "instrument_type",
                "product_group", "price_index"):
        if result[key] is not None and not isinstance(result[key], str):
            raise DeribitError(f"invalid RWA lifecycle spec {key}")
    if (result["expiration_timestamp"] is not None and
            type(result["expiration_timestamp"]) is not int):
        raise DeribitError("invalid RWA lifecycle expiry")
    return result


def parse_state(raw: Any) -> dict[str, Any]:
    """Validate the small immutable prior catalog checkpoint before using it."""
    if (not isinstance(raw, dict) or raw.get("version") != _VERSION
            or raw.get("provider") != "deribit"
            or not isinstance(raw.get("contracts"), dict)
            or not isinstance(raw.get("events"), list)
            or not isinstance(raw.get("confirmed_actions"), list)):
        raise ValueError("malformed RWA lifecycle checkpoint")
    _stamp(raw.get("snapshot_slot_at"))
    _stamp(raw.get("observed_at"))
    prev = raw.get("previous_slot_at")
    if prev is not None and _stamp(prev) >= _stamp(raw["snapshot_slot_at"]):
        raise ValueError("invalid RWA checkpoint parent slot")
    for name, record in raw["contracts"].items():
        if not _NAME.fullmatch(name) or not isinstance(record, dict):
            raise ValueError("invalid RWA checkpoint instrument")
        if (record.get("status") not in _STATUSES
                or type(record.get("missing_observations")) is not int
                or record["missing_observations"] < 0
                or not isinstance(record.get("spec"), dict)):
            raise ValueError("invalid RWA checkpoint status/spec")
        _stamp(record.get("first_seen_at"))
        _stamp(record.get("last_seen_at"))
        _spec(record["spec"])
    for confirmed in raw["confirmed_actions"]:
        if (not isinstance(confirmed, dict)
                or set(confirmed) != {"id", "observed_at", "action"}
                or not isinstance(confirmed["id"], str)):
            raise ValueError("invalid confirmed RWA actions")
        _stamp(confirmed["observed_at"])
        declared = validate_actions([confirmed["action"]])[0]
        if confirmed["id"] != declared["id"]:
            raise ValueError("confirmed RWA action identifier mismatch")
    ids = [x["id"] for x in raw["confirmed_actions"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate confirmed RWA action")
    return raw


def build_state(slot: datetime, observed_at: datetime,
                inventory: Mapping[str, Mapping[str, Any]],
                previous: Mapping[str, Any] | None,
                actions: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    """Use previous *successful* archive checkpoint; never infer exchange event time."""
    if slot.tzinfo is None or slot.utcoffset().total_seconds() != 0:
        raise ValueError("slot must be UTC")
    if observed_at.tzinfo is None or observed_at.utcoffset().total_seconds() != 0:
        raise ValueError("capture must be UTC")
    if previous is not None:
        parse_state(previous)
        if _stamp(previous["snapshot_slot_at"]) >= slot:
            raise DeribitError("RWA previous checkpoint is not older than current slot")
    now = observed_at.isoformat()
    old = previous["contracts"] if previous else {}
    if old and not inventory and any(x["status"] == "active" for x in old.values()):
        raise DeribitError("RWA catalog unexpectedly empty after active inventory; retry rather than infer mass delisting")
    events: list[dict[str, Any]] = []
    contracts: dict[str, dict[str, Any]] = {}
    for name in sorted(set(old) | set(inventory)):
        prior = old.get(name)
        current = inventory.get(name)
        if current is None:
            missing = prior["missing_observations"] + 1
            status = "delisted_inferred" if missing >= 2 else "missing_unconfirmed"
            contracts[name] = {**prior, "missing_observations": missing, "status": status}
            if missing in (1, 2):
                events.append({"type": ("absence_observed" if missing == 1
                                        else "delisting_inferred"), "instrument_name": name,
                               "observed_at": now, "missing_observations": missing})
            continue
        if not _NAME.fullmatch(name):
            raise DeribitError(f"invalid RWA contract name {name}")
        spec = _spec(current)
        active = current.get("is_active") is not False
        status = "active" if active else "inactive"
        contracts[name] = {
            "spec": spec,
            "status": status,
            "missing_observations": 0,
            "first_seen_at": prior["first_seen_at"] if prior else now,
            "last_seen_at": now,
        }
        if prior is None:
            events.append({"type": "first_observed", "instrument_name": name,
                           "observed_at": now})
        else:
            if prior["status"] != status:
                change = ("relisted_observed" if prior["status"] == "delisted_inferred"
                          else "reappeared" if prior["status"] == "missing_unconfirmed"
                          else "reactivated" if status == "active"
                          else "inactive_observed")
                events.append({"type": change, "instrument_name": name,
                               "observed_at": now, "prior_status": prior["status"]})
            diff = {key: {"before": prior["spec"].get(key), "after": value}
                    for key, value in spec.items() if prior["spec"].get(key) != value}
            if diff:
                events.append({"type": "contract_spec_changed", "instrument_name": name,
                               "observed_at": now, "changes": diff})
                if "price_index" in diff:
                    # Price reference changed in observed metadata. This does
                    # NOT prove a post-IPO bridge or authorize price adjustment.
                    events.append({
                        "type": "pricing_reference_changed_observed",
                        "instrument_name": name, "observed_at": now,
                        "previous_index": diff["price_index"]["before"],
                        "new_index": diff["price_index"]["after"],
                        "conversion_status": "unverified_reference_transition",
                    })
    confirmed = list(previous["confirmed_actions"]) if previous else []
    declared = {x["id"]: dict(x) for x in actions}
    # Lock verified ratios, source URLs and identity links to archived data.
    # A later config edit must not silently recalculate historical performance.
    for item in confirmed:
        if declared.get(item["id"]) != item["action"]:
            raise DeribitError(f"confirmed RWA conversion changed or removed: {item['id']}")
    confirmed_ids = {item["id"] for item in confirmed}
    for action in actions:
        if action["id"] in confirmed_ids:
            continue
        src, dst = action["from_instrument"], action["to_instrument"]
        # Announcement alone NEVER authorizes adjustment. Require observed
        # legacy contract in a prior checkpoint AND live successor in catalog.
        if (_stamp(action["effective_at"]) <= observed_at
                and src in old and src not in inventory
                and dst in inventory and inventory[dst].get("is_active") is not False):
            event = {
                "type": "price_rebase_observed",
                "id": action["id"], "from_instrument": src, "to_instrument": dst,
                "observed_at": now,
                "announced_effective_at": action["effective_at"],
                "price_divisor": action["price_divisor"],
                "quantity_multiplier": action["quantity_multiplier"],
                "source_url": action["source_url"],
                "target_pricing_basis": action["target_pricing_basis"],
            }
            events.append(event)
            confirmed.append({"id": action["id"], "observed_at": now,
                              "action": dict(action)})
    return parse_state({
        "version": _VERSION,
        "provider": "deribit",
        "snapshot_slot_at": slot.isoformat(),
        "observed_at": now,
        "previous_slot_at": previous["snapshot_slot_at"] if previous else None,
        "contracts": contracts,
        "events": events,
        "confirmed_actions": confirmed,
    })


def state_payload(state: Mapping[str, Any]) -> bytes:
    parse_state(dict(state))
    return (json.dumps(state, ensure_ascii=True, sort_keys=True,
                       allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def price_basis(instrument_name: str, category: str,
                actions: tuple[Mapping[str, Any], ...],
                confirmed_action_ids: set[str]) -> str:
    for action in actions:
        if (action["id"] in confirmed_action_ids
                and action["to_instrument"] == instrument_name):
            return action["target_pricing_basis"]
    if category == "pre_ipo":
        return "pre_ipo_internal_reference"
    if category == "unclassified":
        return "unclassified_rwa_reference"
    return "rwa_market_reference"


def continuous_marks(rows: list[Mapping[str, Any]],
                     actions: tuple[Mapping[str, Any], ...],
                     confirmed: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Derived, display-only adjusted marks in the latest CONFIRMED basis.

    Output retains raw mark. An unconfirmed conversion does not join series.
    Recomputing from the versioned action ledger adjusts older history without
    ever rewriting archived Parquet price blobs. Supports multi-step rebases.
    """
    declared = {a["id"]: a for a in validate_actions(list(actions))}
    for item in confirmed:
        if (not isinstance(item, dict) or
                declared.get(item.get("id")) != item.get("action")):
            raise ValueError("continuous marks require source-pinned verified conversion")
    confirmed_ids = {x["id"] for x in confirmed}
    chain = {a["from_instrument"]: a for a in actions if a["id"] in confirmed_ids}
    dests = {a["to_instrument"]: a for a in chain.values()}
    output = []
    for row in rows:
        name = row["instrument_name"]
        raw = row["mark_price"]
        if type(raw) not in (int, float) or not math.isfinite(raw) or raw < 0:
            raise ValueError("invalid raw RWA mark")
        current = name
        divisor = Decimal(1)
        visited = set()
        while current in chain:
            if current in visited:
                raise ValueError("cyclic RWA continuous-price actions")
            visited.add(current)
            action = chain[current]
            divisor *= Decimal(str(action["price_divisor"]))
            current = action["to_instrument"]
        root = name
        while root in dests:
            root = dests[root]["from_instrument"]
        output.append({
            **row, "lineage_id": root,
            "canonical_instrument": current,
            "raw_mark_price": raw,
            "adjusted_mark_price": float(Decimal(str(raw)) / divisor),
            "price_adjustment_divisor": float(divisor),
            "price_adjustment_status": ("confirmed_rebased" if current != name
                                        or root != name else "native_no_conversion"),
        })
    return output
