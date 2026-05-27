from src.models import ApprovalDecision, ApprovalRequest, ApprovalState
from src.activities.approval import _render_approval_comment


def test_approval_state_pending_is_undecided():
    s = ApprovalState(approval_id="a")
    assert not s.decided


def test_approval_state_settled_is_decided():
    s = ApprovalState(approval_id="a", pending=False, allowed=True, reason="ok")
    assert s.decided
    d = s.to_decision()
    assert isinstance(d, ApprovalDecision)
    assert d.allowed is True
    assert d.reason == "ok"


def test_approval_request_round_trip():
    req = ApprovalRequest(
        approval_id="abc", tool_name="Bash",
        tool_input={"command": "git push origin main"}, iteration=2,
    )
    dumped = req.model_dump()
    assert dumped["tool_name"] == "Bash"
    parsed = ApprovalRequest.model_validate(dumped)
    assert parsed == req


def test_approval_decision_default_is_deny():
    d = ApprovalDecision(allowed=False, reason="policy")
    assert d.allowed is False
    assert d.modified_input is None


def test_approval_comment_contains_resolvable_commands():
    req = ApprovalRequest(
        approval_id="aabbccddeeff00112233445566778899",
        tool_name="push_changes",
        tool_input={"summary": "fixed"},
        iteration=1,
    )
    body = _render_approval_comment(req)
    assert "/autofix approve aabbccddeeff00112233445566778899" in body
    assert "/autofix deny aabbccddeeff00112233445566778899 <reason>" in body
