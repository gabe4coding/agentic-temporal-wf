import pytest
from src.repo_allowlist import RepoAllowlist, RepoDenied

def test_allowlist_accepts_listed_repo() -> None:
    allow = RepoAllowlist(["lafourchette/playground", "lafourchette/web"])
    allow.check("lafourchette", "playground")

def test_allowlist_rejects_unknown_repo() -> None:
    allow = RepoAllowlist(["lafourchette/playground"])
    with pytest.raises(RepoDenied, match="lafourchette/other not in allowlist"):
        allow.check("lafourchette", "other")

def test_allowlist_from_env_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_REPOS", "lafourchette/playground,lafourchette/web")
    allow = RepoAllowlist.from_env()
    allow.check("lafourchette", "web")

def test_allowlist_empty_env_is_deny_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_REPOS", raising=False)
    allow = RepoAllowlist.from_env()
    with pytest.raises(RepoDenied):
        allow.check("lafourchette", "playground")
