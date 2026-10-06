"""Validate the structured review the agent must return."""

from __future__ import annotations

RECOMMENDATIONS = {"approve", "escalate", "reject"}
SEVERITIES = {"low", "medium", "high"}


def validate_review(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Review must be an object")

    score = payload.get("risk_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("risk_score must be an integer from 1 to 10")
    if isinstance(score, float) and not score.is_integer():
        raise ValueError("risk_score must be an integer from 1 to 10")
    score_i = int(score)
    if not 1 <= score_i <= 10:
        raise ValueError("risk_score must be an integer from 1 to 10")

    recommendation = payload.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        raise ValueError("recommendation must be approve, escalate, or reject")

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        raise ValueError("findings must be a non-empty list")

    findings = []
    for finding in raw_findings:
        if not isinstance(finding, dict):
            raise ValueError("each finding must be an object")
        summary = finding.get("summary")
        severity = finding.get("severity")
        citations = finding.get("citations")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("each finding needs a summary")
        if severity not in SEVERITIES:
            raise ValueError("severity must be low, medium, or high")
        if not isinstance(citations, list) or not citations:
            raise ValueError("each finding needs citations")
        clean_citations = []
        for citation in citations:
            if not isinstance(citation, dict):
                raise ValueError("each citation must be an object")
            source = citation.get("source")
            detail = citation.get("detail")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("each citation needs a source")
            if not isinstance(detail, str) or not detail.strip():
                raise ValueError("each citation needs a detail")
            clean_citations.append({"source": source.strip(), "detail": detail.strip()})
        findings.append(
            {
                "summary": summary.strip(),
                "severity": severity,
                "citations": clean_citations,
            }
        )

    return {
        "risk_score": score_i,
        "recommendation": recommendation,
        "findings": findings,
    }
