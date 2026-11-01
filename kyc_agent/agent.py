"""Stateless KYC reviewer. Each call starts a new Gemini conversation."""

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

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_TURNS = 8

TOOLS = [
    {
        "name": "sanctions_lookup",
        "description": "Search the sanctions list for a person by name, date of birth, and address.",
        "parameters": {
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
        "parameters": {
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
        "parameters": {
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
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "The Google Gen AI SDK is not installed. Install google-genai and set GEMINI_API_KEY."
        ) from exc
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set.")
    return genai.Client()


def review_with_gemini(
    application: dict,
    *,
    client: Any = None,
    model: str | None = None,
) -> dict:
    """Run one fresh tool-use loop with Gemini. No messages are kept between calls."""
    from google.genai import types

    gemini_client = client or _client_default()
    chosen = model or os.environ.get("KYC_MODEL") or DEFAULT_MODEL
    log = ToolLog()

    tool = types.Tool(function_declarations=TOOLS)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[tool],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    contents: list[types.Content] = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="Application:\n" + json.dumps(application, indent=2, default=str))],
        )
    ]

    for _ in range(MAX_TURNS):
        response = gemini_client.models.generate_content(
            model=chosen,
            contents=contents,
            config=config,
        )

        function_calls = getattr(response, "function_calls", None) or []
        if function_calls:
            # Append the model's response (which contains the function calls)
            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            submitted = None
            response_parts = []
            for fc in function_calls:
                name = fc.name
                args = dict(fc.args) if fc.args else {}

                if name == "submit_review":
                    if not log.called("sanctions_lookup") or not log.called("adverse_media_lookup"):
                        outputs = {
                            "error": "Call sanctions_lookup and adverse_media_lookup before submit_review."
                        }
                        log.record("submit_review", args, outputs)
                        response_parts.append(
                            types.Part.from_function_response(
                                name=name,
                                response=outputs,
                                id=getattr(fc, "id", None),
                            )
                        )
                        continue
                    try:
                        review = validate_review(args)
                    except ValueError as exc:
                        outputs = {"error": str(exc)}
                        log.record("submit_review", args, outputs)
                        response_parts.append(
                            types.Part.from_function_response(
                                name=name,
                                response=outputs,
                                id=getattr(fc, "id", None),
                            )
                        )
                        continue
                    log.record("submit_review", args, {"accepted": True})
                    submitted = review
                    response_parts.append(
                        types.Part.from_function_response(
                            name=name,
                            response={"accepted": True},
                            id=getattr(fc, "id", None),
                        )
                    )
                else:
                    outputs = dispatch(name, args, log)
                    response_parts.append(
                        types.Part.from_function_response(
                            name=name,
                            response=outputs,
                            id=getattr(fc, "id", None),
                        )
                    )

            if submitted is not None:
                return {**submitted, "tool_log": log.entries, "engine": "gemini"}

            if response_parts:
                contents.append(types.Content(role="user", parts=response_parts))
            continue

        # No function calls — try to parse text as a review if lookups are done
        text = getattr(response, "text", "") or ""
        if log.called("sanctions_lookup") and log.called("adverse_media_lookup") and text.strip():
            try:
                review = validate_review(_extract_json(text))
                return {**review, "tool_log": log.entries, "engine": "gemini"}
            except (ValueError, json.JSONDecodeError):
                pass

        # Nudge the model
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="Call sanctions_lookup and adverse_media_lookup, then submit_review.")],
            )
        )

    raise RuntimeError("Review stopped before a disposition was submitted.")


def review(application: dict, *, engine: str = "auto", client: Any = None, model: str | None = None) -> dict:
    """Review one application from scratch.

    engine auto uses Gemini when GEMINI_API_KEY or GOOGLE_API_KEY is set, otherwise the written rule
    over the same two tools. A client argument forces the Gemini loop so tests can inject a fake client.
    """
    if engine not in {"auto", "gemini", "policy"}:
        raise ValueError("engine must be auto, gemini, or policy")
    use_gemini = client is not None or engine == "gemini" or (
        engine == "auto" and bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    )
    if use_gemini:
        return review_with_gemini(application, client=client, model=model)
    return review_with_policy(application)
