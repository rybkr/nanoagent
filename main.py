#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json

from nanoagent import MODEL
from orchestrator import run_orchestrated
from run_single import run_single_agent
from task_support import load_task_bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the lab workflow in either single-agent or orchestrated mode."
    )
    parser.add_argument("--task", required=True, help="Task directory")
    parser.add_argument(
        "--condition",
        choices=("single", "orchestrated"),
        required=True,
        help="Which workflow to run",
    )
    parser.add_argument("--model", default=MODEL, help="Model name to send to the backend")
    args = parser.parse_args()

    loaded = load_task_bundle(args.task, args.model, args.condition)
    if args.condition == "single":
        result = run_single_agent(
            issue_text=loaded["issue_text"],
            repo_summary=loaded["repo_summary"],
            config=loaded["config"],
        )
    else:
        result = run_orchestrated(
            issue_text=loaded["issue_text"],
            repo_summary=loaded["repo_summary"],
            config=loaded["config"],
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
