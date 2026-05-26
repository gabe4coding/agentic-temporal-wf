import json
import subprocess
from pathlib import Path

HOOKS = Path("plugins/tf-mitigations/hooks")


def _run_hook(script: str, payload: dict) -> tuple[int, str]:
    p = subprocess.run(
        ["python3", str(HOOKS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return p.returncode, p.stdout


def test_secret_scan_blocks_aws_key():
    rc, out = _run_hook(
        "secret_scan.py",
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/autofix-x/repo/foo.py",
                "new_string": "key = 'AKIAIOSFODNN7EXAMPLE'",
            },
        },
    )
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_secret_scan_allows_normal_edit():
    rc, out = _run_hook(
        "secret_scan.py",
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/autofix-x/repo/foo.py",
                "new_string": "x = 1",
            },
        },
    )
    assert rc == 0


def test_signed_trailer_verify_passes_with_trailer():
    rc, out = _run_hook(
        "signed_trailer_verify.py",
        {
            "tool_name": "mcp__repo__git_commit_and_push",
            "tool_input": {"message": "fix: lint\n\n[autofix-bot]"},
        },
    )
    assert rc == 0


def test_signed_trailer_verify_denies_without_trailer():
    rc, out = _run_hook(
        "signed_trailer_verify.py",
        {
            "tool_name": "mcp__repo__git_commit_and_push",
            "tool_input": {"message": "fix: lint"},
        },
    )
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
