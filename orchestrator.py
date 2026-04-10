#!/usr/bin/env python3
"""orchestrator - minimal multi-agent orchestrator for nanoagent"""

import argparse
import json
import os
from pathlib import Path
import re

from nanoagent import (
    MODEL,
    call_api,
    extract_text,
    get_trace_state,
    make_studio_tools,
    reset_trace_state,
    run_tool,
)
from task_support import (
    evaluate_acceptance,
    ensure_str_list,
    get_diff_summary,
    get_modified_files,
    load_task_bundle,
    normalize_task_tool_args,
    parse_json_object,
    truncate_text,
)


READ_ONLY_TOOLS = {"read", "glob", "grep"}


def compact_json(value):
    return json.dumps(value, indent=2, sort_keys=True)


def summarize_tool_result(tool_name, args, result):
    if result.startswith("error:"):
        return result
    if tool_name == "read":
        path = args.get("path", "")
        return f"Read {path}\n{truncate_text(result, 2200)}"
    if tool_name == "glob":
        path = args.get("path", ".")
        pat = args.get("pat", "")
        return f"Glob path={path} pat={pat}\n{truncate_text(result, 1400)}"
    if tool_name == "grep":
        path = args.get("path", ".")
        pat = args.get("pat", "")
        return f"Grep path={path} pat={pat}\n{truncate_text(result, 1600)}"
    if tool_name == "bash":
        cmd = args.get("cmd", "")
        return f"Bash cmd={cmd}\n{truncate_text(result, 1800)}"
    if tool_name in {"write", "edit"}:
        path = args.get("path", "")
        return f"{tool_name.capitalize()} {path}\n{truncate_text(result, 1200)}"
    return truncate_text(result, 1200)


def format_observations(observations, limit):
    if not observations:
        return "(none)"
    recent = observations[-limit:]
    blocks = []
    for i, observation in enumerate(recent, 1):
        blocks.append(
            f"{i}. tool={observation['tool']}\n"
            f"reason={observation['reason']}\n"
            f"summary=\n{observation['summary']}"
        )
    return "\n\n".join(blocks)


def tool_signature(name, args):
    return json.dumps({"tool": name, "args": args}, sort_keys=True)


def to_repo_relative(repo_path, path_value):
    if not isinstance(path_value, str) or not path_value.strip():
        return ""
    resolved = Path(path_value).resolve(strict=False)
    try:
        return str(resolved.relative_to(Path(repo_path).resolve()))
    except ValueError:
        return str(resolved)


def within_token_budget(config):
    return get_trace_state()["cumulative_total"] < config.max_total_tokens


def build_metrics(config, acceptance, tool_calls):
    trace = get_trace_state()
    return {
        "task_id": config.task_id,
        "condition": config.condition,
        "model_calls": trace["call_count"],
        "total_tokens": trace["cumulative_total"],
        "tool_calls": tool_calls,
        "files_edited": acceptance.get("modified_files", []),
    }


def normalize_plan(plan):
    def list_field(name):
        value = plan.get(name, [])
        return [str(item) for item in value] if isinstance(value, list) else []

    return {
        "goal": str(plan.get("goal", "")).strip(),
        "suspected_relevant_files": list_field("suspected_relevant_files"),
        "inspection_goals": list_field("inspection_goals"),
        "implementation_outline": list_field("implementation_outline"),
        "acceptance_checks": list_field("acceptance_checks"),
        "notes": str(plan.get("notes", "")).strip(),
    }


def default_review():
    return {
        "approved": False,
        "critique": "",
        "follow_up_focus": [],
        "risks": [],
    }


def _ordered_unique(items):
    seen = set()
    ordered = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def extract_issue_identifiers(issue_text, repo_summary, limit=8):
    text = f"{issue_text}\n{repo_summary}"
    backtick_blocks = re.findall(r"`([^`]+)`", text)
    file_paths = re.findall(r"\b(?:[\w.-]+/)+[\w.-]+\.py\b", text)
    function_names = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    code_like_words = re.findall(
        r"\b(?:[A-Za-z0-9]*_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9]*)\b",
        text,
    )

    candidates = []
    for path in file_paths:
        candidates.append(path)
        path_parts = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", path)
        candidates.extend(path_parts)
    for block in backtick_blocks:
        candidates.extend(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", block))
    candidates.extend(function_names)
    candidates.extend(code_like_words)

    return _ordered_unique(candidates)[:limit]


def summarize_discovery_hits(hit_lines, repo_path, limit=20):
    if not hit_lines:
        return "(none)"
    summarized = []
    for line in hit_lines[:limit]:
        path_text = line.split(":", 1)[0]
        summarized.append(to_repo_relative(repo_path, path_text))
    unique = list(dict.fromkeys(summarized))
    return "\n".join(f"- {entry}" for entry in unique[:limit]) or "(none)"


def run_discovery(issue_text, repo_summary, config):
    identifiers = extract_issue_identifiers(issue_text, repo_summary)
    root_listing = execute_tool_action(
        "glob",
        {"path": ".", "pat": "*"},
        config.repo_path,
        READ_ONLY_TOOLS,
        {},
        config.max_identical_tool_calls,
    )
    python_listing = execute_tool_action(
        "glob",
        {"path": ".", "pat": "**/*.py"},
        config.repo_path,
        READ_ONLY_TOOLS,
        {},
        config.max_identical_tool_calls,
    )
    grep_summaries = []
    for identifier in identifiers[:4]:
        grep_result = execute_tool_action(
            "grep",
            {"path": ".", "pat": identifier},
            config.repo_path,
            READ_ONLY_TOOLS,
            {},
            config.max_identical_tool_calls,
        )
        hit_lines = [
            line
            for line in str(grep_result["raw_result"]).splitlines()
            if line.strip() and line.strip() != "none"
        ]
        grep_summaries.append(
            {
                "identifier": identifier,
                "matches": summarize_discovery_hits(hit_lines, config.repo_path),
            }
        )

    summary_lines = [
        "Repository discovery:",
        "Top-level entries:",
        truncate_text(root_listing["raw_result"], 1200),
        "",
        "Python files:",
        truncate_text(python_listing["raw_result"], 1800),
        "",
        "Identifier matches:",
    ]
    if grep_summaries:
        for item in grep_summaries:
            summary_lines.append(f"{item['identifier']}:")
            summary_lines.append(item["matches"])
    else:
        summary_lines.append("(none)")

    discovered_files = []
    for item in grep_summaries:
        for line in item["matches"].splitlines():
            entry = line.removeprefix("- ").strip()
            if (
                entry
                and entry not in discovered_files
                and entry != "(none)"
                and entry.endswith(".py")
            ):
                discovered_files.append(entry)
    return {
        "identifiers": identifiers,
        "discovered_files": discovered_files[:12],
        "summary": "\n".join(summary_lines).strip(),
    }


def planner_prompt(issue_text, repo_summary, discovery_summary, config):
    return (
        "You are the planner in a deterministic software engineering workflow.\n"
        "Return JSON only.\n"
        "Schema:\n"
        "{\n"
        '  "goal": "one sentence",\n'
        '  "suspected_relevant_files": ["path1", "path2"],\n'
        '  "inspection_goals": ["goal 1", "goal 2"],\n'
        '  "implementation_outline": ["step 1", "step 2"],\n'
        '  "acceptance_checks": ["check 1", "check 2"],\n'
        '  "notes": "short notes"\n'
        "}\n"
        f"Stay within a patch budget of at most {config.file_budget} files.\n"
        f"Acceptance: {config.acceptance_description or 'Provided tests must pass and constraints must hold.'}\n"
        f"Canonical test command: {config.test_command or '(none provided)'}\n"
        f"Reproduction command: {config.reproduction_command or '(none provided)'}\n"
        "Use the repository discovery evidence below rather than guessing file names.\n"
        "Prefer files that were actually discovered in the codebase.\n"
        f"Discovery summary:\n{discovery_summary}\n\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary}\n"
    )


def implementer_prompt(
    issue_text,
    repo_summary,
    plan,
    critique,
    observations,
    modified_files,
    config,
    step_num,
):
    allowed_files_text = (
        ", ".join(config.allowed_files) if config.allowed_files else "(any files)"
    )
    return (
        "You are the implementer in a deterministic software engineering workflow.\n"
        "Use the provided tools directly for inspection and editing.\n"
        "When the requested work is complete, return JSON only in this schema:\n"
        '{ "action": "done", "reason": "short reason", "summary": "short summary", '
        '"completed_checks": ["check 1"], "remaining_risks": ["risk 1"] }\n'
        "Rules:\n"
        "- Prefer read, glob, and grep over bash for inspection.\n"
        "- Prefer edit over write for existing files.\n"
        "- Use bash only for read-only verification commands.\n"
        "- All file tools are confined to the staged repo checkout.\n"
        "- Do not change more than the allowed file budget.\n"
        "- Do not describe tool calls in JSON or prose; call the tool directly.\n"
        "- If the requested work is complete, return the done JSON object.\n\n"
        "Allowed tool schemas:\n"
        '- read: {"path": "relative/or/absolute/path", "offset": 0, "limit": 120}\n'
        '- glob: {"path": ".", "pat": "*"}\n'
        '- grep: {"path": ".", "pat": "regex"}\n'
        '- edit: {"path": "file", "old": "exact existing text", "new": "replacement text"}\n'
        '- write: {"path": "file", "content": "full file contents"}\n'
        '- bash: {"cmd": "python3 -m unittest -q"}\n'
        "Use the exact argument names shown above.\n"
        'For glob or grep at repository root, set "path" to "." instead of an empty string.\n'
        "For edit, provide both old and new with exact text.\n"
        "If edit.old is not unique, read the file and use a larger unique old snippet including surrounding lines.\n"
        "Prefer simple repo-relative paths like calc.py or test_calc.py.\n"
        "Do not prepend the repository root manually unless an absolute path is necessary.\n"
        "Do not invent fields like file, files, file_path, pattern, regex, content_changes, or replacement.\n\n"
        f"Repository root: {config.repo_path}\n"
        f"Implementer step: {step_num}/{config.max_implementer_steps}\n"
        f"File budget: {config.file_budget}\n"
        f"Allowed files: {allowed_files_text}\n"
        f"Acceptance: {config.acceptance_description or 'Provided tests must pass and constraints must hold.'}\n"
        f"Canonical test command: {config.test_command or '(none provided)'}\n"
        f"Reproduction command: {config.reproduction_command or '(none provided)'}\n"
        f"Current modified files: {modified_files or ['(none)']}\n"
        f"Critique to address: {critique or 'None.'}\n\n"
        "Prioritize files named in suspected_relevant_files and reviewer feedback before "
        "exploring unrelated files.\n\n"
        f"Plan:\n{compact_json(plan)}\n\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary}\n\n"
        f"Recent observations:\n{format_observations(observations, config.context_observation_limit)}\n"
    )


def reviewer_prompt(plan, acceptance, implementer_result, diff_summary):
    return (
        "You are the reviewer in a deterministic software engineering workflow.\n"
        "Return JSON only.\n"
        "Schema:\n"
        "{\n"
        '  "approved": true,\n'
        '  "critique": "short critique",\n'
        '  "follow_up_focus": ["focus area"],\n'
        '  "risks": ["risk 1"]\n'
        "}\n"
        "Approve only if the implementation, diff summary, and acceptance evidence look sufficient.\n"
        "If not approved, provide one concrete critique that should be addressed next.\n\n"
        f"Plan:\n{compact_json(plan)}\n\n"
        f"Implementer result:\n{compact_json(implementer_result)}\n\n"
        f"Acceptance:\n{compact_json(acceptance)}\n\n"
        f"Diff summary:\n{diff_summary}\n"
    )


def run_text_role(role, system_prompt, user_prompt, config):
    response = call_api(
        model=config.model,
        max_tokens=config.max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[],
        task_id=config.task_id,
        condition=config.condition,
        role=role,
    )
    return response["assistant_text"] or extract_text(response.get("content", []))


def run_json_role(role, system_prompt, user_prompt, config, fallback):
    raw_text = run_text_role(role, system_prompt, user_prompt, config)
    return parse_json_object(raw_text, fallback), raw_text


def make_implementer_tools():
    tools = []
    for tool in make_studio_tools():
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        if function.get("name") == "returnToUser":
            continue
        tools.append(tool)
    return tools


def normalize_implementer_decision(decision):
    if not isinstance(decision, dict):
        return decision
    action = str(decision.get("action", "")).strip()
    if action in READ_ONLY_TOOLS | {"write", "edit", "bash"}:
        tool_args = (
            dict(decision.get("args", {}))
            if isinstance(decision.get("args"), dict)
            else {
                key: value
                for key, value in decision.items()
                if key not in {"action", "reason", "tool", "args"}
            }
        )
        return {
            "action": "tool",
            "tool": action,
            "args": tool_args,
            "reason": str(decision.get("reason", "")).strip(),
        }
    if action == "tool" and isinstance(decision.get("args"), dict):
        tool_name = str(decision.get("tool", "")).strip()
        tool_args = dict(decision.get("args", {}))
        if not tool_name:
            tool_name = str(
                tool_args.get("tool")
                or tool_args.get("action")
                or tool_args.get("name")
                or ""
            ).strip()
        if isinstance(tool_args.get("args"), dict):
            nested_args = dict(tool_args.get("args", {}))
            if not tool_name:
                tool_name = str(
                    tool_args.get("tool")
                    or tool_args.get("action")
                    or tool_args.get("name")
                    or ""
                ).strip()
            tool_args = nested_args
        return {
            "action": "tool",
            "tool": tool_name,
            "args": tool_args,
            "reason": str(decision.get("reason", "")).strip(),
        }
    if action == "tool":
        tool_name = str(decision.get("tool", "")).strip()
        tool_args = {
            key: value
            for key, value in decision.items()
            if key not in {"action", "reason", "tool", "args"}
        }
        return {
            **decision,
            "tool": tool_name,
            "args": decision.get("args")
            if isinstance(decision.get("args"), dict)
            else tool_args,
        }
    return decision


def execute_tool_action(
    tool_name,
    args,
    repo_path,
    allowed_tools,
    signature_counts,
    max_identical_calls,
    allowed_files=None,
):
    allowed_files = allowed_files or []
    normalized_args, normalize_error = normalize_task_tool_args(
        tool_name,
        args,
        repo_path,
    )
    if tool_name not in allowed_tools:
        result = f"error: tool {tool_name} is not allowed in this phase"
    elif normalize_error is not None:
        result = normalize_error
    elif tool_name in {"write", "edit"} and allowed_files:
        repo_relative_path = to_repo_relative(repo_path, normalized_args.get("path"))
        if repo_relative_path not in allowed_files:
            result = (
                "error: attempted to modify a file outside the allowed set: "
                f"{repo_relative_path}"
            )
        else:
            signature = tool_signature(tool_name, normalized_args)
            count = signature_counts.get(signature, 0)
            if count >= max_identical_calls:
                result = "error: repeated identical tool request blocked"
            else:
                signature_counts[signature] = count + 1
                result = run_tool(tool_name, normalized_args)
    else:
        signature = tool_signature(tool_name, normalized_args)
        count = signature_counts.get(signature, 0)
        if count >= max_identical_calls:
            result = "error: repeated identical tool request blocked"
        else:
            signature_counts[signature] = count + 1
            result = run_tool(tool_name, normalized_args)
    return {
        "tool": tool_name,
        "args": normalized_args if normalize_error is None else {},
        "reason": "",
        "summary": summarize_tool_result(
            tool_name,
            normalized_args if normalize_error is None else {},
            result,
        ),
        "raw_result": result,
    }


def run_implementer_pass(issue_text, repo_summary, plan, critique, config):
    observations = []
    signature_counts = {}
    last_raw = ""
    active_critique = critique
    messages = [
        {
            "role": "user",
            "content": implementer_prompt(
                issue_text,
                repo_summary,
                plan,
                active_critique,
                observations,
                get_modified_files(config.repo_path),
                config,
                1,
            ),
        }
    ]
    tools = make_implementer_tools()

    for step in range(1, config.max_implementer_steps + 1):
        if not within_token_budget(config):
            break
        response = call_api(
            model=config.model,
            max_tokens=config.max_tokens,
            system=(
                "You are a concise coding assistant. Use tools directly when needed. "
                "When no more tool calls are needed, return exactly one JSON object "
                "matching the requested done schema. Do not use markdown fences or "
                f"explanatory prose. cwd: {os.getcwd()}"
            ),
            messages=messages,
            tools=tools,
            task_id=config.task_id,
            condition=config.condition,
            role="implementer",
        )
        last_raw = response["assistant_text"] or extract_text(
            response.get("content", [])
        )
        messages.append({"role": "assistant", "content": response["content"]})

        if response["tool_calls"]:
            tool_results = []
            for i, tool_call in enumerate(response["tool_calls"]):
                observation = execute_tool_action(
                    tool_call["name"],
                    tool_call["arguments"],
                    config.repo_path,
                    set(READ_ONLY_TOOLS) | {"write", "edit", "bash"},
                    signature_counts,
                    config.max_identical_tool_calls,
                    config.allowed_files,
                )
                observation["reason"] = last_raw or f"tool_call_{i + 1}"
                observations.append(observation)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.get("id", f"call_{i}"),
                        "content": observation["raw_result"],
                    }
                )
                if observation["raw_result"].startswith("error:"):
                    active_critique = (
                        "Your previous tool request failed. "
                        f"Error: {observation['raw_result']}. "
                        "Choose a different valid action."
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        decision = normalize_implementer_decision(
            parse_json_object(
                last_raw,
                {
                    "action": "done",
                    "reason": "Implementer output was not valid JSON.",
                    "summary": "Implementer output was not valid JSON.",
                    "completed_checks": [],
                    "remaining_risks": ["Invalid implementer output"],
                },
            )
        )

        if decision.get("reason") == "Implementer output was not valid JSON.":
            observations.append(
                {
                    "tool": "none",
                    "args": {},
                    "reason": "Model failed to return valid JSON",
                    "summary": "error: implementer output was not valid JSON",
                    "raw_result": last_raw,
                }
            )
            active_critique = (
                "Your previous reply was not valid JSON. "
                "Reply with exactly one JSON object using the documented schema."
            )
            messages.append({"role": "user", "content": active_critique})
            continue

        if decision.get("action") == "tool":
            tool_name = str(decision.get("tool", "")).strip()
            tool_args = decision.get("args", {})
            observation = execute_tool_action(
                tool_name,
                tool_args,
                config.repo_path,
                set(READ_ONLY_TOOLS) | {"write", "edit", "bash"},
                signature_counts,
                config.max_identical_tool_calls,
                config.allowed_files,
            )
            observation["reason"] = str(decision.get("reason", "")).strip()
            observations.append(observation)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous reply described a tool action in text instead of "
                        f"calling the tool. The tool result was:\n{observation['raw_result']}"
                    ),
                }
            )
            continue

        if decision.get("action") == "done":
            return {
                "summary": str(decision.get("summary", "")).strip(),
                "completed_checks": ensure_str_list(decision.get("completed_checks")),
                "remaining_risks": ensure_str_list(decision.get("remaining_risks")),
                "observations": observations,
                "raw_last": last_raw,
                "modified_files": get_modified_files(config.repo_path),
            }
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous reply did not request a tool and did not return the "
                    "done JSON object. Use a tool or return the done JSON object."
                ),
            }
        )

    return {
        "summary": "Implementer exhausted the step budget.",
        "completed_checks": [],
        "remaining_risks": ["Implementer step budget exhausted"],
        "observations": observations,
        "raw_last": last_raw,
        "modified_files": get_modified_files(config.repo_path),
    }


def run_orchestrated(issue_text, repo_summary, config):
    reset_trace_state()
    discovery = run_discovery(issue_text, repo_summary, config)
    plan = normalize_plan(
        {
            "goal": "",
            "suspected_relevant_files": discovery["discovered_files"],
            "inspection_goals": [],
            "implementation_outline": [],
            "acceptance_checks": [],
            "notes": "",
        }
    )
    planner_raw = ""

    for _ in range(config.max_planner_passes):
        if not within_token_budget(config):
            break
        planner_output, planner_raw = run_json_role(
            "planner",
            f"Concise coding assistant. cwd: {os.getcwd()}",
            planner_prompt(issue_text, repo_summary, discovery["summary"], config),
            config,
            plan,
        )
        parsed_plan = normalize_plan(planner_output)
        if parsed_plan:
            plan.update(parsed_plan)
            merged_files = list(
                dict.fromkeys(
                    discovery["discovered_files"] + plan["suspected_relevant_files"]
                )
            )
            plan["suspected_relevant_files"] = merged_files
            break

    critique = ""
    implementer_runs = []
    reviewer_runs = []
    tool_call_total = 0

    for _ in range(config.max_implementer_passes):
        if not within_token_budget(config):
            break
        implementer_result = run_implementer_pass(
            issue_text,
            repo_summary,
            plan,
            critique,
            config,
        )
        implementer_runs.append(implementer_result)
        tool_call_total += len(implementer_result["observations"])

        acceptance = evaluate_acceptance(
            test_command=config.test_command,
            repo_path=config.repo_path,
            allowed_files=config.allowed_files,
            file_budget=config.file_budget,
        )
        diff_summary = get_diff_summary(config.repo_path, config.max_diff_lines)
        if acceptance["accepted"]:
            return {
                "accepted": True,
                "plan": plan,
                "planner_raw": planner_raw,
                "acceptance": acceptance,
                "diff_summary": diff_summary,
                "implementer_runs": implementer_runs,
                "reviewer_runs": reviewer_runs,
                "metrics": build_metrics(config, acceptance, tool_call_total),
            }

        if len(reviewer_runs) >= config.max_reviewer_passes:
            break
        if not within_token_budget(config):
            break

        review, reviewer_raw = run_json_role(
            "reviewer",
            f"Concise coding assistant. cwd: {os.getcwd()}",
            reviewer_prompt(
                plan,
                acceptance,
                implementer_result,
                diff_summary,
            ),
            config,
            default_review(),
        )
        review = {**default_review(), **review, "raw": reviewer_raw}
        review["follow_up_focus"] = ensure_str_list(review.get("follow_up_focus"))
        review["risks"] = ensure_str_list(review.get("risks"))
        reviewer_runs.append(review)

        critique_parts = [str(review.get("critique", "")).strip()]
        if review["follow_up_focus"]:
            critique_parts.append(
                "Reviewer follow-up focus: " + "; ".join(review["follow_up_focus"])
            )
            follow_up_discovery = run_discovery(
                "\n".join(review["follow_up_focus"]),
                repo_summary,
                config,
            )
            merged_files = list(
                dict.fromkeys(
                    plan["suspected_relevant_files"]
                    + follow_up_discovery["discovered_files"]
                )
            )
            plan["suspected_relevant_files"] = merged_files
        critique = "\n".join(part for part in critique_parts if part)
        if not critique:
            break

    acceptance = evaluate_acceptance(
        test_command=config.test_command,
        repo_path=config.repo_path,
        allowed_files=config.allowed_files,
        file_budget=config.file_budget,
    )
    return {
        "accepted": acceptance["accepted"],
        "plan": plan,
        "planner_raw": planner_raw,
        "acceptance": acceptance,
        "diff_summary": get_diff_summary(config.repo_path, config.max_diff_lines),
        "implementer_runs": implementer_runs,
        "reviewer_runs": reviewer_runs,
        "metrics": build_metrics(config, acceptance, tool_call_total),
    }


def main():
    parser = argparse.ArgumentParser(description="Run a bounded orchestrator.")
    parser.add_argument("--task", required=True, help="Task directory")
    parser.add_argument(
        "--condition",
        default="orchestrated",
        help="Label recorded in traces/results, usually 'orchestrated' for this workflow",
    )
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    loaded = load_task_bundle(args.task, args.model, args.condition)
    config = loaded["config"]

    result = run_orchestrated(
        issue_text=loaded["issue_text"],
        repo_summary=loaded["repo_summary"],
        config=config,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
