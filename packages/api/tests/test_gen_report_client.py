from data_intelligence_api.application.gen_report_client import (
    _chat_request_payload,
)


def test_chat_payload_forwards_workspace_discovery_scope() -> None:
    payload = _chat_request_payload(
        conversation_id="gen-conv-1",
        message="Create a report",
        file_ids=[],
        language="vi",
        runtime_gateway={"endpoint": "http://runtime", "token": "secret"},
        execution_context={"run_id": "resp-1"},
        execution_files=[],
        organization_id="test-org",
        workspace_id="workspace-b",
        discover_workspace_files=True,
    )

    assert payload["organization_id"] == "test-org"
    assert payload["workspace_id"] == "workspace-b"
    assert payload["discover_workspace_files"] is True
