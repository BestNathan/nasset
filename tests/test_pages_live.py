"""Offline tests for Pages asset freshness and public deployment checks."""
from __future__ import annotations

import hashlib
import io
import json
import runpy
from pathlib import Path
from urllib.error import HTTPError

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
STAMP = runpy.run_path(str(ROOT / "scripts/stamp_pages_assets.py"))["stamp"]
VERIFY = runpy.run_path(str(ROOT / "scripts/check_deribit_pages_live.py"))["verify_live"]


def test_fingerprinted_asset_urls_are_deterministic(tmp_path):
    page = tmp_path / "deribit.html"
    page.write_text(
        '<link href="./styles.css"><link href="./deribit.css">'
        '<script src="./metric-guide.js"></script><script src="./deribit.js" defer></script>',
        encoding="utf-8",
    )
    for name in ("styles.css", "deribit.css", "deribit.js", "metric-guide.js"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    (tmp_path / "data").mkdir()
    snapshot = {
        "version": 1,
        "archive_commit": "a" * 40,
        "latest_slot": "2026-11-07T15:00:00+00:00",
        "analytics": {"version": 1, "assets": {"BTC": {"markets": {}}}},
        "source": 'dangerous </script><script>alert("x")</script>',
    }
    (tmp_path / "data" / "deribit-dashboard.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    STAMP(tmp_path)
    first = page.read_text(encoding="utf-8")
    for name in ("styles.css", "deribit.css"):
        digest = hashlib.sha256(name.encode()).hexdigest()[:12]
        assert f"./{name}?v={digest}" in first
    assert '<script id="deribit-dashboard-bootstrap" type="application/json">' in first
    assert '<script data-nasset-metric-guide>' in first
    assert '<script data-nasset-deribit-inline-app>' in first
    assert "deribit.js\" defer" not in first
    assert "</script><script>alert" not in first
    assert r"\u003c/script\u003e" in first
    STAMP(tmp_path)
    assert page.read_text(encoding="utf-8") == first


def test_public_data_check_rejects_missing_or_stale_snapshot(monkeypatch):
    expected = {
        "archive_commit": "a" * 40,
        "latest_slot": "2026-11-07T15:00:00+00:00",
    }
    delivered = {
        **expected,
        "version": 1,
        "analytics": {"version": 1, "assets": {"BTC": {"markets": {}}}},
    }
    page_snapshot = {**delivered}
    seen = []

    def respond(request, timeout):
        seen.append(request.full_url)
        if "/deribit.html?" in request.full_url:
            payload = (
                '<script id="deribit-dashboard-bootstrap" type="application/json">'
                + json.dumps(page_snapshot)
                + '</script><script data-nasset-deribit-inline-app></script>'
            ).encode()
        else:
            payload = json.dumps(delivered).encode()
        buf = io.BytesIO(payload)
        buf.status = 200
        return buf

    monkeypatch.setitem(VERIFY.__globals__, "urlopen", respond)
    VERIFY("https://example.github.io/nasset/", expected, attempts=1)
    assert seen and seen[0].startswith(
        "https://example.github.io/nasset/data/deribit-dashboard.json?"
    )
    assert seen[1].startswith("https://example.github.io/nasset/deribit.html?")
    delivered["latest_slot"] = "2026-11-07T14:00:00+00:00"
    with pytest.raises(RuntimeError, match="never became current"):
        VERIFY("https://example.github.io/nasset/", expected, attempts=1)
    delivered["latest_slot"] = expected["latest_slot"]
    delivered["analytics"] = {"version": 0, "assets": {}}
    with pytest.raises(RuntimeError, match="never became current"):
        VERIFY("https://example.github.io/nasset/", expected, attempts=1)
    delivered["analytics"] = {"version": 1, "assets": {"BTC": {"markets": {}}}}
    page_snapshot = {**delivered, "latest_slot": "2026-11-07T14:00:00+00:00"}
    with pytest.raises(RuntimeError, match="never became current"):
        VERIFY("https://example.github.io/nasset/", expected, attempts=1)


def test_both_pages_workflows_fingerprint_and_validate_public_json():
    for name in ("daily.yml", "deribit-pages.yml"):
        wf = yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)
        job = wf["jobs"]["generate" if name == "daily.yml" else "deploy"]
        steps = job["steps"]
        names = [step.get("name", "") for step in steps]
        assert names.index("Fingerprint static Pages assets") < names.index("Upload Pages artifact")
        assert names.index("Deploy Pages") < names.index("Verify public Deribit data is actually available")
        assert any("check_deribit_pages_live.py" in s.get("run", "") for s in steps)
