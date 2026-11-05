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
