"""Worker Versioning helpers (Replay 2026 GA).

We don't boot a Worker here — just exercise the `_build_id()` helper
and confirm it reads the env var. The end-to-end behaviour is
upstream's responsibility once `use_worker_versioning=True` is passed."""
from src.worker import _build_id


def test_build_id_falls_back_to_dev(monkeypatch):
    monkeypatch.delenv("WORKER_BUILD_ID", raising=False)
    assert _build_id() == "dev"


def test_build_id_reads_env(monkeypatch):
    monkeypatch.setenv("WORKER_BUILD_ID", "abc123")
    assert _build_id() == "abc123"
