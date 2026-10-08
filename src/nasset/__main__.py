from __future__ import annotations

import argparse
from pathlib import Path

from .engine import collect_snapshot, write_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate nasset daily snapshot")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    snapshot = collect_snapshot()
    write_outputs(snapshot, root)
    print(f"generated {len(snapshot['assets'])} assets at {snapshot['generated_at']} with {len(snapshot['errors'])} errors")
    for error in snapshot["errors"]:
        print(f"WARN {error['asset_id']}: {error['error']}")


if __name__ == "__main__":
    main()
