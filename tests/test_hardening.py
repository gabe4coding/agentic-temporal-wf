"""Hardening checklist self-test wrapper. Runs the script and asserts
exit code 0."""
import subprocess
import sys
from pathlib import Path


def test_hardening_check_passes():
    script = Path(__file__).parent.parent / "scripts" / "hardening_check.py"
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, (
        f"hardening_check.py exited {r.returncode}\nstdout:\n{r.stdout}\n"
        f"stderr:\n{r.stderr}"
    )
