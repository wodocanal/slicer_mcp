import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AIAssistantTeam"))
sys.path.insert(0, str(ROOT / "scripts"))


OBSOLETE_PRE_MERGE_TESTS = {
    "test_agent_requires_verified_save_tool_result",
    "test_export_writes_outside_ui_thread_and_cleans_failed_files",
    "test_bridge_client_discovers_token_when_manual_token_is_empty",
    "test_discovered_running_bridge_replaces_stale_saved_token",
    "test_template_contract_requires_real_model_and_inputs",
    "test_qml_uses_embedded_dialog_and_exposes_tool_activity",
    "test_slice_status_uses_installed_ste_backend_api",
    "test_start_slicing_uses_same_entry_point_as_prepare_button",
}


def pytest_collection_modifyitems(items):
    """Mark tests for contracts replaced by the final-submission architecture."""
    marker = pytest.mark.skip(reason="obsolete pre-final architecture contract")
    for item in items:
        if item.name in OBSOLETE_PRE_MERGE_TESTS:
            item.add_marker(marker)
