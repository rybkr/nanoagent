#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json

from nanoagent import (
    MODEL,
    SYSTEM_PROMPT,
    call_api,
    enforce_tool_policy,
    get_trace_state,
    make_studio_tools,
    reset_trace_state,
    run_tool,
    update_tool_state,
)
from orchestrator import build_metrics
from task_support import (
    evaluate_acceptance,
    get_diff_summary,
    load_task_bundle,
    normalize_task_tool_args,
    truncate_text,
)


def build_task_prompt(issue_text, repo_summary, config):
    allowed_files = (
        ", ".join(config.allowed_files) if config.allowed_files else "(any files)"
    )
    return (
        "Fix the bug described below in the current repository checkout.\n\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary or '(none provided)'}\n\n"
        "Constraints:\n"
        f"- File budget: {config.file_budget}\n"
        f"- Allowed files: {allowed_files}\n"
        f"- Acceptance: {config.acceptance_description or 'Provided tests must pass and constraints must hold.'}\n"
        f"- Canonical test command: {config.test_command or '(none provided)'}\n"
        f"- Reproduction command: {config.reproduction_command or '(none provided)'}\n"
        "- Use tools to inspect and edit the repo.\n"
        "- All file tools are confined to the staged repo checkout.\n"
        "- Bash is restricted to read-only verification commands.\n"
        "- When the fix is complete, stop using tools and give a short summary.\n"
    )


def build_feedback(acceptance, reason):
    return (
        "The task is not yet accepted.\n"
        f"Reason: {reason}\n"
        f"Acceptance summary: {acceptance['reason']}\n"
        f"Tests passed: {acceptance['tests_passed']}\n"
        f"Constraints satisfied: {acceptance['constraints_satisfied']}\n"
        f"Modified files: {acceptance['modified_files']}\n"
        f"Test output:\n{truncate_text(str(acceptance['test_output']))}\n"
        "Continue only if a concrete next change is needed."
    )


def run_single_agent(issue_text, repo_summary, config):
    reset_trace_state()
    messages = [
        {"role": "user", "content": build_task_prompt(issue_text, repo_summary, config)}
    ]
    last_read_steps = {}
    last_mutations = {}
    last_reads = {}
    repeated_calls = {}
    tool_step = 0
    tool_call_total = 0
    final_text = ""
    stop_reason = "turn_limit"

    for _turn in range(1, config.max_single_agent_turns + 1):
        if get_trace_state()["cumulative_total"] >= config.max_total_tokens:
            stop_reason = "token_budget_exhausted"
            break

        response = call_api(
            model=config.model,
            max_tokens=config.max_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=make_studio_tools(),
            task_id=config.task_id,
            condition=config.condition,
            role="single",
        )
        final_text = response["assistant_text"]
        tool_results = []
        halt_reason = ""

        for i, tool_call in enumerate(response["tool_calls"]):
            tool_step += 1
            tool_name = tool_call["name"]
            tool_args, normalize_error = normalize_task_tool_args(
                tool_name,
                tool_call["arguments"],
                config.repo_path,
            )
            if tool_step > config.max_tool_iterations:
                result = "error: tool iteration limit reached for this run"
                halt_reason = "tool iteration limit reached"
            elif normalize_error is not None:
                result = normalize_error
            else:
                result = enforce_tool_policy(
                    tool_name,
                    tool_args,
                    messages[0]["content"],
                    last_read_steps,
                    last_mutations,
                    last_reads,
                    repeated_calls,
                    config.max_identical_tool_calls,
                ) or run_tool(tool_name, tool_args)
            if result.startswith("error: repeated identical"):
                halt_reason = "repeated identical tool request blocked"
            update_tool_state(
                tool_name,
                tool_args,
                result,
                tool_step,
                last_read_steps,
                last_mutations,
                last_reads,
                repeated_calls,
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.get("id", f"call_{i}"),
                    "content": result,
                }
            )

        messages.append({"role": "assistant", "content": response["content"]})
        tool_call_total += len(tool_results)
        if tool_results:
            acceptance = evaluate_acceptance(
                test_command=config.test_command,
                repo_path=config.repo_path,
                allowed_files=config.allowed_files,
                file_budget=config.file_budget,
            )
            if acceptance["accepted"]:
                stop_reason = "accepted"
                return {
                    "accepted": True,
                    "stop_reason": stop_reason,
                    "final_response": final_text,
                    "acceptance": acceptance,
                    "diff_summary": get_diff_summary(
                        config.repo_path, config.max_diff_lines
                    ),
                    "metrics": build_metrics(config, acceptance, tool_call_total),
                }
            messages.append({"role": "user", "content": tool_results})
            if halt_reason:
                messages.append(
                    {
                        "role": "user",
                        "content": build_feedback(acceptance, halt_reason),
                    }
                )
            continue

        acceptance = evaluate_acceptance(
            test_command=config.test_command,
            repo_path=config.repo_path,
            allowed_files=config.allowed_files,
            file_budget=config.file_budget,
        )
        if acceptance["accepted"]:
            stop_reason = "accepted"
            return {
                "accepted": True,
                "stop_reason": stop_reason,
                "final_response": final_text,
                "acceptance": acceptance,
                "diff_summary": get_diff_summary(
                    config.repo_path, config.max_diff_lines
                ),
                "metrics": build_metrics(config, acceptance, tool_call_total),
            }

        messages.append(
            {
                "role": "user",
                "content": build_feedback(acceptance, "acceptance check failed"),
            }
        )

    acceptance = evaluate_acceptance(
        test_command=config.test_command,
        repo_path=config.repo_path,
        allowed_files=config.allowed_files,
        file_budget=config.file_budget,
    )
    return {
        "accepted": acceptance["accepted"],
        "stop_reason": stop_reason,
        "final_response": final_text,
        "acceptance": acceptance,
        "diff_summary": get_diff_summary(config.repo_path, config.max_diff_lines),
        "metrics": build_metrics(config, acceptance, tool_call_total),
    }


def main():
    parser = argparse.ArgumentParser(description="Run the single-agent baseline.")
    parser.add_argument("--task", required=True, help="Task directory")
    parser.add_argument(
        "--condition",
        default="single",
        help="Label recorded in traces/results, usually 'single' for the baseline",
    )
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    loaded = load_task_bundle(args.task, args.model, args.condition)
    result = run_single_agent(
        issue_text=loaded["issue_text"],
        repo_summary=loaded["repo_summary"],
        config=loaded["config"],
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
