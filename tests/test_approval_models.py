from src.models import ApprovalDecision, ApprovalRequest, ApprovalState


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
