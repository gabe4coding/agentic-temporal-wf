"""Worker Versioning helpers (Replay 2026 GA).

We don't boot a Worker here — just exercise the helpers and confirm
they read the env vars. End-to-end behaviour is upstream's responsibility
once `use_worker_versioning=_use_versioning()` is passed."""
from src.worker import _build_id, _use_versioning


def test_build_id_falls_back_to_dev(monkeypatch):
    monkeypatch.delenv("WORKER_BUILD_ID", raising=False)
    assert _build_id() == "dev"


def test_build_id_reads_env(monkeypatch):
    monkeypatch.setenv("WORKER_BUILD_ID", "abc123")
    assert _build_id() == "abc123"


def test_versioning_off_by_default(monkeypatch):
    monkeypatch.delenv("USE_WORKER_VERSIONING", raising=False)
    assert _use_versioning() is False


def test_versioning_on_when_env_set(monkeypatch):
    monkeypatch.setenv("USE_WORKER_VERSIONING", "1")
    assert _use_versioning() is True
