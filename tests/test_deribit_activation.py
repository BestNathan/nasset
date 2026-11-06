"""Repository-controlled activation and workflow fail-closed contracts."""
import json
from pathlib import Path

import pytest
import yaml

from nasset.deribit_activation import load_activation, main, validate_activation

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/market-data-activation.json"
WORKFLOW = ROOT / ".github/workflows/deribit-archive.yml"


def test_checked_in_policy_is_strictly_valid():
    assert type(load_activation(POLICY)) is bool


@pytest.mark.parametrize("bad", [
    {}, {"version": 1, "enabled": "true", "branch": "market-data"},
    {"version": True, "enabled": True, "branch": "market-data"},
    {"version": 1, "enabled": True, "branch": "main"},
    {"version": 1, "enabled": True, "branch": "market-data", "extra": 1},
])
def test_bad_activation_policy_fails_closed(bad):
    with pytest.raises(ValueError, match="invalid archive activation"):
        validate_activation(bad)


def test_enabled_policy_is_exact_boolean_not_truthy():
    assert validate_activation({"version": 1, "enabled": True, "branch": "market-data"})
    assert not validate_activation({"version": 1, "enabled": False, "branch": "market-data"})


def test_activation_step_writes_boolean_output(tmp_path, monkeypatch):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.chdir(ROOT)
    main()
    assert output.read_text() == f"enabled={str(load_activation(POLICY)).lower()}\n"


def test_workflow_fails_closed_until_activation_commit():
    root = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert root["on"]["schedule"] == [{"cron": "43 * * * *"}]
    assert root["on"]["push"] == {
        "branches": ["main"], "paths": ["config/market-data-activation.json"]
    }
    assert root["permissions"]["contents"] == "read"
    jobs = root["jobs"]
    assert set(jobs) == {"read-only-acceptance", "publish"}
    ro, publish = jobs["read-only-acceptance"], jobs["publish"]
    assert "github.ref == 'refs/heads/main'" in ro["if"]
    assert ro["outputs"]["enabled"] == "${{ steps.activation.outputs.enabled }}"
    assert "needs.read-only-acceptance.outputs.enabled == 'true'" in publish["if"]
    assert "inputs.publish == true" in publish["if"]
    assert "github.ref == 'refs/heads/main'" in publish["if"]
    assert publish["permissions"]["contents"] == "write"
    assert publish["needs"] == "read-only-acceptance"
    gate = next(x for x in ro["steps"] if x.get("id") == "activation")
    assert gate["run"] == "PYTHONPATH=src python -m nasset.deribit_activation"
    for s in ro["steps"]:
        if s.get("name") in {
            "Pin UTC slot across acceptance and publication",
            "Install archive dependencies", "Verify Deribit live source contract",
            "Build and validate due UTC-hour batch without Git writes",
        }:
            assert "steps.activation.outputs.enabled == 'true'" in s["if"]
            assert "github.event_name == 'workflow_dispatch'" in s["if"]
    for job in jobs.values():
        checkout = [x for x in job["steps"] if x.get("uses", "").startswith("actions/checkout@")]
        assert len(checkout) == 1
        assert checkout[0]["with"] == {
            "ref": "${{ github.sha }}", "fetch-depth": "1", "persist-credentials": "false",
        }
