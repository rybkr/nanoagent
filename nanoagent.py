#!/usr/bin/env python3
"""nanoagent - minimal claude code alternative"""

# Import
import glob as globlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import traceback
import argparse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.modules.setdefault("nanoagent", sys.modules[__name__])

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:

    def load_dotenv(*_args: object, **_kwargs: object) -> None:
        return None


# Load environment variables from the repo-root .env if present.
ENV_PATH = Path(__file__).resolve().with_name(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()

# Type definitions
type JSONValue = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)
type JSONObject = dict[str, JSONValue]
type ToolSpec = tuple[str, dict[str, str], Callable[[JSONObject], str]]
type Message = dict[str, object]
type Response = dict[str, object]
type Usage = dict[str, int]
type ToolState = dict[str, object]

# Constants
API_URL = os.environ.get("API_URL")
API_KEY = os.environ.get("API_KEY")
MODEL = os.environ.get("MODEL")
PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_log_path(path_value: str, base_dir: str | Path | None = None) -> Path:
    """Resolve relative log paths against a caller-provided base directory."""
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    anchor = Path(base_dir).expanduser() if base_dir is not None else Path.cwd()
    return anchor.resolve(strict=False) / candidate


LOG_DIR = resolve_log_path(os.environ.get("LOG_DIR", "results"), PROJECT_ROOT)
TOOL_MODE = os.environ.get("TOOL_MODE", "native").strip().lower()
try:
    STUDIO_TIMEOUT_SECONDS = int(os.environ.get("STUDIO_TIMEOUT_SECONDS", "60"))
except ValueError:
    STUDIO_TIMEOUT_SECONDS = 60

TRACE_PATH = LOG_DIR / "traces.jsonl"
RAW_RESPONSE_LOG_PATH = LOG_DIR / "raw_responses.jsonl"
DEFAULT_TASK_ID = os.environ.get("TASK_ID", "interactive")
DEFAULT_BUDGET = 50000
TRACE_STATE: dict[str, int] = {
    "cumulative_total": 0,
    "last_input_tokens": 0,
    "last_output_tokens": 0,
    "last_total_tokens": 0,
    "call_count": 0,
}
MAX_TOOL_ITERATIONS = 12
MAX_IDENTICAL_TOOL_CALLS = 2

## Terminal formatting
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, RED = "\033[34m", "\033[36m", "\033[32m", "\033[31m"


def set_trace_path(log_path: str | None, base_dir: str | Path | None = None) -> None:
    """Override the trace output path when requested on the CLI."""
    global TRACE_PATH, RAW_RESPONSE_LOG_PATH, LOG_DIR
    if not log_path:
        return
    TRACE_PATH = resolve_log_path(log_path, base_dir)
    LOG_DIR = TRACE_PATH.parent
    RAW_RESPONSE_LOG_PATH = LOG_DIR / "raw_responses.jsonl"
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACE_PATH.touch(exist_ok=True)


def get_required_str(args: JSONObject, key: str) -> str:
    """Check which args are required"""
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"missing or invalid {key}")
    return value


def get_optional_int(value: object, default: int = 0) -> int:
    """Handle a type conversion to int"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def return_to_user(args: JSONObject) -> str:
    """Tool to provide to model to explicitly call when execution is done"""
    return ""


def read(args: JSONObject) -> str:
    """Read a file contents into a single string"""
    lines = Path(get_required_str(args, "path")).read_text().splitlines(keepends=True)
    offset = get_optional_int(args.get("offset"))
    limit = get_optional_int(args.get("limit"), len(lines))
    return "\n".join(
        f"{offset + i + 1:4}| {line}"
        for i, line in enumerate(lines[offset : offset + limit])
    )


def write(args: JSONObject) -> str:
    """Tool: Write contents into a file"""
    content: str = get_required_str(args, "content")
    path = Path(get_required_str(args, "path"))
    path.write_text(content)
    return f"Wrote '{content}' to {path}"


def _find_relaxed_edit_match(text: str, old: str) -> tuple[str | None, int]:
    """Find a unique multiline match while tolerating indentation differences."""
    old_lines = old.splitlines()
    if not old_lines:
        return None, 0

    text_lines = text.splitlines(keepends=True)
    line_offsets: list[int] = []
    offset = 0
    for line in text_lines:
        line_offsets.append(offset)
        offset += len(line)

    matches: list[str] = []
    window_size = len(old_lines)
    for start in range(len(text_lines) - window_size + 1):
        window = text_lines[start : start + window_size]
        if any(window[i].strip() != old_lines[i].strip() for i in range(window_size)):
            continue
        start_offset = line_offsets[start]
        end_offset = line_offsets[start + window_size - 1] + len(
            text_lines[start + window_size - 1].rstrip("\r\n")
        )
        matches.append(text[start_offset:end_offset])

    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0], 1
    return None, len(unique_matches)


def _compress_blank_lines(text: str) -> str:
    """Remove empty lines from a snippet copied from numbered read output."""
    lines = text.splitlines()
    compact = [line for line in lines if line.strip()]
    return "\n".join(compact)


def edit(args: JSONObject) -> str:
    """Tool: Make edits to an existing file"""
    path = Path(get_required_str(args, "path"))
    text = path.read_text()
    old = get_required_str(args, "old")
    count = text.count(old)
    if not count:
        relaxed_count = 0
        for candidate_old in (old, _compress_blank_lines(old)):
            relaxed_old, candidate_count = _find_relaxed_edit_match(text, candidate_old)
            relaxed_count = max(relaxed_count, candidate_count)
            if relaxed_old is not None:
                old = relaxed_old
                count = 1
                break
        if not count:
            if relaxed_count > 1:
                return (
                    "error: old_string not found exactly and appears "
                    f"{relaxed_count} times when ignoring indentation"
                )
            return "error: old_string not found"
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"
    content: str = text.replace(
        old, get_required_str(args, "new"), -1 if args.get("all") else 1
    )
    path.write_text(content)
    return content


def glob_files(args: JSONObject) -> str:
    """Tool: Resolve a path glob"""
    pat = get_required_str(args, "pat").strip()
    if not pat:
        return "error: pat must be a non-empty glob pattern"
    pattern = str(Path(args.get("path") or ".") / pat)
    files = globlib.glob(pattern, recursive=True)
    files.sort(
        key=lambda match: Path(match).stat().st_mtime if Path(match).is_file() else 0,
        reverse=True,
    )
    return "\n".join(files) or "none"


def grep_files(args: JSONObject) -> str:
    """Tool: Search file content with regex matching"""
    pat = get_required_str(args, "pat").strip()
    if not pat:
        return "error: pat must be a non-empty regex pattern"
    pattern = re.compile(pat)
    hits: list[str] = []
    for match in globlib.glob(
        str(Path(args.get("path") or ".") / "**"), recursive=True
    ):
        path = Path(match)
        if not path.is_file():
            continue
        try:
            with path.open() as handle:
                for line_num, line in enumerate(handle, 1):
                    if pattern.search(line):
                        hits.append(f"{path}:{line_num}:{line.rstrip()}")
        except (OSError, UnicodeError):
            pass
    return "\n".join(hits[:50]) or "none"


def run_bash(args: JSONObject) -> str:
    """Tool: Run a bash command"""
    proc = subprocess.Popen(
        get_required_str(args, "cmd"),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    stdout = proc.stdout
    if stdout is None:
        raise RuntimeError("failed to capture subprocess output")
    output: list[str] = []
    try:
        while True:
            line = stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                print(f"  {DIM}│ {line.rstrip()}{RESET}", flush=True)
                output.append(line)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        output.append("\n(timed out after 30s)")
    return "".join(output).strip() or "(empty)"


# API-friendly formatting of a tool dict
TOOLS: dict[str, ToolSpec] = {
    "read": (
        "Read file with line numbers (file path, not directory)",
        {"path": "string", "offset": "number?", "limit": "number?"},
        read,
    ),
    "write": ("Write content to file", {"path": "string", "content": "string"}, write),
    "edit": (
        "Replace old with new in file (old must be unique unless all=true)",
        {"path": "string", "old": "string", "new": "string", "all": "boolean?"},
        edit,
    ),
    "glob": (
        "Find files by glob pattern, sorted by mtime. For listing a directory, use pat='*' and path='.'.",
        {"pat": "string", "path": "string?"},
        glob_files,
    ),
    "grep": (
        "Search files for regex pattern",
        {"pat": "string", "path": "string?"},
        grep_files,
    ),
    "bash": (
        "Run a shell command only when read, glob, or grep cannot do the job. Avoid bash for simple file listing or searching.",
        {"cmd": "string"},
        run_bash,
    ),
    "returnToUser": (
        "Return control to the user if no other tool calls are needed",
        {},
        return_to_user,
    ),
}


def run_tool(name: str, args: JSONObject) -> str:
    """Execute a tool"""
    try:
        return TOOLS[name][2](args)
    except KeyError:
        return f"error: unknown tool {name}"
    except Exception as err:
        return f"error: {err}"


def make_studio_tools() -> list[dict[str, object]]:
    """Convert the tools list into actual API format"""
    tools: list[dict[str, object]] = []
    for name, (description, params, _fn) in TOOLS.items():
        properties = {
            key: {
                "type": "integer"
                if value.removesuffix("?") == "number"
                else value.removesuffix("?")
            }
            for key, value in params.items()
        }
        required = [key for key, value in params.items() if not value.endswith("?")]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return tools


def get_tool_path(name: str, args: JSONObject) -> Path | None:
    """Resolve the tool path"""
    if name not in {"read", "write", "edit", "glob", "grep"}:
        return None
    path = args.get("path")
    return Path(path).resolve() if isinstance(path, str) else None


def make_tool_signature(name: str, args: JSONObject) -> str:
    """Fill in the arguments for a tool"""
    normalized_args = dict(args)
    path = get_tool_path(name, args)
    if path is not None:
        normalized_args["path"] = str(path)
    return json.dumps(
        {"name": name, "arguments": normalized_args},
        sort_keys=True,
    )


def normalize_read_output(result: str) -> str:
    """Normalizes the read tool output"""
    lines: list[str] = []
    for line in result.splitlines():
        match = re.match(r"^\s*\d+\| ?", line)
        lines.append(re.sub(r"^\s*\d+\| ?", "", line) if match else line)
    return "\n".join(lines)


def user_requested_direct_write(user_input: str, path: Path) -> bool:
    """Checks if a user allows the model to write over existing files without reading"""
    request = user_input.lower()
    path_text = str(path).lower()
    explicit_phrases = (
        "create",
        "overwrite",
        "replace",
        "rewrite",
        "truncate",
        "new file",
        "from scratch",
    )
    if any(phrase.lower() in request.lower() for phrase in explicit_phrases):
        return True
    return (
        "write" in request.lower()
        and " to " in request.lower()
        and (
            path.name.lower() in request.lower() or path_text.lower() in request.lower()
        )
    )


def enforce_tool_policy(
    tool_name: str,
    tool_args: JSONObject,
    user_input: str,
    last_read_steps: dict[str, int],
    last_mutations: dict[str, ToolState],
    last_reads: dict[str, ToolState],
    repeated_calls: dict[str, int],
    max_identical_tool_calls: int = MAX_IDENTICAL_TOOL_CALLS,
) -> str | None:
    """Check the model-generated tool call"""
    signature = make_tool_signature(tool_name, tool_args)
    if repeated_calls.get(signature, 0) >= max_identical_tool_calls:
        return (
            f"error: repeated identical {tool_name} blocked; use the existing result "
            "or choose a different action"
        )

    path = get_tool_path(tool_name, tool_args)
    if path is None:
        return None

    path_key = str(path)
    last_read_step = last_read_steps.get(path_key, -1)
    last_mutation = last_mutations.get(path_key)
    last_mutation_step = (
        last_mutation["step"]
        if last_mutation and isinstance(last_mutation.get("step"), int)
        else -1
    )

    if (
        tool_name == "write"
        and path.exists()
        and last_read_step <= last_mutation_step
        and not user_requested_direct_write(user_input, path)
    ):
        return (
            f"error: read {tool_args.get('path', path.name)} before writing it unless "
            "the user explicitly asked to create or overwrite the file"
        )

    if (
        tool_name in {"write", "edit"}
        and last_mutation
        and last_mutation.get("signature") == signature
        and last_read_step <= last_mutation_step
    ):
        return (
            f"error: repeated identical {tool_name} blocked for "
            f"{tool_args.get('path', path.name)}; read the file before retrying"
        )

    if tool_name == "read":
        last_read = last_reads.get(path_key)
        if (
            last_read
            and last_read.get("signature") == signature
            and last_read.get("step_after_mutation") == last_mutation_step
        ):
            return (
                f"error: repeated identical read blocked for "
                f"{tool_args.get('path', path.name)}; choose a different action"
            )

    return None


def update_tool_state(
    tool_name: str,
    tool_args: JSONObject,
    result: str,
    step: int,
    last_read_steps: dict[str, int],
    last_mutations: dict[str, ToolState],
    last_reads: dict[str, ToolState],
    repeated_calls: dict[str, int],
) -> bool:
    """Manages tool states for the policy"""
    signature = make_tool_signature(tool_name, tool_args)
    repeated_calls[signature] = repeated_calls.get(signature, 0) + 1

    path = get_tool_path(tool_name, tool_args)
    if path is None or result.startswith("error:"):
        return False

    path_key = str(path)
    if tool_name == "read":
        last_read_steps[path_key] = step
        last_reads[path_key] = {
            "step": step,
            "signature": signature,
            "content": normalize_read_output(result),
            "step_after_mutation": (
                last_mutations[path_key]["step"] if path_key in last_mutations else -1
            ),
        }
        last_mutation = last_mutations.get(path_key)
        expected_content = (
            last_mutation.get("expected_content")
            if isinstance(last_mutation, dict)
            else None
        )
        return isinstance(
            expected_content, str
        ) and expected_content == normalize_read_output(result)
    if tool_name in {"write", "edit"}:
        last_mutations[path_key] = {
            "step": step,
            "signature": signature,
            "expected_content": (
                get_required_str(tool_args, "content")
                if tool_name == "write"
                else result
            ),
        }
    return False


def convert_messages(system_prompt: str, messages: list[Message]) -> list[Message]:
    """Manipulates messages to inject system prompt"""
    result: list[Message] = [{"role": "system", "content": system_prompt}]
    for message in messages:
        role, content = message["role"], message["content"]
        if role == "user" and isinstance(content, str):
            result.append({"role": "user", "content": content})
            continue
        if role == "assistant":
            text: list[str] = []
            tool_calls: list[dict[str, object]] = []
            for block in content:
                if block["type"] == "text":
                    text.append(block["text"])
                elif block["type"] == "tool_use":
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"]),
                            },
                        }
                    )
            result.append(
                {
                    "role": "assistant",
                    "content": "\n".join(text),
                    "tool_calls": tool_calls,
                }
            )
            continue
        for block in content:
            if block["type"] == "tool_result":
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block["content"],
                    }
                )
    return result


def extract_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


def format_json_error(err: json.JSONDecodeError, source: object) -> str:
    """Summarize a JSON parsing failure with a short source preview."""
    preview = source if isinstance(source, str) else repr(source)
    preview = preview.replace("\n", "\\n")
    if len(preview) > 160:
        preview = preview[:157] + "..."
    return f"{err.msg} at line {err.lineno} column {err.colno}; source={preview}"


def append_raw_response_row(
    *,
    task_id: str,
    condition: str,
    role: str,
    model: str,
    stage: str,
    raw_body: str,
    details: object | None = None,
) -> None:
    """Write raw response bodies and parse diagnostics to a sidecar log."""
    RAW_RESPONSE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now().isoformat(),
        "task_id": task_id,
        "condition": condition,
        "role": role,
        "model": model,
        "stage": stage,
        "raw_body": raw_body,
    }
    if details is not None:
        row["details"] = details
    with RAW_RESPONSE_LOG_PATH.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def parse_tool_arguments(
    arguments: object,
) -> tuple[JSONObject | None, str | None]:
    """Parse the proposed tool call arguments."""
    if arguments in (None, ""):
        return {}, None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as err:
            return None, format_json_error(err, arguments)
    if not isinstance(arguments, dict):
        return None, "tool arguments must decode to an object"
    return arguments, None


def normalize_usage(payload: object) -> Usage:
    """Normalizes the token usage"""
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = get_optional_int(
        usage.get("input_tokens", usage.get("prompt_tokens", 0))
    )
    output_tokens = get_optional_int(
        usage.get("output_tokens", usage.get("completion_tokens", 0))
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": get_optional_int(
            usage.get("total_tokens", usage.get("total")), input_tokens + output_tokens
        ),
    }


def extract_tool_calls(raw_calls: object) -> tuple[list[dict[str, object]], list[str]]:
    """Parse the list of agent-proposed tool calls."""
    calls: list[dict[str, object]] = []
    errors: list[str] = []
    for i, tool_call in enumerate(raw_calls or []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str):
            continue
        arguments, error = parse_tool_arguments(function.get("arguments"))
        if error is not None or arguments is None:
            errors.append(
                f"Skipped native tool call '{name}' at index {i}: "
                f"{error or 'invalid arguments'}"
            )
            continue
        calls.append(
            {
                "id": tool_call.get("id", f"call_{i}"),
                "name": name,
                "arguments": arguments,
            }
        )
    return calls, errors


def extract_structured_response(
    text: object,
) -> tuple[str, list[dict[str, object]], list[str]]:
    """Parse the agent's response."""
    if not isinstance(text, str):
        return "", [], []
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return text, [], []
    if not isinstance(payload, dict):
        return text, [], []
    tool_calls: list[dict[str, object]] = []
    errors: list[str] = []
    for i, tool_call in enumerate(payload.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        arguments: object = tool_call.get("arguments", {})
        if not isinstance(name, str):
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            arguments = function.get("arguments", {})
        if not isinstance(name, str):
            continue
        parsed_arguments, error = parse_tool_arguments(arguments)
        if error is not None or parsed_arguments is None:
            errors.append(
                f"Skipped emulated tool call '{name}' at index {i}: "
                f"{error or 'invalid arguments'}"
            )
            continue
        tool_calls.append(
            {
                "id": tool_call.get("id", f"call_{i}"),
                "name": name,
                "arguments": parsed_arguments,
            }
        )
    assistant_text = payload.get("assistant_text")
    return (
        assistant_text
        if isinstance(assistant_text, str)
        else ("" if tool_calls else text)
    ), tool_calls, errors


def normalize_response(payload: object) -> Response:
    """Normalize the agent's response"""
    payload = payload if isinstance(payload, dict) else {}
    choices = payload.get("choices", [])
    first = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    message = first.get("message", {}) if isinstance(first.get("message"), dict) else {}
    text = extract_message_text(message.get("content", ""))
    tool_calls, parse_errors = extract_tool_calls(message.get("tool_calls"))
    assistant_text = text if tool_calls else ""
    if not tool_calls:
        assistant_text, tool_calls, structured_errors = extract_structured_response(text)
        parse_errors.extend(structured_errors)
    if parse_errors and not assistant_text:
        assistant_text = (
            "The model returned malformed tool arguments, so this turn was skipped."
        )
    stop_reason = first.get("finish_reason") or payload.get("stop_reason") or "stop"
    if tool_calls and stop_reason == "stop":
        stop_reason = "tool_calls"
    content = ([{"type": "text", "text": assistant_text}] if assistant_text else []) + [
        {
            "type": "tool_use",
            "id": tool_call.get("id", f"call_{i}"),
            "name": tool_call["name"],
            "input": tool_call["arguments"],
        }
        for i, tool_call in enumerate(tool_calls)
    ]
    return {
        "assistant_text": assistant_text,
        "tool_calls": tool_calls,
        "usage": normalize_usage(payload),
        "stop_reason": stop_reason,
        "content": content,
        "parse_errors": parse_errors,
    }


def append_trace_row(
    task_id: str, condition: str, role: str, usage: Usage, stop_reason: str
) -> None:
    """Output a log to the trace file"""
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACE_STATE["cumulative_total"] += usage["total_tokens"]
    TRACE_STATE["last_input_tokens"] = usage["input_tokens"]
    TRACE_STATE["last_output_tokens"] = usage["output_tokens"]
    TRACE_STATE["last_total_tokens"] = usage["total_tokens"]
    TRACE_STATE["call_count"] += 1
    row = {
        "task_id": task_id,
        "condition": condition,
        "role": role,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "cumulative_total": TRACE_STATE["cumulative_total"],
        "stop_reason": stop_reason,
    }
    with TRACE_PATH.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def reset_trace_state() -> None:
    TRACE_STATE["cumulative_total"] = 0
    TRACE_STATE["last_input_tokens"] = 0
    TRACE_STATE["last_output_tokens"] = 0
    TRACE_STATE["last_total_tokens"] = 0
    TRACE_STATE["call_count"] = 0


def get_trace_state() -> dict[str, int]:
    return dict(TRACE_STATE)


def extract_text(content_blocks: list[dict[str, object]]) -> str:
    """Pulls text from the parsed-normalized response data"""
    return "\n".join(
        block["text"]
        for block in content_blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()


def call_api(
    model: str | None,
    max_tokens: int,
    system: str,
    messages: list[Message],
    tools: list[dict[str, object]],
    task_id: str = DEFAULT_TASK_ID,
    condition: str = "single",
    role: str = "single",
) -> Response:
    """Invoke the GenStudio API"""
    if not API_KEY:
        raise RuntimeError("Missing API_KEY")
    if not API_URL:
        raise RuntimeError("Missing API_URL")
    use_native_tools = bool(tools) and TOOL_MODE == "native"
    extra_instruction = ""
    if tools and not use_native_tools:
        extra_instruction = (
            "\n\nTool use is being emulated for this request. If you need a tool, respond "
            "with JSON only in this shape: "
            '{"assistant_text":"","tool_calls":[{"id":"call_1","name":"tool_name",'
            '"arguments":{"arg":"value"}}]}. If you do not need a tool, respond with '
            "normal assistant text."
        )
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(
            {
                "model": model,
                "max_tokens": max_tokens,
                "messages": convert_messages(
                    system + extra_instruction,
                    messages,
                ),
                **({"tools": tools} if use_native_tools else {}),
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=STUDIO_TIMEOUT_SECONDS
        ) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            try:
                decoded = json.loads(raw_body)
            except json.JSONDecodeError as err:
                append_raw_response_row(
                    task_id=task_id,
                    condition=condition,
                    role=role,
                    model=model,
                    stage="response_json_decode_error",
                    raw_body=raw_body,
                    details={"error": format_json_error(err, raw_body)},
                )
                raise RuntimeError(
                    "Studio returned an invalid JSON response; see "
                    f"{RAW_RESPONSE_LOG_PATH} for details"
                ) from err
            normalized = normalize_response(decoded)
            if normalized["parse_errors"]:
                append_raw_response_row(
                    task_id=task_id,
                    condition=condition,
                    role=role,
                    model=model,
                    stage="tool_argument_parse_error",
                    raw_body=raw_body,
                    details={"parse_errors": normalized["parse_errors"]},
                )
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Studio request failed with HTTP {err.code}: {detail}"
        ) from err
    append_trace_row(
        task_id, condition, role, normalized["usage"], normalized["stop_reason"]
    )
    return normalized


def separator() -> str:
    """Terminal output separation line"""
    width = min(shutil.get_terminal_size(fallback=(80, 24)).columns, 80)
    return f"{DIM}{'─' * width}{RESET}"


def status_line() -> str:
    """Formatted status line for terminal output"""
    clock = datetime.now().strftime("%H:%M:%S")
    return (
        f"{DIM}[{clock}] API calls: {TRACE_STATE['call_count']} | "
        f"Last tokens in/out/total: {TRACE_STATE['last_input_tokens']}/"
        f"{TRACE_STATE['last_output_tokens']}/{TRACE_STATE['last_total_tokens']} | "
        f"Session tokens: {TRACE_STATE['cumulative_total']}{RESET}"
    )


def render_markdown(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)


def display_path(path_text: str) -> str:
    try:
        path = Path(path_text)
        return str(path.relative_to(Path.cwd())) if path.is_absolute() else path_text
    except ValueError:
        return path_text


def summarize_tool_output(tool_name: str, result: str) -> str:
    if tool_name == "glob":
        entries = [line.strip() for line in result.splitlines() if line.strip()]
        if not entries or entries == ["none"]:
            return "Matches:\n- none"
        return "Matches:\n" + "\n".join(f"- {display_path(entry)}" for entry in entries)
    return result


SYSTEM_PROMPT: str = f"""
You are a concise coding assistant with tool access.

Operate with this protocol:
1. If a previous tool result already answers the user's request, stop using tools and answer.
2. Never repeat an identical tool call after a successful result.
3. Prefer the minimum number of tool calls needed to complete the task.
4. Read before editing existing files unless the user explicitly asked to overwrite them.
5. Prefer read, glob, and grep over bash whenever they can accomplish the task.
6. You must call return_to_user if no other tools are needed to fulfill the request.
7. Never read a file you just wrote unless specifically asked to.

cwd: {os.getcwd()}
"""


def run_task_workflow(
    task_dir: str,
    condition: str,
    model: str | None,
    budget: int,
) -> int:
    """Run one packaged task in single-agent or orchestrated mode."""
    from orchestrator import run_orchestrated
    from run_single import run_single_agent
    from task_support import load_task_bundle

    loaded = load_task_bundle(task_dir, model, condition)
    loaded["config"].max_total_tokens = budget
    if condition == "single":
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
    output = json.dumps(result, indent=2)
    print(output)
    return 0


def run_repl(model: str | None, budget: int) -> int:
    """Run the interactive nanoagent REPL."""
    selected_model = model or MODEL
    preview_len = 60
    reset_trace_state()

    print(
        f"{BOLD}nanoagent{RESET} | {DIM}{selected_model} (GenAI Studio) | {os.getcwd()}{RESET}\n"
    )
    messages: list[Message] = []

    # This is the REPL loop for the nanoagent runtime
    while True:
        try:
            print(status_line())
            print(separator())
            user_input = input(f"{BOLD}{BLUE}❯{RESET} ").strip()
            print(separator())
            if not user_input:
                continue
            if user_input in ("/q", "exit"):
                break
            if user_input == "/c":
                messages.clear()
                reset_trace_state()
                print(f"{GREEN}* Cleared conversation{RESET}")
                continue
            messages.append({"role": "user", "content": user_input})
            tool_step = 0
            last_read_steps: dict[str, int] = {}
            last_mutations: dict[str, ToolState] = {}
            last_reads: dict[str, ToolState] = {}
            repeated_calls: dict[str, int] = {}

            # Get the model's response and iterate through tool calls
            # This also sends tool output back to the model so it can propose additional calls
            while True:
                if get_trace_state()["cumulative_total"] >= budget:
                    print(
                        f"\n{CYAN}*{RESET} Token budget exhausted ({budget} total tokens)."
                    )
                    return 0
                response = call_api(
                    selected_model,
                    8192,
                    SYSTEM_PROMPT,
                    messages,
                    make_studio_tools(),
                    DEFAULT_TASK_ID,
                    "single",
                    "single",
                )
                response_tool_calls = (
                    response["tool_calls"]
                    if isinstance(response.get("tool_calls"), list)
                    else []
                )
                response_content = (
                    response["content"]
                    if isinstance(response.get("content"), list)
                    else []
                )

                # Loop control handle
                if (
                    response_tool_calls
                    and isinstance(response_tool_calls[0], dict)
                    and response_tool_calls[0].get("name") == "returnToUser"
                ):
                    for block in response_content:
                        if block.get("type") == "text" and isinstance(
                            block.get("text"), str
                        ):
                            print(f"\n{CYAN}*{RESET} {render_markdown(block['text'])}")
                    break

                print(status_line())
                tool_results: list[dict[str, str]] = []
                halt_reason = ""

                # Check each piece of the response for tool calls
                for block in response_content:
                    if block["type"] == "text":
                        print(f"\n{CYAN}*{RESET} {render_markdown(block['text'])}")
                    elif block["type"] == "tool_use":
                        tool_step += 1
                        tool_name = block["name"]
                        tool_args = block["input"]
                        arg_preview = str(next(iter(tool_args.values()), ""))[:50]
                        print(
                            f"\n{GREEN}* {tool_name.capitalize()}{RESET}({DIM}{arg_preview}{RESET})"
                        )
                        if tool_step > MAX_TOOL_ITERATIONS:
                            result = (
                                "error: tool iteration limit reached for this user "
                                "request"
                            )
                            halt_reason = (
                                "Stopped after reaching the tool iteration limit"
                            )
                        else:
                            result = enforce_tool_policy(
                                tool_name,
                                tool_args,
                                user_input,
                                last_read_steps,
                                last_mutations,
                                last_reads,
                                repeated_calls,
                            ) or run_tool(tool_name, tool_args)
                        if result.startswith("error: repeated identical"):
                            halt_reason = "Stopped after a repeated identical tool request was blocked"
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
                        lines = result.splitlines() or [result]
                        preview = lines[0][:preview_len]
                        if len(lines) > 1:
                            preview += f" ... +{len(lines) - 1} lines"
                        elif len(lines[0]) > 60:
                            preview += "..."
                        print(f"  {DIM}⎿  {preview}{RESET}")
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result,
                            }
                        )

                # The API requires us to build up and re-send the whole conversation
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": response_tool_calls,
                        "content": response_content,
                    }
                )

                if len(tool_results) > 0 and response["stop_reason"] == "tool_calls":
                    # Nifty trick to trim down "noise" in the message content
                    tool_content_strings = []
                    for tool_string in tool_results:
                        tool_content_strings.append(
                            f"A tool call created the following output: {tool_string['content']}"
                        )
                    messages.append(
                        {"role": "user", "content": "\n".join(tool_content_strings)}
                    )
                elif halt_reason != "":
                    print(f"\n{CYAN}*{RESET} {halt_reason}.")
                    break
                else:
                    break
            print()
        except (KeyboardInterrupt, EOFError):  # Stop on Ctrl-C or Ctrl-D
            break
        except Exception as err:
            traceback.print_exc()
            print(f"{RED}* Error: {err}{RESET}")

    return 0


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for either REPL or packaged-task execution."""
    parser = argparse.ArgumentParser(
        description="Run nanoagent interactively or execute a packaged task workflow."
    )
    parser.add_argument("--task", help="Path to the task directory")
    parser.add_argument(
        "--condition",
        choices=("single", "orchestrated"),
        default="single",
        help="Which workflow to run",
    )
    parser.add_argument(
        "--budget",
        default=DEFAULT_BUDGET,
        type=int,
        help="Maximum total token budget",
    )
    parser.add_argument(
        "--model",
        help="Override the default model name from MODEL",
    )
    parser.add_argument(
        "--log",
        help="Path to the output trace log file (default: results/traces.jsonl)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task_base_dir = Path(args.task).expanduser() if args.task else None
    set_trace_path(args.log, task_base_dir)
    if args.task:
        return run_task_workflow(args.task, args.condition, args.model, args.budget)
    return run_repl(args.model, args.budget)


if __name__ == "__main__":
    raise SystemExit(main())
