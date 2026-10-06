"""Sanctions and adverse-media lookups. Both are pure functions over the mock lists."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger("kyc_agent")

DATA_DIR = Path(__file__).resolve().parent / "data"
HIGH_RISK_COUNTRIES = frozenset(
    {"IR", "KP", "SY", "CU", "RU", "BY", "MM", "VE", "YE", "AF", "SS"}
)

_MATCH_RANK = {"exact_name": 3, "alias": 2, "partial_name": 1}


def load_json(name: str):
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compare_dob(applicant: str | None, listed: str | None) -> str:
    left = (applicant or "").strip()
    right = (listed or "").strip()
    if not left and not right:
        return "unknown"
    if not left:
        return "not_provided"
    if not right:
        return "not_on_list"
    return "match" if left == right else "mismatch"


def _partial_kind(query: str, listed: str) -> str | None:
    q_tokens = query.split()
    l_tokens = listed.split()
    if len(q_tokens) < 2 or len(l_tokens) < 2:
        return None
    if len(q_tokens[-1]) < 4 or q_tokens[-1] != l_tokens[-1]:
        return None
    q_first, l_first = q_tokens[0], l_tokens[0]
    if q_first == l_first:
        return "partial_name"
    same_initial = q_first[:1] == l_first[:1]
    initial = len(q_first) == 1 or len(l_first) == 1
    prefix = len(q_first) >= 3 and len(l_first) >= 3 and q_first[:3] == l_first[:3]
    if same_initial and (initial or prefix):
        return "partial_name"
    return None


def _classify(query: str, listed: str, primary: bool) -> str | None:
    if query and query == listed:
        return "exact_name" if primary else "alias"
    return _partial_kind(query, listed)


def _address_overlap(query_address: str | None, listed_address: str | None) -> bool:
    query = normalize_name(query_address)
    listed = normalize_name(listed_address)
    if not query or not listed:
        return False
    listed_tokens = {token for token in listed.split() if len(token) >= 4}
    return any(token in query.split() for token in listed_tokens)


def sanctions_lookup(
    name: str,
    date_of_birth: str | None = None,
    address: str | None = None,
    entries: list | None = None,
) -> dict:
    """Search the sanctions list. A hit requires a name or alias match, never a date of birth alone."""
    query_name = normalize_name(name)
    matches = []
    if query_name:
        for entry in entries if entries is not None else load_json("sanctions.json"):
            names = [entry["name"], *entry.get("aliases", [])]
            best_type = None
            best_on = None
            for index, listed in enumerate(names):
                kind = _classify(query_name, normalize_name(listed), primary=index == 0)
                if kind and _MATCH_RANK[kind] > _MATCH_RANK.get(best_type, 0):
                    best_type = kind
                    best_on = listed
            if not best_type:
                continue
            matches.append(
                {
                    "list_id": entry["list_id"],
                    "listed_name": entry["name"],
                    "matched_on": best_on,
                    "match_type": best_type,
                    "date_of_birth": entry.get("date_of_birth"),
                    "dob_status": compare_dob(date_of_birth, entry.get("date_of_birth")),
                    "program": entry.get("program"),
                    "list_address": entry.get("address"),
                    "address_overlap": _address_overlap(address, entry.get("address")),
                    "remarks": entry.get("remarks"),
                }
            )
    return {
        "query": {"name": name, "date_of_birth": date_of_birth, "address": address},
        "hit_count": len(matches),
        "matches": matches,
    }


def adverse_media_lookup(
    name: str,
    date_of_birth: str | None = None,
    articles: list | None = None,
) -> dict:
    """Search adverse media by exact subject name or alias."""
    query_name = normalize_name(name)
    hits = []
    if query_name:
        for article in articles if articles is not None else load_json("adverse_media.json"):
            names = [article["subject"], *article.get("aliases", [])]
            if query_name not in {normalize_name(item) for item in names}:
                continue
            dob_status = compare_dob(date_of_birth, article.get("date_of_birth"))
            hits.append(
                {
                    "id": article["id"],
                    "headline": article["headline"],
                    "source": article["source"],
                    "published": article["published"],
                    "severity": article["severity"],
                    "category": article["category"],
                    "summary": article["summary"],
                    "subject": article["subject"],
                    "date_of_birth": article.get("date_of_birth"),
                    "dob_status": dob_status,
                    "relevance": (
                        "name_only_dob_conflict" if dob_status == "mismatch" else "subject_match"
                    ),
                }
            )
    return {
        "query": {"name": name, "date_of_birth": date_of_birth},
        "hit_count": len(hits),
        "articles": hits,
    }


class ToolLog:
    """One log per review. Nothing is kept after the function returns."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, tool: str, inputs: dict, outputs: dict) -> None:
        entry = {"tool": tool, "inputs": inputs, "outputs": outputs}
        self.entries.append(entry)
        logger.info("%s", json.dumps(entry, default=str))

    def called(self, tool: str) -> bool:
        return any(entry["tool"] == tool for entry in self.entries)


def _lookup_inputs(raw: dict, with_address: bool) -> dict:
    inputs = {
        "name": str(raw.get("name") or ""),
        "date_of_birth": raw.get("date_of_birth") or raw.get("dob"),
    }
    if with_address:
        inputs["address"] = raw.get("address")
    return inputs


def dispatch(tool_name: str, raw_input: dict, log: ToolLog) -> dict:
    """Run one tool and log the exact inputs and outputs."""
    raw_input = raw_input if isinstance(raw_input, dict) else {}
    if tool_name == "sanctions_lookup":
        inputs = _lookup_inputs(raw_input, with_address=True)
        outputs = sanctions_lookup(**inputs)
    elif tool_name == "adverse_media_lookup":
        inputs = _lookup_inputs(raw_input, with_address=False)
        outputs = adverse_media_lookup(**inputs)
    else:
        inputs = raw_input
        outputs = {"error": f"Unknown tool {tool_name}"}
    log.record(tool_name, inputs, outputs)
    return outputs
