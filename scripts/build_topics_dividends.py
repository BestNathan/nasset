"""Build dividend event history for the A-share topic, without fabricating missing data."""
import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "topics-universe.json"
OUTPUT = ROOT / "site" / "data" / "topics-dividends.json"


def annualize(events):
    """Assign ex-date cash dividends to calendar year; calendar year is NOT fiscal year."""
    result = {}
    for date, amount in events.items():
        if amount is None or float(amount) <= 0:
            continue
        year = str(date.year)
        result[year] = result.get(year, 0.0) + float(amount)
    return {year: round(value, 6) for year, value in sorted(result.items())}


def statistics(years):
    """Only contiguous years with observed data can support a cut-count."""
    values = [(int(y), float(v)) for y, v in sorted(years.items())]
    cuts = sum(values[i][0] == values[i - 1][0] + 1 and values[i][1] < values[i - 1][1]
               for i in range(1, len(values)))
    first, last = (values[0], values[-1]) if values else (None, None)
    cagr = ((last[1] / first[1]) ** (1 / (last[0] - first[0])) - 1) * 100 if (
        first and last and last[0] > first[0] and first[1] > 0) else None
    return {"observed_years": len(values), "observed_cuts": cuts,
            "cagr_pct": round(cagr, 3) if cagr is not None else None,
            "first_year": first[0] if first else None, "last_year": last[0] if last else None}


def main():
    universe = json.loads(REGISTRY.read_text(encoding="utf-8"))
    result = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "basis": "Ex-dividend calendar year, unadjusted per-share cash amounts from Yahoo Finance. NOT fiscal-year attribution. No zero-filling missing years.",
              "stocks": {}}
    for item in universe:
        symbol = item["symbol"]
        try:
            events = yf.Ticker(symbol).dividends
            years = annualize(events)
            result["stocks"][symbol] = {"years": years, "statistics": statistics(years),
                                        "source": "Yahoo Finance / yfinance", "status": "observed" if years else "missing"}
        except Exception as exc:
            result["stocks"][symbol] = {"years": {}, "statistics": statistics({}),
                                        "status": "error", "error": str(exc)[:240]}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
