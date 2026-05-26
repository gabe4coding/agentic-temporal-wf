"""Unit tests for list_orphan_containers / reap_orphans.

Docker client is faked — these are pure logic tests."""
from src.activities.cleanup_orphans import (
    list_orphan_containers,
    reap_orphans,
)


class _FakeContainer:
    def __init__(self, id_: str, name: str) -> None:
        self.id = id_
        self.name = name
        self.stop_calls = 0
        self.remove_calls = 0

    def stop(self, timeout=None):
        self.stop_calls += 1

    def remove(self, force=False):
        self.remove_calls += 1


class _FakeContainersAPI:
    def __init__(self, containers):
        self._all = containers

    def list(self, all=False):  # docker-py signature
        return list(self._all)

    def get(self, cid):
        for c in self._all:
            if c.id == cid:
                return c
        raise KeyError(cid)


class _FakeClient:
    def __init__(self, containers):
        self.containers = _FakeContainersAPI(containers)


def test_list_orphans_skips_running_workflow():
    containers = [
        _FakeContainer("c1", "autofix-sbx-wf-1"),
        _FakeContainer("c2", "autofix-sbx-wf-2"),
        _FakeContainer("c3", "unrelated"),
    ]
    client = _FakeClient(containers)
    # wf-1 is running, wf-2 is orphan, unrelated doesn't match prefix
    orphans = list_orphan_containers(
        client,
        is_workflow_running=lambda wf_id: wf_id == "wf-1",
    )
    assert [name for _, name in orphans] == ["autofix-sbx-wf-2"]


def test_reap_orphans_counts_and_calls_stop_remove():
    c = _FakeContainer("c1", "autofix-sbx-wf-2")
    client = _FakeClient([c])
    count = reap_orphans(client, [("c1", "autofix-sbx-wf-2")])
    assert count == 1
    assert c.stop_calls == 1
    assert c.remove_calls == 1


def test_reap_orphans_swallows_individual_failures():
    class _BadContainer(_FakeContainer):
        def stop(self, timeout=None):
            raise RuntimeError("boom")

    good = _FakeContainer("c1", "autofix-sbx-wf-1")
    bad = _BadContainer("c2", "autofix-sbx-wf-2")
    client = _FakeClient([good, bad])
    count = reap_orphans(
        client,
        [("c1", "autofix-sbx-wf-1"), ("c2", "autofix-sbx-wf-2")],
    )
    assert count == 1  # only the good one
