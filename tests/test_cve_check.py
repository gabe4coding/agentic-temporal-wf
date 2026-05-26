"""Tests for the CVE check helpers — pure logic on canned advisory
payloads."""
from src.activities.cve_check import filter_findings


_HIGH_PAYLOAD = {
    "ghsa_id": "GHSA-xxxx",
    "cve_id": "CVE-2025-59536",
    "severity": "high",
    "cvss": {"score": 8.7},
    "summary": "Sample high-CVSS advisory",
    "html_url": "https://example",
    "vulnerabilities": [
        {"package": {"ecosystem": "npm", "name": "@anthropic-ai/claude-code"}},
    ],
}


_LOW_PAYLOAD = {
    "ghsa_id": "GHSA-yyyy",
    "cve_id": "CVE-2026-22222",
    "severity": "medium",
    "cvss": {"score": 4.5},
    "summary": "Low-CVSS",
    "html_url": "https://example/low",
    "vulnerabilities": [
        {"package": {"ecosystem": "npm", "name": "@anthropic-ai/claude-code"}},
    ],
}


_UNRELATED_PAYLOAD = {
    "ghsa_id": "GHSA-zzzz",
    "cve_id": "CVE-X",
    "severity": "critical",
    "cvss": {"score": 9.9},
    "summary": "Affects something else",
    "html_url": "https://example/other",
    "vulnerabilities": [
        {"package": {"ecosystem": "npm", "name": "left-pad"}},
    ],
}


def test_filter_findings_keeps_high_cvss():
    out = filter_findings(
        [_HIGH_PAYLOAD, _LOW_PAYLOAD],
        "npm", "@anthropic-ai/claude-code",
        cvss_threshold=7.0,
    )
    assert [f.ghsa_id for f in out] == ["GHSA-xxxx"]
    assert out[0].cvss == 8.7


def test_filter_findings_drops_unrelated_package():
    out = filter_findings(
        [_UNRELATED_PAYLOAD],
        "npm", "@anthropic-ai/claude-code",
        cvss_threshold=7.0,
    )
    assert out == []


def test_filter_findings_drops_below_threshold():
    out = filter_findings(
        [_HIGH_PAYLOAD], "npm", "@anthropic-ai/claude-code",
        cvss_threshold=9.0,
    )
    assert out == []
