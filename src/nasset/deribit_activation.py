"""Strict, auditable operator activation gate for the Deribit archive.

This is independent of market-data source configuration: the collector can be
fully tested while live publication remains disabled. A repository commit
containing the exact activation document is the only automation opt-in.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ACTIVATION_FILE = Path("config/market-data-activation.json")


def validate_activation(document: Any) -> bool:
    if (not isinstance(document, dict)
            or set(document) != {"version", "enabled", "branch"}
            or type(document["version"]) is not int or document["version"] != 1
            or type(document["enabled"]) is not bool
            or document["branch"] != "market-data"):
        raise ValueError("invalid archive activation policy; refusing publication")
    return document["enabled"]


def load_activation(path: str | Path = ACTIVATION_FILE) -> bool:
    with Path(path).open(encoding="utf-8") as stream:
        return validate_activation(json.load(stream))


def main() -> None:
    enabled = load_activation()
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        raise RuntimeError("GITHUB_OUTPUT is required; refusing untracked activation")
    with open(output, "a", encoding="utf-8") as file:
        file.write(f"enabled={'true' if enabled else 'false'}\n")
    print(json.dumps({"production_archive_enabled": enabled, "branch": "market-data"}))


if __name__ == "__main__":
    main()
