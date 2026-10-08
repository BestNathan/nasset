from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_site_has_required_mount_points():
    html = (ROOT / "site" / "index.html").read_text()
    required = [
        'id="freshness"',
        'id="summary"',
        'id="riskLadder"',
        'id="yieldLadder"',
        'id="assetNav"',
        'id="detail"',
    ]
    for marker in required:
        assert marker in html, f"missing site mount point: {marker}"


def test_site_assets_exist():
    assert (ROOT / "site" / "app.js").exists()
    assert (ROOT / "site" / "styles.css").exists()
    assert (ROOT / "site" / "data" / "latest.json").exists()
    assert (ROOT / "site" / "data" / "timeline.json").exists()
