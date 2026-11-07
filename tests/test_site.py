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


def test_shared_research_navigation():
    links = ['./index.html', './topics.html', './deribit.html']
    for filename, selected in zip(('index.html', 'topics.html', 'deribit.html'), links):
        html = (ROOT / 'site' / filename).read_text()
        nav = html.split('<nav class="site-nav" aria-label="全站主导航">', 1)[1].split('</nav>', 1)[0]
        assert [nav.index('href="' + href + '"') for href in links] == sorted(
            nav.index('href="' + href + '"') for href in links
        )
        assert nav.count('aria-current="page"') == 1
        assert 'href="' + selected + '" class="active" aria-current="page"' in nav
    assert '.site-header' in (ROOT / 'site' / 'styles.css').read_text()


def test_research_sections_prioritize_decision_workflow():
    home = (ROOT / 'site' / 'index.html').read_text()
    assert home.index('id="asset-research"') < home.index('id="asset-comparison"') < home.index('id="risk-framework"')
    assert '<nav id="assetNav"' in home and '<details class="method-disclosure">' in home
    topics = (ROOT / 'site' / 'topics.html').read_text()
    assert topics.index('id="company-research"') < topics.index('id="company-compare"') < topics.index('id="dividend-methods"')
    assert 'id="topic-compare"' in topics and 'id="topic-industries"' in topics
    derivatives = (ROOT / 'site' / 'deribit.html').read_text()
    for asset in ('BTC', 'ETH', 'PAXG'):
        assert f'data-asset="{asset}"' in derivatives
    for tab in ('surface', 'risk', 'futures', 'options'):
        assert f'data-tab="{tab}"' in derivatives
