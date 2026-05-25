"""Probe test: documents the claude_agent_sdk symbols we depend on.
If the SDK renames or removes any of these, this test fails loudly so we
know to update our wrappers."""


def test_sdk_exposes_expected_symbols():
    import claude_agent_sdk

    # Core surfaces
    assert hasattr(claude_agent_sdk, "query"), "query() is the one-shot entry point we use"
    assert hasattr(claude_agent_sdk, "ClaudeAgentOptions"), "options builder"
    assert hasattr(claude_agent_sdk, "tool"), "tool decorator for custom tools"
    assert hasattr(claude_agent_sdk, "create_sdk_mcp_server"), "SDK-MCP factory"

    # Message types we destructure on
    assert hasattr(claude_agent_sdk, "AssistantMessage"), "to log tool calls"
    assert hasattr(claude_agent_sdk, "ResultMessage"), "final result extraction"
