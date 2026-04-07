#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field

from nanoagent import MODEL, call_api, run_tool


READ_ONLY_TOOLS = {"read", "glob", "grep"}
INSPECTION_TOOLS = {"read", "glob", "grep"}


def read_text(path):
    with open(path) as f:
        return f.read()


def extract_text(content_blocks):
    return "\n".join(
        block["text"]
        for block in content_blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def parse_json_object(text, fallback):
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return fallback
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return fallback
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return fallback
    return value if isinstance(value, dict) else fallback


def compact_json(value):
    return json.dumps(value, indent=2, sort_keys=True)


def trim_text(text, limit=1600):
    if len(text) <= limit:
        return text
    return text[: limit - 17] + "\n... (truncated)"


def summarize_tool_result(tool_name, args, result):
    if result.startswith("error:"):
        return result
    if tool_name == "read":
        path = args.get("path", "")
        return f"Read {path}\n{trim_text(result, 2200)}"
    if tool_name == "glob":
        path = args.get("path", ".")
        pat = args.get("pat", "")
        return f"Glob path={path} pat={pat}\n{trim_text(result, 1400)}"
    if tool_name == "grep":
        path = args.get("path", ".")
        pat = args.get("pat", "")
        return f"Grep path={path} pat={pat}\n{trim_text(result, 1600)}"
    if tool_name == "bash":
        cmd = args.get("cmd", "")
        return f"Bash cmd={cmd}\n{trim_text(result, 1800)}"
    if tool_name in {"write", "edit"}:
        path = args.get("path", "")
        return f"{tool_name.capitalize()} {path}\n{trim_text(result, 1200)}"
    return trim_text(result, 1200)


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


def resolve_repo_path(repo_path, path_value):
    if not isinstance(path_value, str) or not path_value.strip():
        return path_value
    if os.path.isabs(path_value):
        return path_value
    if os.path.exists(path_value):
        return os.path.normpath(path_value)
    joined = os.path.normpath(os.path.join(repo_path, path_value))
    if os.path.exists(joined):
        return joined
    basename_path = os.path.join(repo_path, os.path.basename(path_value))
    if os.path.exists(basename_path):
        return os.path.normpath(basename_path)
    return joined


def normalize_tool_args(tool_name, args, repo_path):
    if not isinstance(args, dict):
        return {}

    normalized = dict(args)
    if tool_name in {"read", "write", "edit"}:
        if "path" not in normalized:
            normalized["path"] = normalized.get("file_path", normalized.get("file"))
        normalized["path"] = resolve_repo_path(repo_path, normalized.get("path"))
    if tool_name == "glob":
        if "pat" not in normalized:
            normalized["pat"] = normalized.get("pattern")
        normalized["path"] = resolve_repo_path(
            repo_path, normalized.get("path", repo_path)
        )
    if tool_name == "grep":
        if "pat" not in normalized:
            normalized["pat"] = normalized.get("pattern", normalized.get("regex"))
        normalized["path"] = resolve_repo_path(
            repo_path, normalized.get("path", repo_path)
        )
    if tool_name == "write" and "content" not in normalized:
        normalized["content"] = normalized.get("text")
    if tool_name == "edit":
        if "old" not in normalized:
            normalized["old"] = normalized.get("old_string")
        if "new" not in normalized:
            normalized["new"] = normalized.get("new_string")
    if tool_name == "bash":
        cmd = normalized.get("cmd", "")
        if isinstance(cmd, str) and cmd.strip():
            normalized["cmd"] = f"cd {shlex.quote(repo_path)} && {cmd}"
    return normalized


def normalize_plan(plan):
    return {
        "goal": str(plan.get("goal", "")).strip(),
        "suspected_relevant_files": list(plan.get("suspected_relevant_files", [])),
        "inspection_goals": list(plan.get("inspection_goals", [])),
        "implementation_outline": list(plan.get("implementation_outline", [])),
        "acceptance_checks": list(plan.get("acceptance_checks", [])),
        "notes": str(plan.get("notes", "")).strip(),
    }


def normalize_inspection(summary, fallback_files):
    return {
        "relevant_files": list(summary.get("relevant_files", fallback_files)),
        "key_facts": list(summary.get("key_facts", [])),
        "open_questions": list(summary.get("open_questions", [])),
    }


def default_review():
    return {
        "approved": False,
        "critique": "",
        "follow_up_focus": [],
        "risks": [],
    }


@dataclass
class OrchestratorConfig:
    model: str = MODEL
    max_tokens: int = 4096
    max_planner_passes: int = 2
    max_inspector_steps: int = 4
    max_implementer_passes: int = 2
    max_implementer_steps: int = 8
    max_reviewer_passes: int = 2
    max_identical_tool_calls: int = 2
    file_budget: int = 3
    context_observation_limit: int = 8
    max_diff_lines: int = 120
    condition: str = "orchestrated"
    task_id: str = "task"
    test_command: str = ""
    repo_path: str = "."
    allowed_files: list[str] = field(default_factory=list)


def resolve_task_paths(task_dir):
    return {
        "issue": os.path.join(task_dir, "ISSUE.md"),
        "repo_summary": os.path.join(task_dir, "REPO_SUMMARY.md"),
        "metadata": os.path.join(task_dir, "task.json"),
        "run_tests": os.path.join(task_dir, "run_tests.sh"),
    }


def load_task_config(task_dir, model, condition):
    paths = resolve_task_paths(task_dir)
    metadata = {}
    if os.path.exists(paths["metadata"]):
        metadata = parse_json_object(read_text(paths["metadata"]), {})

    repo_path = metadata.get("repo_path") or task_dir
    if not os.path.isabs(repo_path):
        repo_path = os.path.normpath(os.path.join(task_dir, repo_path))

    test_command = metadata.get("test_command", "")
    if not test_command and os.path.exists(paths["run_tests"]):
        test_command = f"sh {os.path.abspath(paths['run_tests'])}"

    return {
        "issue_path": paths["issue"],
        "repo_summary_path": paths["repo_summary"],
        "config": OrchestratorConfig(
            model=model,
            max_planner_passes=metadata.get("max_planner_passes", 2),
            max_inspector_steps=metadata.get("max_inspector_steps", 4),
            max_implementer_passes=metadata.get("max_implementer_passes", 2),
            max_implementer_steps=metadata.get("max_implementer_steps", 8),
            max_reviewer_passes=metadata.get("max_reviewer_passes", 2),
            max_identical_tool_calls=metadata.get("max_identical_tool_calls", 2),
            file_budget=metadata.get("file_budget", 3),
            context_observation_limit=metadata.get("context_observation_limit", 8),
            max_diff_lines=metadata.get("max_diff_lines", 120),
            condition=condition,
            task_id=metadata.get(
                "task_id", os.path.basename(os.path.abspath(task_dir))
            ),
            test_command=test_command,
            repo_path=repo_path,
            allowed_files=metadata.get("allowed_files", []),
        ),
    }


def planner_prompt(issue_text, repo_summary, file_budget):
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
        f"Stay within a patch budget of at most {file_budget} files.\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary}\n"
    )


def inspector_prompt(issue_text, repo_summary, plan, observations, config, step_num):
    return (
        "You are the inspector in a deterministic software engineering workflow.\n"
        "Return JSON only.\n"
        "Choose exactly one action:\n"
        '{ "action": "tool", "reason": "short reason", "tool": "glob|grep|read", "args": {...} }\n'
        "or\n"
        '{ "action": "done", "reason": "short reason", "summary": {\n'
        '    "relevant_files": ["path1"],\n'
        '    "key_facts": ["fact 1"],\n'
        '    "open_questions": ["question 1"]\n'
        "} }\n"
        "Rules:\n"
        "- Use only glob, grep, or read.\n"
        "- Prefer targeted reads with offset and limit.\n"
        "- Avoid repeating the same inspection.\n"
        "- Stop once you have enough context for implementation.\n\n"
        "Allowed tool schemas:\n"
        '- read: {"path": "relative/or/absolute/path", "offset": 0, "limit": 80}\n'
        '- glob: {"path": "directory", "pat": "*"}\n'
        '- grep: {"path": "directory", "pat": "regex"}\n'
        "Do not invent argument names like file_path or pattern.\n\n"
        "Prefer simple repo-relative paths like calc.py or test_calc.py.\n"
        "Do not prepend the repository root manually unless an absolute path is necessary.\n\n"
        f"Repository root: {config.repo_path}\n"
        f"Inspection step: {step_num}/{config.max_inspector_steps}\n"
        f"Plan:\n{compact_json(plan)}\n\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary}\n\n"
        f"Recent observations:\n{format_observations(observations, config.context_observation_limit)}\n"
    )


def implementer_prompt(
    issue_text,
    repo_summary,
    plan,
    inspection,
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
        "Return JSON only.\n"
        "Choose exactly one action:\n"
        '{ "action": "tool", "reason": "short reason", "tool": "read|write|edit|glob|grep|bash", "args": {...} }\n'
        "or\n"
        '{ "action": "done", "reason": "short reason", "summary": "short summary", '
        '"completed_checks": ["check 1"], "remaining_risks": ["risk 1"] }\n'
        "Rules:\n"
        "- Prefer read, glob, and grep over bash for inspection.\n"
        "- Prefer edit over write for existing files.\n"
        "- Use bash mainly for tests/build/verification.\n"
        "- Do not change more than the allowed file budget.\n"
        "- If the requested work is complete, choose action=done.\n\n"
        "Allowed tool schemas:\n"
        '- read: {"path": "relative/or/absolute/path", "offset": 0, "limit": 120}\n'
        '- glob: {"path": "directory", "pat": "*"}\n'
        '- grep: {"path": "directory", "pat": "regex"}\n'
        '- edit: {"path": "file", "old": "exact existing text", "new": "replacement text"}\n'
        '- write: {"path": "file", "content": "full file contents"}\n'
        '- bash: {"cmd": "python3 -m unittest -q"}\n'
        "Use the exact argument names shown above.\n"
        "For edit, provide both old and new with exact text.\n"
        "If edit.old is not unique, read the file and use a larger unique old snippet including surrounding lines.\n"
        "Prefer simple repo-relative paths like calc.py or test_calc.py.\n"
        "Do not prepend the repository root manually unless an absolute path is necessary.\n"
        "Do not invent fields like file, files, file_path, pattern, regex, content_changes, or replacement.\n\n"
        f"Repository root: {config.repo_path}\n"
        f"Implementer step: {step_num}/{config.max_implementer_steps}\n"
        f"File budget: {config.file_budget}\n"
        f"Allowed files: {allowed_files_text}\n"
        f"Current modified files: {modified_files or ['(none)']}\n"
        f"Critique to address: {critique or 'None.'}\n\n"
        f"Plan:\n{compact_json(plan)}\n\n"
        f"Inspection summary:\n{compact_json(inspection)}\n\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary}\n\n"
        f"Recent observations:\n{format_observations(observations, config.context_observation_limit)}\n"
    )


def reviewer_prompt(plan, inspection, acceptance, implementer_result, diff_summary):
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
        f"Inspection summary:\n{compact_json(inspection)}\n\n"
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


def execute_tool_action(
    tool_name,
    args,
    repo_path,
    allowed_tools,
    signature_counts,
    max_identical_calls,
):
    normalized_args = normalize_tool_args(tool_name, args, repo_path)
    if tool_name not in allowed_tools:
        result = f"error: tool {tool_name} is not allowed in this phase"
    elif not isinstance(args, dict):
        result = "error: tool args must be an object"
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
        "args": normalized_args if isinstance(args, dict) else {},
        "reason": "",
        "summary": summarize_tool_result(
            tool_name,
            normalized_args if isinstance(args, dict) else {},
            result,
        ),
        "raw_result": result,
    }


def run_inspection(issue_text, repo_summary, plan, config):
    observations = []
    signature_counts = {}
    last_raw = ""

    for step in range(1, config.max_inspector_steps + 1):
        decision, last_raw = run_json_role(
            "inspector",
            f"Concise coding assistant. cwd: {os.getcwd()}",
            inspector_prompt(
                issue_text, repo_summary, plan, observations, config, step
            ),
            config,
            {
                "action": "done",
                "reason": "Inspector output was not valid JSON.",
                "summary": {
                    "relevant_files": plan.get("suspected_relevant_files", []),
                    "key_facts": ["Inspector output was not valid JSON."],
                    "open_questions": [],
                },
            },
        )

        if decision.get("action") == "done":
            return {
                "summary": normalize_inspection(
                    decision.get("summary", {}),
                    plan.get("suspected_relevant_files", []),
                ),
                "observations": observations,
                "raw_last": last_raw,
            }

        tool_name = str(decision.get("tool", "")).strip()
        tool_args = decision.get("args", {})
        observation = execute_tool_action(
            tool_name,
            tool_args,
            config.repo_path,
            INSPECTION_TOOLS,
            signature_counts,
            config.max_identical_tool_calls,
        )
        observation["reason"] = str(decision.get("reason", "")).strip()
        observations.append(observation)

    return {
        "summary": normalize_inspection(
            {
                "relevant_files": plan.get("suspected_relevant_files", []),
                "key_facts": [
                    observation["summary"] for observation in observations[-3:]
                ],
                "open_questions": [],
            },
            plan.get("suspected_relevant_files", []),
        ),
        "observations": observations,
        "raw_last": last_raw,
    }


def get_modified_files(repo_path):
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    files = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        files.append(line[3:])
    return files


def get_diff_summary(repo_path, max_lines):
    status_proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    diff_proc = subprocess.run(
        ["git", "diff", "--no-color", "--unified=0"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )

    parts = []
    status_text = status_proc.stdout.strip()
    if status_text:
        parts.append("git status --short\n" + status_text)
    diff_text = diff_proc.stdout.strip()
    if diff_text:
        diff_lines = diff_text.splitlines()
        parts.append(
            "git diff --no-color --unified=0\n"
            + "\n".join(diff_lines[:max_lines])
            + ("\n... (truncated)" if len(diff_lines) > max_lines else "")
        )
    return "\n\n".join(parts) or "(no diff)"


def evaluate_acceptance(test_command, repo_path, allowed_files=None, file_budget=None):
    allowed_files = allowed_files or []
    modified_files = get_modified_files(repo_path)
    within_budget = file_budget is None or len(modified_files) <= file_budget
    allowed_ok = not allowed_files or all(
        path in allowed_files for path in modified_files
    )

    tests_run = bool(test_command)
    tests_passed = None
    test_output = ""
    if tests_run:
        proc = subprocess.run(
            test_command,
            cwd=repo_path,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        tests_passed = proc.returncode == 0
        test_output = (proc.stdout + proc.stderr).strip()

    constraints_satisfied = within_budget and allowed_ok
    accepted = constraints_satisfied and (tests_passed if tests_run else True)
    reasons = []
    if tests_run and not tests_passed:
        reasons.append("tests failed")
    if not within_budget:
        reasons.append("file budget exceeded")
    if not allowed_ok:
        reasons.append("modified files outside allowed set")
    if not reasons:
        reasons.append("accepted" if accepted else "needs review")

    return {
        "accepted": accepted,
        "reason": "; ".join(reasons),
        "tests_run": tests_run,
        "tests_passed": tests_passed,
        "constraints_satisfied": constraints_satisfied,
        "modified_files": modified_files,
        "test_output": test_output,
    }


def run_implementer_pass(issue_text, repo_summary, plan, inspection, critique, config):
    observations = []
    signature_counts = {}
    last_raw = ""

    for step in range(1, config.max_implementer_steps + 1):
        decision, last_raw = run_json_role(
            "implementer",
            f"Concise coding assistant. cwd: {os.getcwd()}",
            implementer_prompt(
                issue_text,
                repo_summary,
                plan,
                inspection,
                critique,
                observations,
                get_modified_files(config.repo_path),
                config,
                step,
            ),
            config,
            {
                "action": "done",
                "reason": "Implementer output was not valid JSON.",
                "summary": "Implementer output was not valid JSON.",
                "completed_checks": [],
                "remaining_risks": ["Invalid implementer output"],
            },
        )

        if decision.get("action") == "done":
            return {
                "summary": str(decision.get("summary", "")).strip(),
                "completed_checks": list(decision.get("completed_checks", [])),
                "remaining_risks": list(decision.get("remaining_risks", [])),
                "observations": observations,
                "raw_last": last_raw,
                "modified_files": get_modified_files(config.repo_path),
            }

        tool_name = str(decision.get("tool", "")).strip()
        tool_args = decision.get("args", {})
        observation = execute_tool_action(
            tool_name,
            tool_args,
            config.repo_path,
            set(READ_ONLY_TOOLS) | {"write", "edit", "bash"},
            signature_counts,
            config.max_identical_tool_calls,
        )
        observation["reason"] = str(decision.get("reason", "")).strip()
        observations.append(observation)

    return {
        "summary": "Implementer exhausted the step budget.",
        "completed_checks": [],
        "remaining_risks": ["Implementer step budget exhausted"],
        "observations": observations,
        "raw_last": last_raw,
        "modified_files": get_modified_files(config.repo_path),
    }


def run_orchestrated(issue_text, repo_summary, config):
    plan = normalize_plan(
        {
            "goal": "",
            "suspected_relevant_files": [],
            "inspection_goals": [],
            "implementation_outline": [],
            "acceptance_checks": [],
            "notes": "",
        }
    )
    planner_raw = ""

    for _ in range(config.max_planner_passes):
        planner_output, planner_raw = run_json_role(
            "planner",
            f"Concise coding assistant. cwd: {os.getcwd()}",
            planner_prompt(issue_text, repo_summary, config.file_budget),
            config,
            plan,
        )
        parsed_plan = normalize_plan(planner_output)
        if parsed_plan:
            plan.update(parsed_plan)
            break

    inspection = run_inspection(issue_text, repo_summary, plan, config)

    critique = ""
    implementer_runs = []
    reviewer_runs = []

    for _ in range(config.max_implementer_passes):
        implementer_result = run_implementer_pass(
            issue_text,
            repo_summary,
            plan,
            inspection["summary"],
            critique,
            config,
        )
        implementer_runs.append(implementer_result)

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
                "inspection": inspection,
                "acceptance": acceptance,
                "diff_summary": diff_summary,
                "implementer_runs": implementer_runs,
                "reviewer_runs": reviewer_runs,
            }

        if len(reviewer_runs) >= config.max_reviewer_passes:
            break

        review, reviewer_raw = run_json_role(
            "reviewer",
            f"Concise coding assistant. cwd: {os.getcwd()}",
            reviewer_prompt(
                plan,
                inspection["summary"],
                acceptance,
                implementer_result,
                diff_summary,
            ),
            config,
            default_review(),
        )
        review = {**default_review(), **review, "raw": reviewer_raw}
        reviewer_runs.append(review)

        if review.get("approved"):
            acceptance["accepted"] = True
            return {
                "accepted": True,
                "plan": plan,
                "planner_raw": planner_raw,
                "inspection": inspection,
                "acceptance": acceptance,
                "diff_summary": diff_summary,
                "implementer_runs": implementer_runs,
                "reviewer_runs": reviewer_runs,
            }

        critique = str(review.get("critique", "")).strip()
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
        "inspection": inspection,
        "acceptance": acceptance,
        "diff_summary": get_diff_summary(config.repo_path, config.max_diff_lines),
        "implementer_runs": implementer_runs,
        "reviewer_runs": reviewer_runs,
    }


def main():
    parser = argparse.ArgumentParser(description="Run a bounded orchestrator.")
    parser.add_argument("--task", required=True, help="Task directory")
    parser.add_argument("--condition", default="orchestrated")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    loaded = load_task_config(args.task, args.model, args.condition)
    config = loaded["config"]

    result = run_orchestrated(
        issue_text=read_text(loaded["issue_path"]),
        repo_summary=read_text(loaded["repo_summary_path"])
        if os.path.exists(loaded["repo_summary_path"])
        else "",
        config=config,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
