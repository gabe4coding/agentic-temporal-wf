from src.activities.idempotency import tool_call_key


def test_key_is_deterministic():
    a = tool_call_key("wf-1", 3, "tooluse-abc")
    b = tool_call_key("wf-1", 3, "tooluse-abc")
    assert a == b


def test_key_changes_with_iteration():
    a = tool_call_key("wf-1", 3, "tooluse-abc")
    b = tool_call_key("wf-1", 4, "tooluse-abc")
    assert a != b


def test_key_changes_with_workflow_id():
    a = tool_call_key("wf-1", 3, "tooluse-abc")
    b = tool_call_key("wf-2", 3, "tooluse-abc")
    assert a != b


def test_key_changes_with_tool_use_id():
    a = tool_call_key("wf-1", 3, "tooluse-abc")
    b = tool_call_key("wf-1", 3, "tooluse-xyz")
    assert a != b


def test_key_is_32_chars():
    assert len(tool_call_key("wf", 0, "t")) == 32
