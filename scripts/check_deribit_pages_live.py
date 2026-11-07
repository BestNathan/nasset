"""Verify that the public GitHub Pages URL serves the just-built Deribit JSON.

A successful upload/deploy API response does not prove the public endpoint
contains the new snapshot. Check the actual URL, not only the Actions artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


def verify_live(site_url: str, expected: dict, *, attempts: int = 12, delay: float = 5) -> None:
    if not site_url.startswith("https://"):
        raise ValueError("Pages URL must use HTTPS")
    url = urljoin(site_url.rstrip("/") + "/", "data/deribit-dashboard.json")
    html_url = urljoin(site_url.rstrip("/") + "/", "deribit.html")
    expected_commit = expected["archive_commit"]
    expected_slot = expected["latest_slot"]
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url + "?" + urlencode({"snapshot": expected_slot, "attempt": attempt}),
                headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            )
            with urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError(f"Pages JSON HTTP {response.status}")
                actual = json.load(response)
            if (actual.get("version") != 1 or actual.get("analytics", {}).get("version") != 1
                    or not actual.get("analytics", {}).get("assets", {}).get("BTC")):
                raise RuntimeError("Pages JSON missing required analytics model")
            if actual.get("archive_commit") != expected_commit or actual.get("latest_slot") != expected_slot:
                raise RuntimeError(
                    "Pages served a stale snapshot: "
                    + str(actual.get("latest_slot")) + " vs " + str(expected_slot)
                )
            # Verify the deployed HTML includes the same snapshot and inline
            # app, rather than merely proving that the sidecar JSON exists.
            html_request = Request(
                html_url + "?" + urlencode({"snapshot": expected_slot, "attempt": attempt}),
                headers={"Accept": "text/html", "Cache-Control": "no-cache"},
            )
            with urlopen(html_request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError(f"Pages HTML HTTP {response.status}")
                raw_html = response.read(8 * 1024 * 1024 + 1)
            if len(raw_html) > 8 * 1024 * 1024:
                raise RuntimeError("Pages HTML exceeds 8 MiB")
            html = raw_html.decode("utf-8")
            match = re.search(
                r'<script id="deribit-dashboard-bootstrap" type="application/json">\s*(.*?)\s*</script>',
                html, flags=re.DOTALL,
            )
            if not match or '<script data-nasset-deribit-inline-app>' not in html:
                raise RuntimeError("Pages served HTML without embedded snapshot/app")
            embedded = json.loads(match.group(1))
            if (embedded.get("archive_commit") != expected_commit
                    or embedded.get("latest_slot") != expected_slot
                    or embedded.get("analytics", {}).get("version") != 1):
                raise RuntimeError("Pages served stale or invalid embedded HTML snapshot")
            print(f"Public Pages JSON + standalone HTML verified: {expected_slot} / {expected_commit[:12]}")
            return
        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last_error = exc
            print(f"Pages public check {attempt + 1}/{attempts}: {exc}", flush=True)
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise RuntimeError("Public Deribit Pages HTML/JSON never became current") from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--expected", default="site/data/deribit-dashboard.json")
    args = parser.parse_args()
    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    verify_live(args.site_url, expected)


if __name__ == "__main__":
    main()
