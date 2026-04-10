#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from dataclasses import dataclass, field

from nanoagent import MODEL


def read_text(path: str) -> str:
    with open(path) as handle:
        return handle.read()


def truncate_text(text: str, limit: int = 1600) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 17] + "\n... (truncated)"


def parse_json_object(text: object, fallback: dict[str, object]) -> dict[str, object]:
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


@dataclass
class TaskConfig:
    model: str = MODEL
    max_tokens: int = 4096
    max_planner_passes: int = 3
    max_implementer_passes: int = 3
    max_implementer_steps: int = 15
    max_reviewer_passes: int = 2
    max_identical_tool_calls: int = 2
    file_budget: int = 12
    context_observation_limit: int = 8
    max_diff_lines: int = 120
    condition: str = "single"
    task_id: str = "task"
    test_command: str = ""
    reproduction_command: str = ""
    repo_path: str = "."
    allowed_files: list[str] = field(default_factory=list)
    phase: str = "in_class"
    setup_command: str = ""
    acceptance_description: str = ""
    max_total_tokens: int = 75000
    max_single_agent_turns: int = 8
    max_tool_iterations: int = 16


def normalize_task_dir(task_dir: str) -> tuple[str, str]:
    """Accept either the task bundle root or its repo/ directory."""
    resolved = os.path.abspath(task_dir)
    if os.path.basename(resolved) == "repo":
        bundle_dir = os.path.dirname(resolved)
        repo_dir = resolved
    else:
        bundle_dir = resolved
        repo_dir = os.path.join(resolved, "repo")
    return bundle_dir, repo_dir


def resolve_task_paths(task_dir: str) -> dict[str, str]:
    bundle_dir, repo_dir = normalize_task_dir(task_dir)
    return {
        "bundle_dir": bundle_dir,
        "repo_dir": repo_dir,
        "issue": os.path.join(repo_dir, "ISSUE.md"),
        "repo_summary": os.path.join(bundle_dir, "REPO_SUMMARY.md"),
        "metadata": os.path.join(bundle_dir, "task.json"),
        "run_tests": os.path.join(repo_dir, "run_tests.sh"),
    }


def ensure_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def repo_root(repo_path: str) -> Path:
    return Path(repo_path).resolve()


def is_within_repo(repo_path: str, candidate: Path) -> bool:
    root = repo_root(repo_path)
    resolved = candidate.resolve(strict=False)
    return resolved == root or root in resolved.parents


def resolve_repo_path(
    repo_path: str, path_value: object, *, default: str | None = None
) -> str:
    if path_value is None:
        if default is None:
            raise ValueError("missing path")
        path_value = default
    if not isinstance(path_value, str) or not path_value.strip():
        raise ValueError("path must be a non-empty string")
    root = repo_root(repo_path)
    raw_path = Path(path_value)
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = candidate.resolve(strict=False)
    if not is_within_repo(repo_path, resolved):
        raise ValueError(f"path escapes repository root: {path_value}")
    return str(resolved)


_UNSAFE_BASH_TOKENS = ("&&", "||", ";", "|", ">", "<", "$(", "`")
_SAFE_BASH_PATTERNS = (
    re.compile(r"^python(?:3)? -m pytest\b"),
    re.compile(r"^pytest\b"),
    re.compile(r"^git status(?:\b|$)"),
    re.compile(r"^git diff(?:\b|$)"),
    re.compile(r"^ls(?:\b|$)"),
    re.compile(r"^pwd$"),
    re.compile(r"^find(?:\b|$)"),
    re.compile(r"^rg(?:\b|$)"),
    re.compile(r"^(?:sh|bash) \.\./run_tests\.sh$"),
)


def normalize_task_tool_args(
    tool_name: str,
    args: object,
    repo_path: str,
) -> tuple[dict[str, object], str | None]:
    if not isinstance(args, dict):
        return {}, "error: tool args must be an object"

    normalized = dict(args)

    try:
        if tool_name in {"read", "write", "edit"}:
            normalized["path"] = resolve_repo_path(repo_path, normalized.get("path"))
        elif tool_name in {"glob", "grep"}:
            if normalized.get("path") in (None, ""):
                normalized["path"] = "."
            normalized["path"] = resolve_repo_path(
                repo_path,
                normalized.get("path"),
                default=".",
            )
        elif tool_name == "bash":
            cmd = normalized.get("cmd")
            if not isinstance(cmd, str) or not cmd.strip():
                return {}, "error: bash cmd must be a non-empty string"
            stripped = cmd.strip()
            if any(token in stripped for token in _UNSAFE_BASH_TOKENS):
                return {}, "error: bash command rejected by task sandbox"
            if not any(pattern.match(stripped) for pattern in _SAFE_BASH_PATTERNS):
                return {}, "error: bash is limited to read-only verification commands"
            normalized["cmd"] = f"cd {repo_root(repo_path)} && {stripped}"
    except ValueError as err:
        return {}, f"error: {err}"

    return normalized, None


def load_task_bundle(
    task_dir: str, model: str | None, condition: str
) -> dict[str, object]:
    paths = resolve_task_paths(task_dir)
    metadata: dict[str, object] = {}
    default_config = TaskConfig()
    if os.path.exists(paths["metadata"]):
        metadata = parse_json_object(read_text(paths["metadata"]), {})

    repo_path = metadata.get("repo_path") or paths["repo_dir"]
    if not os.path.isabs(repo_path):
        repo_path = os.path.normpath(os.path.join(paths["bundle_dir"], str(repo_path)))

    test_command = str(metadata.get("test_command", "")).strip()
    if not test_command and os.path.exists(paths["run_tests"]):
        test_command = f"sh {os.path.abspath(paths['run_tests'])}"

    config = TaskConfig(
        model=model or MODEL,
        max_tokens=int(metadata.get("max_tokens", default_config.max_tokens)),
        max_planner_passes=int(
            metadata.get("max_planner_passes", default_config.max_planner_passes)
        ),
        max_implementer_passes=int(
            metadata.get(
                "max_implementer_passes", default_config.max_implementer_passes
            )
        ),
        max_implementer_steps=int(
            metadata.get("max_implementer_steps", default_config.max_implementer_steps)
        ),
        max_reviewer_passes=int(
            metadata.get("max_reviewer_passes", default_config.max_reviewer_passes)
        ),
        max_identical_tool_calls=int(
            metadata.get(
                "max_identical_tool_calls", default_config.max_identical_tool_calls
            )
        ),
        file_budget=int(metadata.get("file_budget", default_config.file_budget)),
        context_observation_limit=int(
            metadata.get(
                "context_observation_limit", default_config.context_observation_limit
            )
        ),
        max_diff_lines=int(
            metadata.get("max_diff_lines", default_config.max_diff_lines)
        ),
        condition=condition,
        task_id=str(metadata.get("task_id", os.path.basename(paths["bundle_dir"]))),
        test_command=test_command,
        reproduction_command=str(metadata.get("reproduction_command", "")).strip(),
        repo_path=repo_path,
        allowed_files=ensure_str_list(metadata.get("allowed_files", [])),
        phase=str(metadata.get("phase", default_config.phase)).strip()
        or default_config.phase,
        setup_command=str(
            metadata.get("setup_command", default_config.setup_command)
        ).strip(),
        acceptance_description=str(
            metadata.get(
                "acceptance_description", default_config.acceptance_description
            )
        ).strip(),
        max_total_tokens=int(
            metadata.get("max_total_tokens", default_config.max_total_tokens)
        ),
        max_single_agent_turns=int(
            metadata.get(
                "max_single_agent_turns", default_config.max_single_agent_turns
            )
        ),
        max_tool_iterations=int(
            metadata.get("max_tool_iterations", default_config.max_tool_iterations)
        ),
    )
    issue_text = read_text(paths["issue"])
    repo_summary = (
        read_text(paths["repo_summary"])
        if os.path.exists(paths["repo_summary"])
        else ""
    )
    return {
        "issue_path": paths["issue"],
        "repo_summary_path": paths["repo_summary"],
        "issue_text": issue_text,
        "repo_summary": repo_summary,
        "config": config,
    }


def get_modified_files(repo_path: str) -> list[str]:
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


def get_diff_summary(repo_path: str, max_lines: int) -> str:
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
    parts: list[str] = []
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


def evaluate_acceptance(
    test_command: str,
    repo_path: str,
    allowed_files: list[str] | None = None,
    file_budget: int | None = None,
) -> dict[str, object]:
    allowed_files = allowed_files or []
    modified_files = get_modified_files(repo_path)
    within_budget = file_budget is None or len(modified_files) <= file_budget
    allowed_ok = not allowed_files or all(
        path in allowed_files for path in modified_files
    )

    tests_run = bool(test_command)
    tests_passed: bool | None = None
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
