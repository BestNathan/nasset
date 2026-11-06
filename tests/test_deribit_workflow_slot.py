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
    assert "DERIBIT_ARCHIVE_ENABLED == 'true'" in workflow
    assert 'default: false' in workflow


def test_deribit_workflow_pins_code_and_restricts_publish_to_main():
    workflow = Path(".github/workflows/deribit-archive.yml").read_text()
    # An intervening main commit must not change the implementation between
    # read-only acceptance and the privileged publication job.
    assert workflow.count('ref: ${{ github.sha }}') == 2
    assert 'ref: main' not in workflow
    # A manual dispatch from a feature branch is allowed to dry-run, but
    # must never be able to publish even when the repository gate is enabled.
    assert "github.ref == 'refs/heads/main' && vars.DERIBIT_ARCHIVE_ENABLED == 'true'" in workflow
    assert 'contents: write' in workflow
