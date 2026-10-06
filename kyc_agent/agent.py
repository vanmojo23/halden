"""Stateless KYC reviewer. Each call starts a new Claude conversation."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from kyc_agent.policy import review_with_policy
from kyc_agent.schema import validate_review
from kyc_agent.tools import ToolLog, dispatch

SYSTEM_PROMPT = (
    "You are a KYC reviewer. Call sanctions_lookup and adverse_media_lookup, "
    "then call submit_review. risk_score is an integer from 1 to 10. "
    "recommendation is approve, escalate, or reject. Every finding needs citations "
    "to the application field or tool record that triggered it. Reject only a "
    "sanctions match whose date of birth agrees. Do not carry context from any other case."
)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TURNS = 8

TOOLS = [
    {
        "name": "sanctions_lookup",
        "description": "Search the sanctions list for a person by name, date of birth, and address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "date_of_birth": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "address": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "adverse_media_lookup",
        "description": "Search the adverse-media database for a person by name and date of birth.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "date_of_birth": {"type": "string", "description": "ISO date YYYY-MM-DD"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "submit_review",
        "description": "Submit the final disposition after both lookups have been called.",
        "input_schema": {
            "type": "object",
            "properties": {
                "risk_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "recommendation": {
                    "type": "string",
                    "enum": ["approve", "escalate", "reject"],
                },
                "findings": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                            "citations": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "source": {"type": "string"},
                                        "detail": {"type": "string"},
                                    },
                                    "required": ["source", "detail"],
                                },
                            },
                        },
                        "required": ["summary", "severity", "citations"],
                    },
                },
            },
            "required": ["risk_score", "recommendation", "findings"],
        },
    },
]


def _block_to_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    kind = getattr(block, "type", None)
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if kind == "text":
        return {"type": "text", "text": block.text}
    raise TypeError(f"Unsupported content block {kind}")


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model did not return a review")
    return json.loads(cleaned[start : end + 1])


def _client_default():
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The Anthropic SDK is not installed. Install anthropic and set ANTHROPIC_API_KEY."
        ) from exc
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic()


def review_with_claude(
    application: dict,
    *,
    client: Any = None,
    model: str | None = None,
) -> dict:
    """Run one fresh tool-use loop. No messages are kept between calls."""
    anthropic_client = client or _client_default()
    chosen = model or os.environ.get("KYC_MODEL") or DEFAULT_MODEL
    log = ToolLog()
    messages: list[dict] = [
        {
            "role": "user",
            "content": "Application:\n" + json.dumps(application, indent=2, default=str),
        }
    ]

    for _ in range(MAX_TURNS):
        response = anthropic_client.messages.create(
            model=chosen,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        content = list(response.content)
        tool_uses = [block for block in content if getattr(block, "type", None) == "tool_use"]
        if tool_uses:
            messages.append({"role": "assistant", "content": [_block_to_dict(block) for block in content]})
            results = []
            submitted = None
            for block in tool_uses:
                if block.name == "submit_review":
                    if not log.called("sanctions_lookup") or not log.called("adverse_media_lookup"):
                        outputs = {
                            "error": "Call sanctions_lookup and adverse_media_lookup before submit_review."
                        }
                        log.record("submit_review", dict(block.input), outputs)
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(outputs),
                                "is_error": True,
                            }
                        )
                        continue
                    try:
                        review = validate_review(dict(block.input))
                    except ValueError as exc:
                        outputs = {"error": str(exc)}
                        log.record("submit_review", dict(block.input), outputs)
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(outputs),
                                "is_error": True,
                            }
                        )
                        continue
                    log.record("submit_review", dict(block.input), {"accepted": True})
                    submitted = review
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"accepted": True}),
                        }
                    )
                else:
                    outputs = dispatch(block.name, dict(block.input), log)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(outputs, default=str),
                        }
                    )
            if submitted is not None:
                return {**submitted, "tool_log": log.entries, "engine": "claude"}
            messages.append({"role": "user", "content": results})
            continue

        text = "".join(getattr(block, "text", "") for block in content if getattr(block, "type", None) == "text")
        if log.called("sanctions_lookup") and log.called("adverse_media_lookup") and text.strip():
            review = validate_review(_extract_json(text))
            return {**review, "tool_log": log.entries, "engine": "claude"}

        messages.append({"role": "assistant", "content": [_block_to_dict(block) for block in content] or [{"type": "text", "text": ""}]})
        messages.append(
            {
                "role": "user",
                "content": "Call sanctions_lookup and adverse_media_lookup, then submit_review.",
            }
        )

    raise RuntimeError("Review stopped before a disposition was submitted.")


def review(application: dict, *, engine: str = "auto", client: Any = None, model: str | None = None) -> dict:
    """Review one application from scratch.

    engine auto uses Claude when ANTHROPIC_API_KEY is set, otherwise the written rule
    over the same two tools. A client argument forces the Claude loop so tests can
    inject a fake SDK client.
    """
    if engine not in {"auto", "claude", "policy"}:
        raise ValueError("engine must be auto, claude, or policy")
    use_claude = client is not None or engine == "claude" or (
        engine == "auto" and bool(os.environ.get("ANTHROPIC_API_KEY"))
    )
    if use_claude:
        return review_with_claude(application, client=client, model=model)
    return review_with_policy(application)
