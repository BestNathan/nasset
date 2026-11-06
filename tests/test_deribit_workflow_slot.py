"""Regression guard: acceptance and publish must use the identical UTC slot."""
from pathlib import Path


def test_deribit_workflow_pins_slot_across_jobs():
    workflow = Path(".github/workflows/deribit-archive.yml").read_text()
    assert 'id: slot' in workflow
    assert 'date -u +' in workflow
    assert 'outputs:\n      slot: ${{ steps.slot.outputs.slot }}' in workflow
    assert 'deribit_collect --config config/market-data.yml --at "${{ steps.slot.outputs.slot }}"' in workflow
    assert 'deribit_collect --config config/market-data.yml --at "${{ needs.read-only-acceptance.outputs.slot }}" --publish' in workflow
    assert workflow.count("deribit_collect --config") == 2
    assert "needs.read-only-acceptance.outputs.enabled == 'true'" in workflow
    assert "config/market-data-activation.json" in workflow
    assert 'default: false' in workflow


def test_deribit_workflow_pins_code_and_restricts_publish_to_main():
    workflow = Path(".github/workflows/deribit-archive.yml").read_text()
    # An intervening main commit must not change the implementation between
    # read-only acceptance and the privileged publication job.
    assert workflow.count('ref: ${{ github.sha }}') == 2
    assert 'ref: main' not in workflow
    # No feature-branch execution or publication: the checked-in exact
    # activation policy and main are both necessary for the write job.
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "needs.read-only-acceptance.outputs.enabled == 'true'" in workflow
    assert 'contents: write' in workflow


def test_external_aws_dispatch_slot_must_match_current_utc_hour():
    import yaml
    root = yaml.load(
        Path(".github/workflows/deribit-archive.yml").read_text(), Loader=yaml.BaseLoader,
    )
    params = root["on"]["workflow_dispatch"]["inputs"]
    assert params["slot"]["required"] == "false"
    assert params["slot"]["default"] == ""
    assert params["publish"]["default"] == "false"
    acceptance, writer = root["jobs"]["read-only-acceptance"], root["jobs"]["publish"]
    pin = next(step for step in acceptance["steps"] if step.get("id") == "slot")
    assert pin["env"]["REQUESTED_SLOT"] == "${{ inputs.slot }}"
    assert 'if [ -n "$REQUESTED_SLOT" ] && [ "$REQUESTED_SLOT" != "$current_slot" ]' in pin["run"]
    assert 'date -u +' in pin["run"]
    rollover = next(
        step for step in writer["steps"] if step.get("name", "").startswith("Reject UTC rollover"),
    )
    assert rollover["env"]["PINNED_SLOT"] == "${{ needs.read-only-acceptance.outputs.slot }}"
    assert '"$PINNED_SLOT" != "$(date -u +' in rollover["run"]
    assert writer["permissions"]["contents"] == "write"
