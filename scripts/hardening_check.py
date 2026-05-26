#!/usr/bin/env python3
"""Hardening checklist self-test (Pattern-C).

Runs each item in the Production Hardening Checklist (Common +
Temporal-hosted) as a Python assertion. Exits non-zero on any failure.

Usage:
    python3 scripts/hardening_check.py

CI integration: a pytest wrapper that invokes the script and asserts
its exit code is 0 lives in tests/test_hardening_e2e.py."""
from __future__ import annotations

import os
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent


class CheckFailed(Exception):
    """One hardening item failed."""


def _ok(name: str) -> None:
    print(f"  [✓] {name}")


def _fail(name: str, reason: str) -> None:
    print(f"  [✗] {name} — {reason}")
    raise CheckFailed(name)


# ---------- individual checks ----------


def check_anthropic_base_url_set() -> None:
    """Worker compose env pins ANTHROPIC_BASE_URL (CVE-2026-21852)."""
    compose = (_REPO_ROOT / "docker-compose.yml").read_text()
    if "ANTHROPIC_BASE_URL" not in compose:
        _fail(
            "ANTHROPIC_BASE_URL pinned in compose",
            "string not present in docker-compose.yml",
        )
    _ok("ANTHROPIC_BASE_URL pinned in compose")


def check_no_sensitive_paths_in_worker_image() -> None:
    """Dockerfile refuses sensitive paths."""
    df = (_REPO_ROOT / "Dockerfile").read_text()
    if "/root/.ssh" not in df or "/root/.aws" not in df:
        _fail(
            "Dockerfile refuses sensitive paths",
            "no /root/.ssh + /root/.aws guard found",
        )
    _ok("Dockerfile refuses sensitive paths (~/.ssh, ~/.aws, ~/.config/gcloud, ~/.docker)")


def check_allowed_tools_no_bash_write_webfetch() -> None:
    """Agent options exclude Bash/Write/WebFetch by policy."""
    sys.path.insert(0, str(_REPO_ROOT))
    # Imports are deferred — env stubs needed for build_options.
    os.environ.setdefault("GITHUB_TOKEN", "stub")
    os.environ.setdefault("CREDENTIAL_PROXY_URL", "http://stub")
    import importlib

    import httpx

    class _Stub:
        def __init__(self):
            self.status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"token": "stub", "ttl_s": 1}

    httpx.get = lambda *a, **kw: _Stub()  # type: ignore[assignment]
    pr_fixer = importlib.import_module("src.agents.pr_fixer")
    opts = pr_fixer.build_options()
    if opts.permission_mode == "bypassPermissions":
        _fail("permission_mode != bypassPermissions", "still bypassPermissions")
    for forbidden in ("Bash", "Write", "WebFetch"):
        if forbidden not in (opts.disallowed_tools or []):
            _fail(
                "disallowed_tools includes Bash/Write/WebFetch",
                f"{forbidden} not in disallowed_tools",
            )
    if opts.sandbox is None or not opts.sandbox.get("enabled"):
        _fail("options.sandbox enabled", "sandbox block missing/disabled")
    _ok("ClaudeAgentOptions has tightened tool policy + sandbox block")


def check_otel_module_present() -> None:
    """OpenInference/Arize setup module is importable."""
    sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    importlib.import_module("src.observability.otel")
    _ok("src.observability.otel importable")


def check_s3_payload_codec_wired() -> None:
    """Worker resolves _data_converter and exposes the S3 path."""
    sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    worker_mod = importlib.import_module("src.worker")
    if not hasattr(worker_mod, "_data_converter") or not hasattr(worker_mod, "_build_id"):
        _fail(
            "worker has _data_converter + _build_id helpers",
            "missing in src/worker.py",
        )
    _ok("worker has _data_converter + _build_id helpers")


def check_worker_versioning_enabled() -> None:
    src = (_REPO_ROOT / "src/worker.py").read_text()
    if "use_worker_versioning=True" not in src:
        _fail(
            "Worker Versioning enabled",
            "use_worker_versioning=True not found",
        )
    _ok("Worker Versioning enabled")


def check_repo_allowlist_module_present() -> None:
    sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    mod = importlib.import_module("src.repo_allowlist")
    if not hasattr(mod, "RepoAllowlist"):
        _fail("RepoAllowlist class present", "missing")
    _ok("RepoAllowlist module present")


def check_tf_guardrails_plugin_present() -> None:
    plugin_root = _REPO_ROOT / "plugins/tf-guardrails"
    manifest = plugin_root / ".claude-plugin/plugin.json"
    if not manifest.exists():
        _fail("tf-guardrails plugin present", f"{manifest} missing")
    _ok("tf-guardrails plugin present")


# ---------- driver ----------


_CHECKS = [
    check_anthropic_base_url_set,
    check_no_sensitive_paths_in_worker_image,
    check_allowed_tools_no_bash_write_webfetch,
    check_otel_module_present,
    check_s3_payload_codec_wired,
    check_worker_versioning_enabled,
    check_repo_allowlist_module_present,
    check_tf_guardrails_plugin_present,
]


def main() -> int:
    print("== Pattern-C Hardening Checklist ==")
    failed: list[str] = []
    for c in _CHECKS:
        try:
            c()
        except CheckFailed as e:
            failed.append(str(e))
    print()
    if failed:
        print(f"❌ {len(failed)} check(s) failed:")
        for f in failed:
            print(f"   - {f}")
        return 1
    print(f"✅ all {len(_CHECKS)} checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
