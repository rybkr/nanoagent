#!/usr/bin/env python3
"""nanoagent - minimal claude code alternative"""

from collections.abc import Callable
from datetime import datetime
import glob as globlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

type JSONValue = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)
type JSONObject = dict[str, JSONValue]
type ToolSpec = tuple[str, dict[str, str], Callable[[JSONObject], str]]
type Message = dict[str, object]
type Response = dict[str, object]
type Usage = dict[str, int]
type ToolState = dict[str, object]

API_URL = os.environ.get("API_URL")
API_KEY = os.environ.get("API_KEY")
MODEL = os.environ.get("MODEL")
TOOL_MODE = os.environ.get("TOOL_MODE", "native").strip().lower()

TRACE_PATH = Path(os.environ.get("TRACE_PATH", "results/traces.jsonl"))
DEFAULT_TASK_ID = os.environ.get("TASK_ID", "interactive")
DEFAULT_CONDITION = os.environ.get("CONDITION", "single")
TRACE_STATE: dict[str, int] = {
    "cumulative_total": 0,
    "last_input_tokens": 0,
    "last_output_tokens": 0,
    "last_total_tokens": 0,
    "call_count": 0,
}

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, RED = "\033[34m", "\033[36m", "\033[32m", "\033[31m"
MAX_TOOL_ITERATIONS = 12
MAX_IDENTICAL_TOOL_CALLS = 2


def get_required_str(args: JSONObject, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"missing or invalid {key}")
    return value


def get_optional_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read(args: JSONObject) -> str:
    lines = Path(get_required_str(args, "path")).read_text().splitlines(keepends=True)
    offset = get_optional_int(args.get("offset"))
    limit = get_optional_int(args.get("limit"), len(lines))
    return "\n".join(
        f"{offset + i + 1:4}| {line}"
        for i, line in enumerate(lines[offset : offset + limit])
    )


def write(args: JSONObject) -> str:
    content: str = get_required_str(args, "content")
    path = Path(get_required_str(args, "path"))
    path.write_text(content)
    return f"Wrote '{content}' to {path}"


def edit(args: JSONObject) -> str:
    path = Path(get_required_str(args, "path"))
    text = path.read_text()
    old = get_required_str(args, "old")
    count = text.count(old)
    if not count:
        return "error: old_string not found"
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"
    content: str = text.replace(
        old, get_required_str(args, "new"), -1 if args.get("all") else 1
    )
    path.write_text(content)
    return content


def glob_files(args: JSONObject) -> str:
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
}


def run_tool(name: str, args: JSONObject) -> str:
    try:
        return TOOLS[name][2](args)
    except KeyError:
        return f"error: unknown tool {name}"
    except Exception as err:
        return f"error: {err}"


def make_studio_tools() -> list[dict[str, object]]:
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
    if name not in {"read", "write", "edit", "glob", "grep"}:
        return None
    path = args.get("path")
    return Path(path).resolve() if isinstance(path, str) else None


def make_tool_signature(name: str, args: JSONObject) -> str:
    normalized_args = dict(args)
    path = get_tool_path(name, args)
    if path is not None:
        normalized_args["path"] = str(path)
    return json.dumps(
        {"name": name, "arguments": normalized_args},
        sort_keys=True,
    )


def normalize_read_output(result: str) -> str:
    lines: list[str] = []
    for line in result.splitlines():
        match = re.match(r"^\s*\d+\| ?", line)
        lines.append(re.sub(r"^\s*\d+\| ?", "", line) if match else line)
    return "\n".join(lines)


def user_requested_direct_write(user_input: str, path: Path) -> bool:
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
    if any(phrase in request for phrase in explicit_phrases):
        return True
    return (
        "write" in request
        and " to " in request
        and (path.name.lower() in request or path_text in request)
    )


def enforce_tool_policy(
    tool_name: str,
    tool_args: JSONObject,
    user_input: str,
    last_read_steps: dict[str, int],
    last_mutations: dict[str, ToolState],
    last_reads: dict[str, ToolState],
    repeated_calls: dict[str, int],
) -> str | None:
    signature = make_tool_signature(tool_name, tool_args)
    if repeated_calls.get(signature, 0) >= MAX_IDENTICAL_TOOL_CALLS:
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


def parse_tool_arguments(arguments: object) -> JSONObject:
    if arguments in (None, ""):
        return {}
    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must decode to an object")
    return arguments


def normalize_usage(payload: object) -> Usage:
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
            usage.get("total_tokens"), input_tokens + output_tokens
        ),
    }


def extract_tool_calls(raw_calls: object) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    for i, tool_call in enumerate(raw_calls or []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str):
            continue
        calls.append(
            {
                "id": tool_call.get("id", f"call_{i}"),
                "name": name,
                "arguments": parse_tool_arguments(function.get("arguments")),
            }
        )
    return calls


def extract_structured_response(text: object) -> tuple[str, list[dict[str, object]]]:
    if not isinstance(text, str):
        return "", []
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return text, []
    if not isinstance(payload, dict):
        return text, []
    tool_calls: list[dict[str, object]] = []
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
        tool_calls.append(
            {
                "id": tool_call.get("id", f"call_{i}"),
                "name": name,
                "arguments": parse_tool_arguments(arguments),
            }
        )
    assistant_text = payload.get("assistant_text")
    return (
        assistant_text
        if isinstance(assistant_text, str)
        else ("" if tool_calls else text)
    ), tool_calls


def normalize_response(payload: object) -> Response:
    payload = payload if isinstance(payload, dict) else {}
    choices = payload.get("choices", [])
    first = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else {}
    )
    message = first.get("message", {}) if isinstance(first.get("message"), dict) else {}
    text = message.get("content", "")
    tool_calls = extract_tool_calls(message.get("tool_calls"))
    assistant_text = text if tool_calls and isinstance(text, str) else ""
    if not tool_calls:
        assistant_text, tool_calls = extract_structured_response(text)
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
    }


def append_trace_row(
    task_id: str, condition: str, role: str, usage: Usage, stop_reason: str
) -> None:
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


def extract_text(content_blocks: list[dict[str, object]]) -> str:
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
    condition: str = DEFAULT_CONDITION,
    role: str = "single",
) -> Response:
    if not API_KEY:
        raise RuntimeError("Missing API_KEY")
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
    with urllib.request.urlopen(request) as response:
        normalized = normalize_response(json.loads(response.read()))
    append_trace_row(
        task_id, condition, role, normalized["usage"], normalized["stop_reason"]
    )
    return normalized


def separator() -> str:
    width = min(shutil.get_terminal_size(fallback=(80, 24)).columns, 80)
    return f"{DIM}{'─' * width}{RESET}"


def status_line() -> str:
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
1. If a tool result already answers the user's request, stop using tools and answer.
2. Never repeat an identical tool call after a successful result.
3. Prefer the minimum number of tool calls needed to complete the task.
4. Read before editing existing files unless the user explicitly asked to overwrite them.
5. Prefer read, glob, and grep over bash whenever they can accomplish the task.

cwd: {os.getcwd()}
"""


def main() -> None:
    print(
        f"{BOLD}nanoagent{RESET} | {DIM}{MODEL} (GenAI Studio) | {os.getcwd()}{RESET}\n"
    )
    messages: list[Message] = []
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
                print(f"{GREEN}⏺ Cleared conversation{RESET}")
                continue
            messages.append({"role": "user", "content": user_input})
            tool_step = 0
            last_read_steps: dict[str, int] = {}
            last_mutations: dict[str, ToolState] = {}
            last_reads: dict[str, ToolState] = {}
            repeated_calls: dict[str, int] = {}
            while True:
                response = call_api(
                    MODEL,
                    8192,
                    SYSTEM_PROMPT,
                    messages,
                    make_studio_tools(),
                    DEFAULT_TASK_ID,
                    DEFAULT_CONDITION,
                    "single",
                )
                print(status_line())
                tool_results: list[dict[str, str]] = []
                halt_reason = None
                for block in response["content"]:
                    if block["type"] == "text":
                        print(f"\n{CYAN}⏺{RESET} {render_markdown(block['text'])}")
                    elif block["type"] == "tool_use":
                        tool_step += 1
                        tool_name = block["name"]
                        tool_args = block["input"]
                        arg_preview = str(next(iter(tool_args.values()), ""))[:50]
                        print(
                            f"\n{GREEN}⏺ {tool_name.capitalize()}{RESET}({DIM}{arg_preview}{RESET})"
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
                        preview = lines[0][:60]
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
                messages.append({"role": "assistant", "content": response["content"]})
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                if not tool_results:
                    break
                if halt_reason:
                    print(f"\n{CYAN}⏺{RESET} {halt_reason}.")
                    break
            print()
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")


if __name__ == "__main__":
    main()
