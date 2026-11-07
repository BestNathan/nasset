"""Fingerprint Deribit static assets in the generated Pages artifact.

GitHub Pages CDN and browser caches may otherwise mix an old JS bundle with
newly published analytics JSON. Source HTML remains clean; run after building.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "site"


def stamp(root: Path = ROOT) -> None:
    page = root / "deribit.html"
    html = page.read_text(encoding="utf-8")
    for name in ("styles.css", "deribit.css", "deribit.js"):
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()[:12]
        html, count = re.subn(
            rf"\./{re.escape(name)}(?:\?v=[0-9a-f]{{12}})?(?=\")",
            f"./{name}?v={digest}",
            html,
        )
        if count != 1:
            raise ValueError(f"expected one asset reference for {name}; found {count}")
    page.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    stamp()
    print("Deribit Pages assets fingerprinted")
