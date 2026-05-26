"""Unit tests for the snapshot helpers. Docker integration is not
exercised here (would require a live daemon — that's `pytest -m
integration` territory)."""
from src.activities.snapshot import should_snapshot


def test_should_snapshot_fires_every_5_iterations():
    assert not should_snapshot(0, 0)
    assert not should_snapshot(4, 0)
    assert should_snapshot(5, 0)
    assert not should_snapshot(6, 5)
    assert should_snapshot(10, 5)


def test_should_snapshot_handles_first_snapshot():
    assert not should_snapshot(0, 0)
    assert should_snapshot(5, 0)


def test_snapshot_ref_model_roundtrip():
    from src.models import SnapshotRef

    ref = SnapshotRef(image_tag="autofix-snap:abc-5", iteration=5)
    dumped = ref.model_dump()
    parsed = SnapshotRef.model_validate(dumped)
    assert parsed == ref
    assert parsed.s3_bucket is None
