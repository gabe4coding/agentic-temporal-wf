"""Refuses to run the agent on repositories not in the allowlist.

Pattern-C requirement (Sandboxing CVE callout):
'(c) refuse to run agents on repositories not in an allowlist'.

The allowlist is the set of repos that the deployment owners explicitly
listed in ALLOWED_REPOS. Empty/unset => deny everything (fail closed)."""
from __future__ import annotations

import os
from dataclasses import dataclass


class RepoDenied(Exception):
    """Raised when a repo is not in the allowlist."""


@dataclass(frozen=True)
class RepoAllowlist:
    repos: frozenset[str]

    @classmethod
    def from_env(cls) -> "RepoAllowlist":
        raw = os.environ.get("ALLOWED_REPOS", "")
        items = [s.strip() for s in raw.split(",") if s.strip()]
        return cls(frozenset(items))

    def __init__(self, repos):
        object.__setattr__(self, "repos", frozenset(repos))

    def check(self, owner: str, repo: str) -> None:
        slug = f"{owner}/{repo}"
        if slug not in self.repos:
            raise RepoDenied(f"{slug} not in allowlist")
