"""The tool loop, the log, and the five desk files."""

from __future__ import annotations

import json
import unittest
from datetime import date

from kyc_agent.agent import SYSTEM_PROMPT, review, review_with_claude
from kyc_agent.policy import review_with_policy
from kyc_agent.tools import load_json


class Block:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Response:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(
            {
                "model": kwargs.get("model"),
                "system": kwargs.get("system"),
                "messages": json.loads(json.dumps(kwargs.get("messages"), default=str)),
            }
        )
        if not self._responses:
            raise AssertionError("the scripted client ran out of responses")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _tool(name, tool_id, payload):
    return Response(
        [Block(type="tool_use", id=tool_id, name=name, input=payload)],
        "tool_use",
    )


def _submit(tool_id, review_body):
    return _tool("submit_review", tool_id, review_body)


VIKTOR_REVIEW = {
    "risk_score": 10,
    "recommendation": "reject",
    "findings": [
        {
            "summary": "Name and date of birth match SDN-104482.",
            "severity": "high",
            "citations": [
                {"source": "application.name", "detail": "Viktor Petrov"},
                {"source": "sanctions_lookup.matches[0]", "detail": "SDN-104482"},
            ],
        }
    ],
}


class ClaudeLoopTests(unittest.TestCase):
    def test_system_prompt_stays_short(self):
        self.assertLess(len(SYSTEM_PROMPT.split()), 80)
        self.assertIn("sanctions_lookup", SYSTEM_PROMPT)
        self.assertIn("adverse_media_lookup", SYSTEM_PROMPT)

    def test_loop_logs_every_tool_call_and_returns_the_submission(self):
        application = load_json("samples.json")[1]["application"]
        client = FakeClient(
            [
                _tool(
                    "sanctions_lookup",
                    "tool_1",
                    {
                        "name": application["name"],
                        "date_of_birth": application["date_of_birth"],
                        "address": application["address"],
                    },
                ),
                _tool(
                    "adverse_media_lookup",
                    "tool_2",
                    {
                        "name": application["name"],
                        "date_of_birth": application["date_of_birth"],
                    },
                ),
                _submit("tool_3", VIKTOR_REVIEW),
            ]
        )
        with self.assertLogs("kyc_agent", level="INFO") as captured:
            result = review_with_claude(application, client=client)

        self.assertEqual(result["recommendation"], "reject")
        self.assertEqual(result["risk_score"], 10)
        self.assertEqual(result["engine"], "claude")
        tools = [entry["tool"] for entry in result["tool_log"]]
        self.assertEqual(
            tools,
            ["sanctions_lookup", "adverse_media_lookup", "submit_review"],
        )
        sanctions = result["tool_log"][0]
        self.assertEqual(sanctions["inputs"]["name"], "Viktor Petrov")
        self.assertGreater(sanctions["outputs"]["hit_count"], 0)
        self.assertIn("SDN-104482", json.dumps(sanctions["outputs"]))
        self.assertTrue(any("sanctions_lookup" in line for line in captured.output))
        self.assertTrue(any("adverse_media_lookup" in line for line in captured.output))

        first_messages = client.messages.calls[0]["messages"]
        self.assertEqual(len(first_messages), 1)
        self.assertEqual(client.messages.calls[0]["system"], SYSTEM_PROMPT)
        second_user = client.messages.calls[1]["messages"][-1]
        self.assertEqual(second_user["content"][0]["type"], "tool_result")
        self.assertIn("SDN-104482", second_user["content"][0]["content"])

    def test_each_run_starts_fresh(self):
        elena = load_json("samples.json")[0]["application"]
        viktor = load_json("samples.json")[1]["application"]

        def script_for(application):
            body = {
                "risk_score": 1,
                "recommendation": "approve",
                "findings": [
                    {
                        "summary": "No derogatory information.",
                        "severity": "low",
                        "citations": [{"source": "application.name", "detail": application["name"]}],
                    }
                ],
            }
            return [
                _tool("sanctions_lookup", "s", {"name": application["name"]}),
                _tool("adverse_media_lookup", "m", {"name": application["name"]}),
                _submit("r", body),
            ]

        client = FakeClient([*script_for(elena), *script_for(viktor)])
        review(elena, client=client)
        review(viktor, client=client)

        viktor_first = client.messages.calls[3]
        self.assertEqual(len(viktor_first["messages"]), 1)
        blob = json.dumps(viktor_first["messages"])
        self.assertIn("Viktor Petrov", blob)
        self.assertNotIn("Elena Voss", blob)

    def test_submit_before_lookups_is_rejected_and_the_loop_continues(self):
        application = {"name": "Elena Voss", "date_of_birth": "1988-02-14", "address": "Berlin"}
        client = FakeClient(
            [
                _submit("early", VIKTOR_REVIEW),
                _tool("sanctions_lookup", "s", {"name": "Elena Voss"}),
                _tool("adverse_media_lookup", "m", {"name": "Elena Voss"}),
                _submit(
                    "ok",
                    {
                        "risk_score": 1,
                        "recommendation": "approve",
                        "findings": [
                            {
                                "summary": "Clear.",
                                "severity": "low",
                                "citations": [{"source": "sanctions_lookup", "detail": "hit_count 0"}],
                            }
                        ],
                    },
                ),
            ]
        )
        result = review_with_claude(application, client=client)
        self.assertEqual(result["recommendation"], "approve")
        self.assertEqual(result["tool_log"][0]["tool"], "submit_review")
        self.assertIn("error", result["tool_log"][0]["outputs"])
        self.assertEqual(len(client.messages.calls), 4)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]: case["application"] for case in load_json("samples.json")}
        self.as_of = date(2026, 10, 4)

    def _review(self, case_id):
        return review_with_policy(self.cases[case_id], as_of=self.as_of)

    def test_dispositions(self):
        expected = {
            "elena": ("approve", 1),
            "viktor": ("reject", 10),
            "amara": ("escalate", 9),
            "maria": ("escalate", 6),
            "samuel": ("escalate", 9),
        }
        for case_id, (recommendation, score) in expected.items():
            result = self._review(case_id)
            self.assertEqual(result["recommendation"], recommendation, case_id)
            self.assertEqual(result["risk_score"], score, case_id)
            self.assertEqual(result["engine"], "policy")
            self.assertEqual(
                [entry["tool"] for entry in result["tool_log"]],
                ["sanctions_lookup", "adverse_media_lookup"],
            )
            for finding in result["findings"]:
                self.assertTrue(finding["citations"])
                for citation in finding["citations"]:
                    self.assertTrue(citation["source"])
                    self.assertTrue(citation["detail"])

    def test_logs_are_not_shared_across_runs(self):
        first = self._review("elena")
        second = self._review("viktor")
        self.assertEqual(first["tool_log"][0]["inputs"]["name"], "Elena Voss")
        self.assertEqual(second["tool_log"][0]["inputs"]["name"], "Viktor Petrov")
        self.assertEqual(len(first["tool_log"]), 2)

    def test_viktor_citations_point_at_the_list_and_the_wire(self):
        result = self._review("viktor")
        blob = json.dumps(result["findings"])
        self.assertIn("SDN-104482", blob)
        self.assertIn("application.transactions[0]", blob)
        self.assertTrue(result["tool_log"][0]["outputs"]["matches"][0]["address_overlap"])

    def test_maria_is_not_rejected_on_a_conflicting_date_of_birth(self):
        result = self._review("maria")
        self.assertNotEqual(result["recommendation"], "reject")
        self.assertIn("different person", json.dumps(result["findings"]).lower())

    def test_samuel_cites_each_cash_deposit_and_the_expired_document(self):
        result = self._review("samuel")
        sources = [cite["source"] for finding in result["findings"] for cite in finding["citations"]]
        for index in range(4):
            self.assertIn(f"application.transactions[{index}]", sources)
        self.assertIn("application.id_document.expiry_date", sources)
        self.assertIn("application.id_document.name_on_document", sources)


if __name__ == "__main__":
    unittest.main()
