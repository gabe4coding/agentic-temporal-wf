import json
from pathlib import Path


PLUGIN_ROOT = Path("plugins/tf-guardrails")


def test_plugin_manifest_present():
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "tf-guardrails"
    assert manifest["version"]


def test_plugin_hooks_present():
    hooks = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text())
    matchers = [h["matcher"] for h in hooks["hooks"]["PreToolUse"]]
    assert any("Bash" in m and "WebFetch" in m for m in matchers)


def test_restrict_paths_executable():
    p = PLUGIN_ROOT / "hooks/restrict_paths.py"
    assert p.exists()
    assert p.read_text().startswith("#!")
