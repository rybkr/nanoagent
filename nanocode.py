#!/usr/bin/env python3
"""nanocode - minimal claude code alternative"""

import glob as globlib, json, os, re, subprocess, urllib.request


def load_dotenv(path=".env"):
    try:
        with open(path) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


load_dotenv()

API_URL = os.environ.get(
    "GENAI_STUDIO_API_URL", "https://genai.rcac.purdue.edu/api/chat/completions"
)
API_KEY = os.environ.get("GENAI_STUDIO_API_KEY", os.environ.get("GENAI_STUDIO_API_KEY"))
MODEL = os.environ.get("MODEL", os.environ.get("GENAI_STUDIO_MODEL", "llama3.1:latest"))
TRACE_PATH = os.environ.get("NANOCODE_TRACE_PATH", "results/traces.jsonl")
DEFAULT_TASK_ID = os.environ.get("NANOCODE_TASK_ID", "interactive")
DEFAULT_CONDITION = os.environ.get("NANOCODE_CONDITION", "single")
TRACE_STATE = {"cumulative_total": 0}

# ANSI colors
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED = (
    "\033[34m",
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
)


# --- Tool implementations ---


def read(args):
    lines = open(args["path"]).readlines()
    offset = args.get("offset", 0)
    limit = args.get("limit", len(lines))
    selected = lines[offset : offset + limit]
    return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))


def write(args):
    with open(args["path"], "w") as f:
        f.write(args["content"])
    return "ok"


def edit(args):
    text = open(args["path"]).read()
    old, new = args["old"], args["new"]
    if old not in text:
        return "error: old_string not found"
    count = text.count(old)
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"
    replacement = (
        text.replace(old, new) if args.get("all") else text.replace(old, new, 1)
    )
    with open(args["path"], "w") as f:
        f.write(replacement)
    return "ok"


def glob(args):
    pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
    files = globlib.glob(pattern, recursive=True)
    files = sorted(
        files,
        key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
        reverse=True,
    )
    return "\n".join(files) or "none"


def grep(args):
    pattern = re.compile(args["pat"])
    hits = []
    for filepath in globlib.glob(args.get("path", ".") + "/**", recursive=True):
        try:
            for line_num, line in enumerate(open(filepath), 1):
                if pattern.search(line):
                    hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
        except Exception:
            pass
    return "\n".join(hits[:50]) or "none"


def bash(args):
    proc = subprocess.Popen(
        args["cmd"], shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True
    )
    output_lines = []
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                print(f"  {DIM}│ {line.rstrip()}{RESET}", flush=True)
                output_lines.append(line)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        output_lines.append("\n(timed out after 30s)")
    return "".join(output_lines).strip() or "(empty)"


# --- Tool definitions: (description, schema, function) ---

TOOLS = {
    "read": (
        "Read file with line numbers (file path, not directory)",
        {"path": "string", "offset": "number?", "limit": "number?"},
        read,
    ),
    "write": (
        "Write content to file",
        {"path": "string", "content": "string"},
        write,
    ),
    "edit": (
        "Replace old with new in file (old must be unique unless all=true)",
        {"path": "string", "old": "string", "new": "string", "all": "boolean?"},
        edit,
    ),
    "glob": (
        "Find files by pattern, sorted by mtime",
        {"pat": "string", "path": "string?"},
        glob,
    ),
    "grep": (
        "Search files for regex pattern",
        {"pat": "string", "path": "string?"},
        grep,
    ),
    "bash": (
        "Run shell command",
        {"cmd": "string"},
        bash,
    ),
}


def run_tool(name, args):
    try:
        return TOOLS[name][2](args)
    except Exception as err:
        return f"error: {err}"


def make_schema():
    result = []
    for name, (description, params, _fn) in TOOLS.items():
        properties = {}
        required = []
        for param_name, param_type in params.items():
            is_optional = param_type.endswith("?")
            base_type = param_type.rstrip("?")
            properties[param_name] = {
                "type": "integer" if base_type == "number" else base_type
            }
            if not is_optional:
                required.append(param_name)
        result.append(
            {
                "name": name,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return result


def make_studio_tools():
    result = []
    for tool in make_schema():
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
        )
    return result


def convert_messages(system_prompt, messages):
    result = [{"role": "system", "content": system_prompt}]
    for message in messages:
        role = message["role"]
        content = message["content"]

        if role == "user" and isinstance(content, str):
            result.append({"role": "user", "content": content})
            continue

        if role == "assistant" and isinstance(content, list):
            text_parts = []
            tool_calls = []
            for block in content:
                if block["type"] == "text":
                    text_parts.append(block["text"])
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

            assistant_message = {
                "role": "assistant",
                "content": "\n".join(text_parts),
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            result.append(assistant_message)
            continue

        if role == "user" and isinstance(content, list):
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


def parse_tool_arguments(arguments):
    if isinstance(arguments, dict):
        return arguments
    if not arguments:
        return {}
    return json.loads(arguments)


def normalize_usage(payload):
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total_tokens),
    }


def make_content_blocks(assistant_text, tool_calls):
    content = []
    if assistant_text:
        content.append({"type": "text", "text": assistant_text})
    for idx, tool_call in enumerate(tool_calls):
        content.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id", f"call_{idx}"),
                "name": tool_call["name"],
                "input": tool_call["arguments"],
            }
        )
    return content


def normalize_tool_calls(message):
    result = []
    for idx, tool_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        function = (
            tool_call.get("function")
            if isinstance(tool_call.get("function"), dict)
            else {}
        )
        if not function.get("name"):
            continue
        result.append(
            {
                "id": tool_call.get("id", f"call_{idx}"),
                "name": function["name"],
                "arguments": parse_tool_arguments(function.get("arguments")),
            }
        )
    return result


def extract_structured_response(text):
    try:
        structured = json.loads(text) if isinstance(text, str) and text else {}
    except json.JSONDecodeError:
        structured = {}

    if not isinstance(structured, dict):
        structured = {}

    assistant_text = structured.get("assistant_text")
    if not isinstance(assistant_text, str):
        assistant_text = text if isinstance(text, str) else ""

    tool_calls = []
    for idx, tool_call in enumerate(structured.get("tool_calls", [])):
        if not isinstance(tool_call, dict) or not tool_call.get("name"):
            continue
        arguments = tool_call.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = parse_tool_arguments(arguments)
        tool_calls.append(
            {
                "id": tool_call.get("id", f"call_{idx}"),
                "name": tool_call["name"],
                "arguments": arguments,
            }
        )

    return assistant_text, tool_calls


def append_trace_row(task_id, condition, role, usage, stop_reason):
    trace_dir = os.path.dirname(TRACE_PATH)
    if trace_dir:
        os.makedirs(trace_dir, exist_ok=True)

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
    with open(TRACE_PATH, "a") as f:
        f.write(json.dumps(row) + "\n")


def normalize_response(payload):
    payload = payload if isinstance(payload, dict) else {}
    choices = payload.get("choices", [])
    first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = (
        first_choice.get("message")
        if isinstance(first_choice.get("message"), dict)
        else {}
    )
    text = message.get("content", "")
    tool_calls = normalize_tool_calls(message)

    if tool_calls:
        assistant_text = text if isinstance(text, str) else ""
    else:
        assistant_text, tool_calls = extract_structured_response(text)

    stop_reason = first_choice.get("finish_reason") or payload.get("stop_reason") or "stop"
    if tool_calls and stop_reason == "stop":
        stop_reason = "tool_calls"

    usage = normalize_usage(payload)
    content = make_content_blocks(assistant_text, tool_calls)
    return {
        "assistant_text": assistant_text,
        "tool_calls": [{"name": call["name"], "arguments": call["arguments"]} for call in tool_calls],
        "usage": usage,
        "stop_reason": stop_reason,
        "content": content,
    }


def call_api(
    model,
    max_tokens,
    system,
    messages,
    tools,
    task_id=DEFAULT_TASK_ID,
    condition=DEFAULT_CONDITION,
    role="single",
):
    if not API_KEY:
        raise RuntimeError("Missing GENAI_STUDIO_API_KEY")

    fallback_prompt = (
        "\n\nIf tool calling is unsupported and you need a tool, respond with JSON only: "
        '{"tool_calls":[{"id":"call_1","name":"tool_name","arguments":{"arg":"value"}}]}.'
    )
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(
            {
                "model": model,
                "max_tokens": max_tokens,
                "messages": convert_messages(system + fallback_prompt, messages),
                "tools": tools,
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    response = urllib.request.urlopen(request)
    payload = json.loads(response.read())
    normalized = normalize_response(payload)
    append_trace_row(task_id, condition, role, normalized["usage"], normalized["stop_reason"])
    return normalized


def separator():
    return f"{DIM}{'─' * min(os.get_terminal_size().columns, 80)}{RESET}"


def render_markdown(text):
    return re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)


def main():
    print(f"{BOLD}nanocode{RESET} | {DIM}{MODEL} (GenAI Studio) | {os.getcwd()}{RESET}\n")
    messages = []
    system_prompt = f"Concise coding assistant. cwd: {os.getcwd()}"

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
                messages = []
                print(f"{GREEN}⏺ Cleared conversation{RESET}")
                continue

            messages.append({"role": "user", "content": user_input})

            # agentic loop: keep calling API until no more tool calls
            while True:
                response = call_api(
                    model=MODEL,
                    max_tokens=8192,
                    system=system_prompt,
                    messages=messages,
                    tools=make_studio_tools(),
                    task_id=DEFAULT_TASK_ID,
                    condition=DEFAULT_CONDITION,
                    role="single",
                )
                content_blocks = response.get("content", [])
                tool_results = []

                for block in content_blocks:
                    if block["type"] == "text":
                        print(f"\n{CYAN}⏺{RESET} {render_markdown(block['text'])}")

                    if block["type"] == "tool_use":
                        tool_name = block["name"]
                        tool_args = block["input"]
                        arg_preview = str(list(tool_args.values())[0])[:50]
                        print(
                            f"\n{GREEN}⏺ {tool_name.capitalize()}{RESET}({DIM}{arg_preview}{RESET})"
                        )

                        result = run_tool(tool_name, tool_args)
                        result_lines = result.split("\n")
                        preview = result_lines[0][:60]
                        if len(result_lines) > 1:
                            preview += f" ... +{len(result_lines) - 1} lines"
                        elif len(result_lines[0]) > 60:
                            preview += "..."
                        print(f"  {DIM}⎿  {preview}{RESET}")

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result,
                            }
                        )

                messages.append({"role": "assistant", "content": content_blocks})

                if not tool_results:
                    break
                messages.append({"role": "user", "content": tool_results})

            print()

        except (KeyboardInterrupt, EOFError):
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")


if __name__ == "__main__":
    main()
