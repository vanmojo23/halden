"""Deterministic disposition from the same two lookups the Claude agent calls.

Used when no Anthropic key is configured, and as the rule the system prompt states:
reject only a sanctions match whose date of birth agrees.
"""

from __future__ import annotations

from datetime import date, datetime

from kyc_agent.tools import (
    HIGH_RISK_COUNTRIES,
    ToolLog,
    dispatch,
    normalize_name,
)

# Floors. Extra blocking findings add one point, capped at 10.
_FLOORS = {
    "sanctions_exact": 10,
    "sanctions_alias": 9,
    "sanctions_unconfirmed": 7,
    "sanctions_dob_mismatch": 6,
    "media_high": 8,
    "media_medium": 5,
    "media_low": 2,
    "structuring": 7,
    "high_risk_country": 8,
    "expired_id": 5,
    "name_mismatch": 6,
}


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _money(amount: float, currency: str) -> str:
    return f"{amount:,.2f} {currency}"


def _finding(summary: str, severity: str, citations: list[dict]) -> dict:
    return {"summary": summary, "severity": severity, "citations": citations}


def structuring_indexes(transactions: list[dict]) -> list[int]:
    cash = []
    for index, txn in enumerate(transactions):
        amount = float(txn.get("amount") or 0)
        if (
            txn.get("channel") == "cash"
            and txn.get("direction") == "credit"
            and 9000 <= amount < 10000
        ):
            cash.append((index, _parse_date(txn["date"])))
    cash.sort(key=lambda item: item[1])
    flagged: set[int] = set()
    for start, (_, start_day) in enumerate(cash):
        cluster = [cash[start][0]]
        for index, day in cash[start + 1 :]:
            if (day - start_day).days <= 14:
                cluster.append(index)
            else:
                break
        if len(cluster) >= 2:
            flagged.update(cluster)
    return sorted(flagged)


def review_with_policy(application: dict, *, as_of: date | None = None) -> dict:
    """Stateless. Builds a new log and calls both lookups before scoring."""
    as_of = as_of or date.today()
    log = ToolLog()
    sanctions = dispatch(
        "sanctions_lookup",
        {
            "name": application.get("name"),
            "date_of_birth": application.get("date_of_birth"),
            "address": application.get("address"),
        },
        log,
    )
    media = dispatch(
        "adverse_media_lookup",
        {
            "name": application.get("name"),
            "date_of_birth": application.get("date_of_birth"),
        },
        log,
    )

    findings: list[dict] = []
    floors: list[int] = []
    blocking = 0
    reject = False

    for index, match in enumerate(sanctions["matches"]):
        cite = [
            {"source": "application.name", "detail": str(application.get("name") or "")},
            {
                "source": "application.date_of_birth",
                "detail": str(application.get("date_of_birth") or ""),
            },
            {
                "source": f"sanctions_lookup.matches[{index}]",
                "detail": (
                    f"{match['list_id']} · {match['match_type']} · "
                    f"listed {match['listed_name']} · dob {match['dob_status']} · {match['program']}"
                ),
            },
        ]
        confirmed = match["match_type"] in {"exact_name", "alias"} and match["dob_status"] == "match"
        if confirmed:
            reject = True
            blocking += 1
            floors.append(
                _FLOORS["sanctions_exact"] if match["match_type"] == "exact_name" else _FLOORS["sanctions_alias"]
            )
            findings.append(
                _finding(
                    (
                        f"{application.get('name')} and date of birth {application.get('date_of_birth')} "
                        f"match {match['list_id']} ({match['listed_name']}) on {match['program']}."
                    ),
                    "high",
                    cite,
                )
            )
        elif match["dob_status"] == "mismatch":
            blocking += 1
            floors.append(_FLOORS["sanctions_dob_mismatch"])
            findings.append(
                _finding(
                    (
                        f"The name matches {match['matched_on']} on {match['list_id']} "
                        f"({match['listed_name']}), but the dates of birth do not "
                        f"({application.get('date_of_birth')} vs {match['date_of_birth']}). "
                        "This may be a different person."
                    ),
                    "medium",
                    cite,
                )
            )
        else:
            blocking += 1
            floors.append(_FLOORS["sanctions_unconfirmed"])
            findings.append(
                _finding(
                    (
                        f"The name matches {match['list_id']} ({match['listed_name']}) "
                        f"as {match['match_type'].replace('_', ' ')}, and the date of birth "
                        f"does not clear it ({match['dob_status']})."
                    ),
                    "high",
                    cite,
                )
            )

    for article in media["articles"]:
        cite = [
            {"source": "application.name", "detail": str(application.get("name") or "")},
            {
                "source": "application.date_of_birth",
                "detail": str(application.get("date_of_birth") or ""),
            },
            {
                "source": f"adverse_media.{article['id']}",
                "detail": (
                    f"{article['headline']} · {article['source']} · {article['published']} · "
                    f"dob {article['dob_status']}"
                ),
            },
        ]
        if article["relevance"] == "name_only_dob_conflict":
            floors.append(_FLOORS["media_low"])
            findings.append(
                _finding(
                    (
                        f"Adverse media names {article['subject']} ({article['headline']}, "
                        f"{article['source']}, {article['published']}), but the article date of birth "
                        f"{article['date_of_birth']} does not match the applicant. Not treated as the same person."
                    ),
                    "low",
                    cite,
                )
            )
            continue
        blocking += 1
        if article["severity"] == "high":
            floors.append(_FLOORS["media_high"])
            severity = "high"
        elif article["severity"] == "medium":
            floors.append(_FLOORS["media_medium"])
            severity = "medium"
        else:
            floors.append(_FLOORS["media_low"])
            severity = "low"
        findings.append(
            _finding(
                (
                    f"Adverse media: {article['headline']} ({article['source']}, {article['published']}). "
                    f"{article['summary']}"
                ),
                severity,
                cite,
            )
        )

    transactions = application.get("transactions") or []
    risky = [
        (index, txn)
        for index, txn in enumerate(transactions)
        if str(txn.get("country") or "").upper() in HIGH_RISK_COUNTRIES
    ]
    if risky:
        blocking += 1
        floors.append(_FLOORS["high_risk_country"])
        listed = "; ".join(
            f"{txn['date']} {_money(float(txn['amount']), txn['currency'])} to {txn['counterparty']} ({txn['country']})"
            for _, txn in risky
        )
        findings.append(
            _finding(
                f"Transaction history includes a high-risk jurisdiction: {listed}.",
                "high",
                [
                    {
                        "source": f"application.transactions[{index}]",
                        "detail": (
                            f"{txn['date']} · {txn['direction']} · "
                            f"{_money(float(txn['amount']), txn['currency'])} · "
                            f"{txn['counterparty']} · {txn['country']} · {txn['channel']}"
                        ),
                    }
                    for index, txn in risky
                ],
            )
        )

    cluster = structuring_indexes(transactions)
    if cluster:
        blocking += 1
        floors.append(_FLOORS["structuring"])
        deposits = [transactions[index] for index in cluster]
        span = f"{deposits[0]['date']} to {deposits[-1]['date']}"
        amounts = ", ".join(_money(float(txn["amount"]), txn["currency"]) for txn in deposits)
        findings.append(
            _finding(
                (
                    f"{len(cluster)} cash deposits between 9,000 and 10,000 "
                    f"({amounts}) posted from {span}, each just under the 10,000 reporting threshold."
                ),
                "high",
                [
                    {
                        "source": f"application.transactions[{index}]",
                        "detail": (
                            f"{transactions[index]['date']} · cash credit · "
                            f"{_money(float(transactions[index]['amount']), transactions[index]['currency'])} · "
                            f"{transactions[index]['counterparty']}"
                        ),
                    }
                    for index in cluster
                ],
            )
        )

    document = application.get("id_document") or {}
    expiry_raw = document.get("expiry_date") or ""
    try:
        expired = _parse_date(expiry_raw) < as_of
    except ValueError:
        expired = True
    if expired:
        blocking += 1
        floors.append(_FLOORS["expired_id"])
        findings.append(
            _finding(
                (
                    f"The {document.get('document_type', 'identity document')} "
                    f"{document.get('document_number', '')} expired on {expiry_raw or 'an unknown date'}."
                ),
                "medium",
                [
                    {
                        "source": "application.id_document.expiry_date",
                        "detail": expiry_raw or "missing",
                    },
                    {
                        "source": "application.id_document.document_number",
                        "detail": str(document.get("document_number") or ""),
                    },
                ],
            )
        )

    app_name = str(application.get("name") or "")
    doc_name = str(document.get("name_on_document") or "")
    if normalize_name(app_name) != normalize_name(doc_name):
        blocking += 1
        floors.append(_FLOORS["name_mismatch"])
        findings.append(
            _finding(
                f"The name on the identity document is {doc_name or 'blank'}, which does not match the application name {app_name or 'blank'}.",
                "medium",
                [
                    {"source": "application.name", "detail": app_name},
                    {"source": "application.id_document.name_on_document", "detail": doc_name},
                ],
            )
        )

    if not findings:
        findings.append(
            _finding(
                (
                    "No sanctions match and no adverse-media match. "
                    "The name on the identity document matches the application, and the document is unexpired."
                ),
                "low",
                [
                    {"source": "sanctions_lookup", "detail": "hit_count 0"},
                    {"source": "adverse_media_lookup", "detail": "hit_count 0"},
                    {"source": "application.name", "detail": app_name},
                    {"source": "application.id_document.name_on_document", "detail": doc_name},
                    {
                        "source": "application.id_document.expiry_date",
                        "detail": str(expiry_raw),
                    },
                ],
            )
        )
        score = 1
        recommendation = "approve"
    else:
        score = min(10, max(floors) + max(0, blocking - 1))
        if reject:
            recommendation = "reject"
        elif blocking:
            recommendation = "escalate"
        else:
            recommendation = "approve"
            score = min(score, 3)

    return {
        "risk_score": score,
        "recommendation": recommendation,
        "findings": findings,
        "tool_log": log.entries,
        "engine": "policy",
    }
