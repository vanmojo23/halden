"""Read one application from a file, a named sample, or stdin. Print the review as JSON."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from kyc_agent.agent import review
from kyc_agent.tools import load_json


def _application_from_args(args: argparse.Namespace) -> dict:
    if args.sample:
        for case in load_json("samples.json"):
            if case["id"] == args.sample:
                return case["application"]
        known = ", ".join(case["id"] for case in load_json("samples.json"))
        raise SystemExit(f"Unknown sample {args.sample}. Known samples: {known}")
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            return json.load(handle)
    if sys.stdin.isatty():
        raise SystemExit("Pass a JSON file, --sample, or a JSON application on stdin.")
    return json.load(sys.stdin)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kyc_agent", description="Review one KYC application.")
    parser.add_argument("file", nargs="?", help="Path to an application JSON file")
    parser.add_argument("--sample", help="Named sample application")
    parser.add_argument("--engine", default="auto", choices=["auto", "claude", "policy"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    application = _application_from_args(args)
    result = review(application, engine=args.engine)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
