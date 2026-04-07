#!/usr/bin/env python3
"""nanoagent - minimal claude code alternative"""

from collections.abc import Callable
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

TRACE_PATH = Path(os.environ.get("NANOCODE_TRACE_PATH", "results/traces.jsonl"))
DEFAULT_TASK_ID = os.environ.get("NANOCODE_TASK_ID", "interactive")
DEFAULT_CONDITION = os.environ.get("NANOCODE_CONDITION", "single")
TRACE_STATE: dict[str, int] = {"cumulative_total": 0}

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, RED = "\033[34m", "\033[36m", "\033[32m", "\033[31m"
MAX_TOOL_ITERATIONS = 25


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
    return f"Successfully wrote {content} to {path}" 


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
    pattern = str(Path(args.get("path") or ".") / get_required_str(args, "pat"))
    files = globlib.glob(pattern, recursive=True)
    files.sort(
        key=lambda match: Path(match).stat().st_mtime if Path(match).is_file() else 0,
        reverse=True,
    )
    return "\n".join(files) or "none"


def grep_files(args: JSONObject) -> str:
    pattern = re.compile(get_required_str(args, "pat"))
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
        "Find files by pattern, sorted by mtime",
        {"pat": "string", "path": "string?"},
        glob_files,
    ),
    "grep": (
        "Search files for regex pattern",
        {"pat": "string", "path": "string?"},
        grep_files,
    ),
    "bash": ("Run shell command", {"cmd": "string"}, run_bash),
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
    if name not in {"read", "write", "edit"}:
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
    return "write" in request and " to " in request and (
        path.name.lower() in request or path_text in request
    )


def enforce_tool_policy(
    tool_name: str,
    tool_args: JSONObject,
    user_input: str,
    last_read_steps: dict[str, int],
    last_mutations: dict[str, ToolState],
    last_reads: dict[str, ToolState],
) -> str | None:
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
    signature = make_tool_signature(tool_name, tool_args)

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
) -> bool:
    path = get_tool_path(tool_name, tool_args)
    if path is None or result.startswith("error:"):
        return False

    path_key = str(path)
    if tool_name == "read":
        last_read_steps[path_key] = step
        last_reads[path_key] = {
            "step": step,
            "signature": make_tool_signature(tool_name, tool_args),
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
        return isinstance(expected_content, str) and expected_content == normalize_read_output(
            result
        )
    elif tool_name in {"write", "edit"}:
        last_mutations[path_key] = {
            "step": step,
            "signature": make_tool_signature(tool_name, tool_args),
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
        raise RuntimeError("Missing GENAI_STUDIO_API_KEY")
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(
            {
                "model": model,
                "max_tokens": max_tokens,
                "messages": convert_messages(
                    system,
                    messages,
                ),
                "tools": tools,
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


def render_markdown(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)


SYSTEM_PROMPT: str = f"""
You are a concise coding assistant with tool access.

Operate with this protocol:
1. If the user explicitly asks to create a new file or fully overwrite a file, call write directly.
2. If the user asks to modify an existing file and the final contents are not fully specified, read the file before editing or writing.
3. After a successful write or edit, read the file once to verify the result when verification is useful.
4. If the read result confirms the user's request has been satisfied, stop calling tools and respond with a short completion message.
5. Do not repeat the same write or edit with the same arguments after a successful result unless a read showed the file is still wrong.
6. Do not repeat a tool call that already failed with the same arguments; choose a different action.
7. Prefer the minimum number of tool calls needed to complete the task.

Important completion rule:
If the latest successful tool result or verification read already satisfies the user's request, you are done. Do not call another tool.

If tool calling is unsupported and you need a tool, respond with JSON only in this form:
{{"tool_calls":[{{"id":"call_1","name":"tool_name","arguments":{{"arg":"value"}}}}]}}

cwd: {os.getcwd()}
"""

def main() -> None:
    print(
        f"{BOLD}nanoagent{RESET} | {DIM}{MODEL} (GenAI Studio) | {os.getcwd()}{RESET}\n"
    )
    messages: list[Message] = []
    while True:
        try:
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
                tool_results: list[dict[str, str]] = []
                stop_after_response = False
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
                            stop_after_response = True
                        else:
                            result = enforce_tool_policy(
                                tool_name,
                                tool_args,
                                user_input,
                                last_read_steps,
                                last_mutations,
                                last_reads,
                            ) or run_tool(tool_name, tool_args)
                        if result.startswith("error: repeated identical"):
                            stop_after_response = True
                        verified_complete = update_tool_state(
                            tool_name,
                            tool_args,
                            result,
                            tool_step,
                            last_read_steps,
                            last_mutations,
                            last_reads,
                        )
                        if verified_complete:
                            stop_after_response = True
                        lines = result.splitlines() or [result]
                        preview = lines[0][:60]
                        if len(lines) > 1:
                            preview += f" ... +{len(lines) - 1} lines"
                        elif len(lines[0]) > 60:
                            preview += "..."
                        print(f"  {DIM}⎿  {preview}{RESET}")
                        if verified_complete:
                            print(
                                f"\n{CYAN}⏺{RESET} Verified; request appears satisfied."
                            )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result,
                            }
                        )
                messages.append({"role": "assistant", "content": response["content"]})
                if not tool_results or stop_after_response:
                    break
                messages.append({"role": "user", "content": tool_results})
            print()
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")


if __name__ == "__main__":
    main()
