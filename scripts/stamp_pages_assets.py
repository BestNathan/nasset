"""Prepare a self-contained Deribit research page from a verified archive snapshot.

The source HTML and JS stay readable and testable. Only the generated Pages
artifact embeds the snapshot and application so opening the page does not
depend on a second multi-megabyte fetch or a separate JS request.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "site"
START = "<!-- nasset-deribit-inline-start -->"
END = "<!-- nasset-deribit-inline-end -->"
EXTERNAL_SCRIPT = '<script src="./deribit.js" defer></script>'
METRIC_SCRIPT = '<script src="./metric-guide.js"></script>'


def stamp(root: Path = ROOT) -> None:
    page = root / "deribit.html"
    html = page.read_text(encoding="utf-8")
    if START in html:
        before, remainder = html.split(START, 1)
        if END not in remainder:
            raise ValueError("incomplete Deribit inline bundle")
        _, after = remainder.split(END, 1)
        html = before + METRIC_SCRIPT + '\n' + EXTERNAL_SCRIPT + after
    if html.count(EXTERNAL_SCRIPT) != 1 or html.count(METRIC_SCRIPT) != 1:
        raise ValueError("expected one unbundled Deribit app and metric guide")

    for name in ("styles.css", "deribit.css"):
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()[:12]
        html, count = re.subn(
            rf"\./{re.escape(name)}(?:\?v=[0-9a-f]{{12}})?(?=\")",
            f"./{name}?v={digest}",
            html,
        )
        if count != 1:
            raise ValueError(f"expected one asset reference for {name}; found {count}")

    raw = (root / "data" / "deribit-dashboard.json").read_bytes()
    snapshot = json.loads(raw)
    if (snapshot.get("version") != 1 or snapshot.get("analytics", {}).get("version") != 1
            or not snapshot.get("analytics", {}).get("assets", {}).get("BTC")
            or not snapshot.get("archive_commit") or not snapshot.get("latest_slot")):
        raise ValueError("invalid Deribit snapshot to embed in published HTML")
    # Prevent a contract/instrument value from terminating the JSON script.
    inline_json = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True)
    inline_json = (inline_json.replace("&", "\\u0026")
                   .replace("<", "\\u003c").replace(">", "\\u003e"))
    script = (root / "deribit.js").read_text(encoding="utf-8")
    metric_script = (root / "metric-guide.js").read_text(encoding="utf-8")
    if "</script" in script.lower() or "</script" in metric_script.lower():
        raise ValueError("embedded JavaScript contains an HTML script terminator")

    bundle = (
        START + "\n"
        + '<script id="deribit-dashboard-bootstrap" type="application/json">\n'
        + inline_json + "\n</script>\n"
        + '<script data-nasset-metric-guide>\n'
        + metric_script + "\n</script>\n"
        + '<script data-nasset-deribit-inline-app>\n'
        + script + "\n</script>\n"
        + END
    )
    html = html.replace(METRIC_SCRIPT, "").replace(EXTERNAL_SCRIPT, bundle)
    page.write_text(html, encoding="utf-8")
    print(
        "Deribit self-contained Pages bundle: "
        f"{len(raw)} snapshot bytes, {len(html.encode('utf-8'))} HTML bytes, "
        f"{snapshot['latest_slot']}"
    )


if __name__ == "__main__":
    stamp()
