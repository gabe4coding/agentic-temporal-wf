"""Daily CVE check activity (Pattern-C hardening checklist).

Queries the GitHub Advisory feed for `@anthropic-ai/claude-code` and
`claude-agent-sdk`. Returns the list of advisories whose CVSS score is
>= the configured threshold (default 7.0). The caller (a scheduled
Workflow or a CI cron) decides what to do with the list — open an
issue, page oncall, etc.

For the PoC the activity just returns the list and logs; opening a
GitHub issue is a downstream wiring left for the scheduling layer."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import httpx
from temporalio import activity


logger = logging.getLogger(__name__)


_ADVISORY_URL = "https://api.github.com/advisories"
_PACKAGES = [
    ("npm", "@anthropic-ai/claude-code"),
    ("pip", "claude-agent-sdk"),
]


@dataclass
class CveFinding:
    cve_id: str | None
    ghsa_id: str
    severity: str
    cvss: float
    summary: str
    ecosystem: str
    package: str
    url: str


def _parse_advisory(adv: dict, ecosystem: str, package: str) -> CveFinding:
    cvss_score = 0.0
    cvss_node = adv.get("cvss") or {}
    if isinstance(cvss_node, dict):
        cvss_score = float(cvss_node.get("score") or 0.0)
    return CveFinding(
        cve_id=adv.get("cve_id"),
        ghsa_id=adv.get("ghsa_id") or "",
        severity=adv.get("severity") or "",
        cvss=cvss_score,
        summary=adv.get("summary") or "",
        ecosystem=ecosystem,
        package=package,
        url=adv.get("html_url") or "",
    )


def filter_findings(
    advisories: Iterable[dict],
    ecosystem: str,
    package: str,
    *,
    cvss_threshold: float = 7.0,
) -> list[CveFinding]:
    """Filter `advisories` to those affecting `package` with CVSS
    >= threshold."""
    out: list[CveFinding] = []
    for adv in advisories:
        # Cross-reference the advisory's vulnerabilities list to confirm
        # it affects our package — the API filter is a hint, not gospel.
        vulns = adv.get("vulnerabilities") or []
        if not any(
            (v.get("package") or {}).get("name") == package
            and (v.get("package") or {}).get("ecosystem") == ecosystem
            for v in vulns
        ):
            continue
        f = _parse_advisory(adv, ecosystem, package)
        if f.cvss >= cvss_threshold:
            out.append(f)
    return out


def fetch_advisories(
    ecosystem: str,
    package: str,
    *,
    client: httpx.Client | None = None,
) -> list[dict]:
    """One HTTP call to the GitHub Advisory feed."""
    params = {"ecosystem": ecosystem, "affects": package}
    owned = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        r = c.get(_ADVISORY_URL, params=params)
        r.raise_for_status()
        return list(r.json() or [])
    finally:
        if owned:
            c.close()


@activity.defn
def daily_cve_check(cvss_threshold: float = 7.0) -> list[dict]:
    """Scheduled activity: query advisories for the pinned SDK
    packages and return findings whose CVSS is >= threshold.
    Returns plain dicts so Temporal payload-codec doesn't need a
    custom encoder for `CveFinding`."""
    all_findings: list[CveFinding] = []
    for ecosystem, pkg in _PACKAGES:
        try:
            advs = fetch_advisories(ecosystem, pkg)
        except Exception as e:
            logger.warning("CVE feed query for %s/%s failed: %s", ecosystem, pkg, e)
            continue
        all_findings.extend(
            filter_findings(advs, ecosystem, pkg, cvss_threshold=cvss_threshold)
        )
    payload = [f.__dict__ for f in all_findings]
    if payload:
        logger.warning("CVE findings >=%.1f: %s", cvss_threshold, payload)
    else:
        logger.info("no CVE findings >=%.1f", cvss_threshold)
    return payload
