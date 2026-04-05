#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
from dataclasses import dataclass, field

from nanoagent import MODEL, call_api, make_studio_tools, run_tool


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
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return fallback
    return value if isinstance(value, dict) else fallback


@dataclass
class OrchestratorConfig:
    model: str = MODEL
    max_tokens: int = 4096
    max_planner_passes: int = 2
    max_implementer_passes: int = 2
    max_reviewer_passes: int = 2
    max_tool_invocations_per_pass: int = 6
    file_budget: int = 3
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
            max_implementer_passes=metadata.get("max_implementer_passes", 2),
            max_reviewer_passes=metadata.get("max_reviewer_passes", 2),
            max_tool_invocations_per_pass=metadata.get(
                "max_tool_invocations_per_pass", 6
            ),
            file_budget=metadata.get("file_budget", 3),
            condition=condition,
            task_id=metadata.get("task_id", os.path.basename(os.path.abspath(task_dir))),
            test_command=test_command,
            repo_path=repo_path,
            allowed_files=metadata.get("allowed_files", []),
        ),
    }


def planner_prompt(issue_text, repo_summary, file_budget):
    return (
        "You are the planner in a bounded software engineering workflow.\n"
        "Read the issue and repo summary and produce a compact JSON object only.\n"
        "Do not call tools.\n"
        "JSON schema:\n"
        "{\n"
        '  "suspected_relevant_files": ["path1", "path2"],\n'
        '  "intended_action": "one short paragraph",\n'
        '  "acceptance_checks": ["check 1", "check 2"],\n'
        '  "notes": "short notes"\n'
        "}\n"
        f"Stay within a patch budget of at most {file_budget} files.\n"
        f"Issue:\n{issue_text}\n\n"
        f"Repo summary:\n{repo_summary}\n"
    )


def implementer_prompt(plan, critique, file_budget):
    critique_text = critique or "None."
    plan_json = json.dumps(plan, indent=2)
    return (
        "You are the implementer in a bounded software engineering workflow.\n"
        "Use tools if needed. Prefer targeted reads and minimal edits.\n"
        f"Do not change more than {file_budget} files.\n"
        "When you are done, respond with a short plain-text implementation summary.\n"
        f"Plan:\n{plan_json}\n\n"
        f"Reviewer critique to address:\n{critique_text}\n"
    )


def reviewer_prompt(plan, acceptance, implementer_summary):
    return (
        "You are the reviewer in a bounded software engineering workflow.\n"
        "Return JSON only with this schema:\n"
        '{ "approved": true, "critique": "short string" }\n'
        "Approve only if the attempt appears to satisfy the plan and acceptance result.\n"
        "If not approved, provide one short concrete critique.\n"
        f"Plan:\n{json.dumps(plan, indent=2)}\n\n"
        f"Implementer summary:\n{implementer_summary}\n\n"
        f"Acceptance result:\n{json.dumps(acceptance, indent=2)}\n"
    )


def run_text_role(role, prompt, config):
    response = call_api(
        model=config.model,
        max_tokens=config.max_tokens,
        system=f"Concise coding assistant. cwd: {os.getcwd()}",
        messages=[{"role": "user", "content": prompt}],
        tools=[],
        task_id=config.task_id,
        condition=config.condition,
        role=role,
    )
    return response["assistant_text"] or extract_text(response.get("content", []))


def run_implementer_pass(plan, critique, config):
    system_prompt = f"Concise coding assistant. cwd: {os.getcwd()}"
    user_prompt = implementer_prompt(plan, critique, config.file_budget)
    messages = [{"role": "user", "content": user_prompt}]
    tool_invocations = 0

    while True:
        response = call_api(
            model=config.model,
            max_tokens=config.max_tokens,
            system=system_prompt,
            messages=messages,
            tools=make_studio_tools(),
            task_id=config.task_id,
            condition=config.condition,
            role="implementer",
        )
        content_blocks = response.get("content", [])
        tool_results = []

        for block in content_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_invocations += 1
            if tool_invocations > config.max_tool_invocations_per_pass:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": "error: tool budget exceeded for this pass",
                    }
                )
                continue

            result = run_tool(block["name"], block["input"])
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                }
            )

        messages.append({"role": "assistant", "content": content_blocks})
        if not tool_results:
            return {
                "summary": extract_text(content_blocks),
                "tool_invocations": tool_invocations,
            }
        messages.append({"role": "user", "content": tool_results})


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


def evaluate_acceptance(test_command, repo_path, allowed_files=None, file_budget=None):
    allowed_files = allowed_files or []
    modified_files = get_modified_files(repo_path)
    within_budget = file_budget is None or len(modified_files) <= file_budget
    allowed_ok = not allowed_files or all(
        path in allowed_files for path in modified_files
    )

    tests_passed = False
    test_output = ""
    if test_command:
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
    accepted = tests_passed and constraints_satisfied
    reasons = []
    if test_command and not tests_passed:
        reasons.append("tests failed")
    if not within_budget:
        reasons.append("file budget exceeded")
    if not allowed_ok:
        reasons.append("modified files outside allowed set")
    if not reasons:
        reasons.append("accepted")

    return {
        "accepted": accepted,
        "reason": "; ".join(reasons),
        "tests_passed": tests_passed,
        "constraints_satisfied": constraints_satisfied,
        "modified_files": modified_files,
        "test_output": test_output,
    }


def run_orchestrated(issue_text, repo_summary, config):
    plan = {
        "suspected_relevant_files": [],
        "intended_action": "",
        "acceptance_checks": [],
        "notes": "",
    }

    for _ in range(config.max_planner_passes):
        planner_output = run_text_role(
            "planner",
            planner_prompt(issue_text, repo_summary, config.file_budget),
            config,
        )
        parsed_plan = parse_json_object(planner_output, {})
        if parsed_plan:
            plan.update(parsed_plan)
            break

    critique = ""
    implementer_runs = []
    reviewer_runs = []

    for _ in range(config.max_implementer_passes):
        implementer_result = run_implementer_pass(plan, critique, config)
        implementer_runs.append(implementer_result)

        acceptance = evaluate_acceptance(
            test_command=config.test_command,
            repo_path=config.repo_path,
            allowed_files=config.allowed_files,
            file_budget=config.file_budget,
        )
        if acceptance["accepted"]:
            return {
                "accepted": True,
                "plan": plan,
                "acceptance": acceptance,
                "implementer_runs": implementer_runs,
                "reviewer_runs": reviewer_runs,
            }

        if len(reviewer_runs) >= config.max_reviewer_passes:
            break

        reviewer_output = run_text_role(
            "reviewer",
            reviewer_prompt(plan, acceptance, implementer_result["summary"]),
            config,
        )
        review = parse_json_object(
            reviewer_output,
            {"approved": False, "critique": "Reviewer output was not valid JSON."},
        )
        reviewer_runs.append(review)

        if review.get("approved"):
            acceptance["accepted"] = True
            return {
                "accepted": True,
                "plan": plan,
                "acceptance": acceptance,
                "implementer_runs": implementer_runs,
                "reviewer_runs": reviewer_runs,
            }

        critique = review.get("critique", "").strip()
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
        "acceptance": acceptance,
        "implementer_runs": implementer_runs,
        "reviewer_runs": reviewer_runs,
    }


def main():
    parser = argparse.ArgumentParser(description="Run a tiny bounded orchestrator.")
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
