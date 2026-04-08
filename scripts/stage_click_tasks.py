#!/usr/bin/env python3
"""Create local task packs for selected Click issues."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_URL = "https://github.com/pallets/click.git"


@dataclass(frozen=True)
class TaskSpec:
    slug: str
    task_id: str
    issue_number: int
    title: str
    difficulty: str
    phase: str
    pre_fix_ref: str
    post_fix_ref: str
    known_fix_ref: str | None
    fix_search_terms: tuple[str, ...]
    issue_url: str
    extra_urls: tuple[str, ...]
    repo_summary_markdown: str
    test_command: str
    reproduction_command: str
    allowed_files: tuple[str, ...]
    file_budget: int
    max_total_tokens: int
    expected_runtime: str
    issue_markdown: str
    acceptance_description: str = "Provided tests pass, required behavior is observed, and the patch stays within the file budget."
    setup_command: str = "python3 -m pip install -e . -r requirements/tests.txt"
    run_tests_script: str | None = None


TASKS = (
    TaskSpec(
        slug="task0_in_class_click_2500",
        task_id="click-2500",
        issue_number=2500,
        title="Empty string default values are not displayed",
        difficulty="simple",
        phase="in_class",
        pre_fix_ref="8.1.7",
        post_fix_ref="8.1.8",
        known_fix_ref="d3e3852",
        fix_search_terms=("#2500", "#2724"),
        issue_url="https://github.com/pallets/click/issues/2500",
        extra_urls=(
            "https://github.com/pallets/click/pull/2724",
            "https://github.com/pallets/click/releases",
        ),
        repo_summary_markdown="""# Repo Summary

`click` is a Python CLI library.

- Library code lives in `src/click/`.
- Tests live in `tests/`.
- Help text, option defaults, and parameter rendering commonly involve `src/click/core.py`.
- Option behavior tests are commonly in `tests/test_options.py`.

Likely relevant files for this task:
- `src/click/core.py`
- `tests/test_options.py`
""",
        test_command="python3 -m pytest -q tests/test_options.py",
        reproduction_command="python3 -m pytest -q tests/test_options.py -k default",
        allowed_files=("src/click/core.py", "tests/test_options.py"),
        file_budget=2,
        max_total_tokens=25000,
        expected_runtime="5-8 minutes",
        issue_markdown="""# Empty string default values are not displayed

Source: https://github.com/pallets/click/issues/2500

Issue: `pallets/click#2500`
Opened: April 28, 2023
Milestone: `8.1.8`

## Original Description

If an option has a default value of `""`, the `show_default` option does not display anything.

Reproducible example:

```python
import click

@click.group()
def test_cli() -> None:
    pass

@test_cli.command(help="Test CLI")
@click.option("--test_value", type=str, default="", show_default=True)
def test_function(test_value: str) -> None:
    print(test_value)

@click.group()
def cli() -> None:
    pass

cli.add_command(test_cli)
cli()
```

Expected to see a default displayed.

Environment:
- Python version: `3.11.3`
- Click version: `8.0.3`
""",
    ),
    TaskSpec(
        slug="task1_in_class_click_2697",
        task_id="click-2697",
        issue_number=2697,
        title='Broken message about invalid argument value for template "File ... is a directory"',
        difficulty="medium",
        phase="in_class",
        pre_fix_ref="8.1.7",
        post_fix_ref="8.1.8",
        known_fix_ref=None,
        fix_search_terms=("#2697",),
        issue_url="https://github.com/pallets/click/issues/2697",
        extra_urls=("https://github.com/pallets/click/releases",),
        repo_summary_markdown="""# Repo Summary

`click` is a Python CLI library.

- Library code lives in `src/click/`.
- Type validation and path error messages are commonly implemented in `src/click/types.py`.
- Tests live in `tests/`.
- Type and path behavior tests are commonly in `tests/test_types.py`.

Likely relevant files for this task:
- `src/click/types.py`
- `tests/test_types.py`
""",
        test_command="python3 -m pytest -q tests/test_types.py",
        reproduction_command="python3 -m pytest -q tests/test_types.py -k directory",
        allowed_files=("src/click/types.py", "tests/test_types.py"),
        file_budget=2,
        max_total_tokens=35000,
        expected_runtime="8-12 minutes",
        issue_markdown="""# Broken message about invalid argument value for template "File ... is a directory"

Source: https://github.com/pallets/click/issues/2697

Issue: `pallets/click#2697`
Opened: March 30, 2024
Milestone: `8.1.8`

## Original Description

User actions:

```bash
mkdir $'my\\n dir'
my-tool $'my\\n dir'
```

Expected output:

```text
Invalid value for 'PATH': File 'my\\ndir' is a directory.
```

Actual output:

```text
Invalid value for 'PATH': File 'my
dir' is a directory.
```

Code:

```python
from pathlib import Path
from typing import Annotated

import typer

def main(path: Annotated[Path, typer.Argument(dir_okay=False)]) -> None:
    pass

if __name__ == "__main__":
    typer.run(main)
```

Cause noted by the reporter:

```text
You clearly forgot !r on this line and are using quotes instead.
```

Relevant nearby templates from `click.types`:

```python
_("{name} {filename!r} does not exist.").format(
_("{name} {filename!r} is a file.").format(
_("{name} '{filename}' is a directory.").format(
_("{name} {filename!r} is not readable.").format(
_("{name} {filename!r} is not writable.").format(
_("{name} {filename!r} is not executable.").format(
```
""",
    ),
    TaskSpec(
        slug="task2_in_class_click_2746",
        task_id="click-2746",
        issue_number=2746,
        title="flag_value is not taken into account with envvar",
        difficulty="hard",
        phase="in_class",
        pre_fix_ref="8.1.7",
        post_fix_ref="8.2.0",
        known_fix_ref=None,
        fix_search_terms=("#2746", "#2788"),
        issue_url="https://github.com/pallets/click/issues/2746",
        extra_urls=(
            "https://github.com/pallets/click/pull/2788",
            "https://github.com/pallets/click/releases",
        ),
        repo_summary_markdown="""# Repo Summary

`click` is a Python CLI library.

- Library code lives in `src/click/`.
- Option parsing and envvar handling often involve `src/click/core.py` and related parameter code.
- Tests live in `tests/`.
- Option and envvar behavior tests are commonly in `tests/test_options.py`.

Likely relevant files for this task:
- `src/click/core.py`
- `tests/test_options.py`
""",
        test_command="python3 -m pytest -q tests/test_options.py",
        reproduction_command="python3 -m pytest -q tests/test_options.py -k envvar",
        allowed_files=("src/click/core.py", "tests/test_options.py"),
        file_budget=2,
        max_total_tokens=50000,
        expected_runtime="10-15 minutes",
        issue_markdown="""# `flag_value` is not taken into account with `envvar`

Source: https://github.com/pallets/click/issues/2746

Issue: `pallets/click#2746`
Opened: June 24, 2024
Labels: `bug`
Milestone: `8.2.0`

## Original Description

When using the `DEBUG` environment variable in the sample command, the debug value is not correctly set (`flag_value`). It is expected to be either `logging.DEBUG` or `None`, but it seems to be getting the integer value directly from the environment variable.

Sample:

```python
import logging
import os
import sys

import click

# Works as expected
# sys.argv = ['', '--debug']

# Does not work as expected
# os.environ['DEBUG'] = '1'

@click.command()
@click.option('--debug', is_flag=True, flag_value=logging.DEBUG, envvar='DEBUG')
def sample(debug):
    click.echo(f"DEBUG: {debug}")
    assert debug in [logging.DEBUG, None], (
        f"Invalid debug value: {debug} - expected >{logging.DEBUG}< or None"
    )

if __name__ == '__main__':
    sample()
```

There is no difference in using `os.environ` or `DEBUG=1 python cli.py`.

```text
DEBUG=8 python cli.py
```

prints:

```text
8
```

Environment:
- Python version: `3.12.4`
- Click version: `8.1.7`
""",
    ),
    TaskSpec(
        slug="task3_takehome_click_3019",
        task_id="click-3019",
        issue_number=3019,
        title="click.prompt and click.confirm prompt_suffix no longer works when suffix is empty",
        difficulty="moderate",
        phase="takehome",
        pre_fix_ref="8.2.1",
        post_fix_ref="8.3.1",
        known_fix_ref="812b800",
        fix_search_terms=("#3019", "#3021"),
        issue_url="https://github.com/pallets/click/issues/3019",
        extra_urls=(
            "https://github.com/pallets/click/pull/3021",
            "https://github.com/pallets/click/releases",
        ),
        repo_summary_markdown="""# Repo Summary

`click` is a Python CLI library.

- Prompt rendering lives in `src/click/termui.py`.
- Related tests live in `tests/test_utils.py`.
- This task is about prompt display behavior, not parsing or option declaration.

Likely relevant files for this task:
- `src/click/termui.py`
- `tests/test_utils.py`
""",
        test_command="sh ../run_tests.sh",
        reproduction_command="sh ../run_tests.sh",
        allowed_files=("src/click/termui.py", "tests/test_utils.py"),
        file_budget=2,
        max_total_tokens=32000,
        expected_runtime="10-15 minutes",
        issue_markdown="""# `click.prompt` and `click.confirm` `prompt_suffix` no longer works when suffix is empty

Source: https://github.com/pallets/click/issues/3019

Issue: `pallets/click#3019`
Opened: July 28, 2025
Milestone: `8.3.1`

## Original Description

After earlier prompt-handling fixes, passing `prompt_suffix=""` no longer behaves like older Click releases.

Before the regression, an empty suffix meant no extra trailing character was added to the prompt:

```python
>>> import click
>>> click.prompt("test", prompt_suffix="")
testfoo
'foo'
```

Current behavior in newer releases incorrectly inserts a space even when the suffix is explicitly empty:

```python
>>> import click
>>> click.prompt("test", prompt_suffix="")
test foo
'foo'
```

This matters for prompts where the user should continue typing immediately after a fixed prefix, for example:

```python
import click
click.prompt("What IP address would you like? : 192.168.1.", prompt_suffix="")
```

Expected display:

```text
What IP address would you like? : 192.168.1.123
```

Actual display:

```text
What IP address would you like? : 192.168.1. 123
```

Environment:
- Python version: `3.13`
- Click version: `8.0.3`
""",
        run_tests_script="""#!/bin/sh
set -eu
cd "$(dirname "$0")/repo"
PYTHONPATH=src python3 - <<'PY'
import click
from click.testing import CliRunner


@click.command()
def prompt_cli():
    value = click.prompt(
        "What IP address would you like? : 192.168.1.",
        prompt_suffix="",
    )
    click.echo(f"VALUE={value}")


@click.command()
def confirm_cli():
    value = click.confirm("Continue", prompt_suffix="")
    click.echo(f"CONFIRM={value}")


runner = CliRunner()
prompt_result = runner.invoke(prompt_cli, input="123\\n")
assert prompt_result.exit_code == 0, prompt_result.output
assert "192.168.1.123" in prompt_result.output, prompt_result.output
assert "192.168.1. 123" not in prompt_result.output, prompt_result.output

confirm_result = runner.invoke(confirm_cli, input="y\\n")
assert confirm_result.exit_code == 0, confirm_result.output
assert "Continue[y/N]" in confirm_result.output, confirm_result.output
assert "Continue [y/N]" not in confirm_result.output, confirm_result.output
PY
""",
    ),
    TaskSpec(
        slug="task4_takehome_click_3084",
        task_id="click-3084",
        issue_number=3084,
        title="Optional value not optional anymore",
        difficulty="higher",
        phase="takehome",
        pre_fix_ref="8.3.1",
        post_fix_ref="8.3.2",
        known_fix_ref="91de59c",
        fix_search_terms=("#3084", "#3152"),
        issue_url="https://github.com/pallets/click/issues/3084",
        extra_urls=(
            "https://github.com/pallets/click/pull/3152",
            "https://github.com/pallets/click/releases",
        ),
        repo_summary_markdown="""# Repo Summary

`click` is a Python CLI library.

- Option parsing and flag/default behavior live in `src/click/core.py`.
- Option behavior tests live in `tests/test_options.py`.
- This task concerns the interaction between `is_flag=False`, `flag_value=...`, and defaults.

Likely relevant files for this task:
- `src/click/core.py`
- `tests/test_options.py`
""",
        test_command="sh ../run_tests.sh",
        reproduction_command="sh ../run_tests.sh",
        allowed_files=("src/click/core.py", "tests/test_options.py"),
        file_budget=2,
        max_total_tokens=42000,
        expected_runtime="12-18 minutes",
        issue_markdown="""# Bug: Optional value not optional anymore

Source: https://github.com/pallets/click/issues/3084

Issue: `pallets/click#3084`
Opened: September 24, 2025
Milestone: `8.3.2`

## Original Description

Click documents that setting `is_flag=False, flag_value=value` should allow an option to be passed either with an explicit value or with no argument, in which case the value should become `flag_value`.

Example from the documentation:

```python
@click.command()
@click.option("--name", is_flag=False, flag_value="Flag", default="Default")
def hello(name):
    click.echo(f"Hello, {name}!")
```

Observed behavior in Click `8.3.0`:

```text
$ hello --name
Error: Option '--name' requires an argument.
```

Expected behavior:

```text
$ hello --name
Hello, Flag!
```

Environment:
- Python version: `3.11`
- Click version: `8.3.0`
""",
        run_tests_script="""#!/bin/sh
set -eu
cd "$(dirname "$0")/repo"
PYTHONPATH=src python3 - <<'PY'
import click
from click.testing import CliRunner


@click.command()
@click.option("--name", is_flag=False, flag_value="Flag", default="Default")
def hello(name):
    click.echo(f"Hello, {name}!")


@click.command()
@click.option("--count", is_flag=False, flag_value="1", type=int, default=0)
def repeat(count):
    for i in range(count):
        click.echo(f"Line {i + 1}")


runner = CliRunner()
hello_result = runner.invoke(hello, ["--name"])
assert hello_result.exit_code == 0, hello_result.output
assert hello_result.output == "Hello, Flag!\\n", hello_result.output

repeat_result = runner.invoke(repeat, ["--count"])
assert repeat_result.exit_code == 0, repeat_result.output
assert repeat_result.output == "Line 1\\n", repeat_result.output
PY
""",
    ),
)


def run(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def ensure_clone(cache_dir: Path) -> None:
    if cache_dir.exists():
        try:
            run("git", "fetch", "--tags", "--prune", "origin", cwd=cache_dir)
        except subprocess.CalledProcessError:
            print(
                f"warning: could not refresh {cache_dir}; using the local cached clone",
                file=sys.stderr,
            )
        return

    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    run("git", "clone", REPO_URL, str(cache_dir))
    run("git", "fetch", "--tags", "--prune", "origin", cwd=cache_dir)


def git_rev_parse(repo_dir: Path, ref: str) -> str:
    return run("git", "rev-parse", ref, cwd=repo_dir)


def detect_fix_commit(repo_dir: Path, spec: TaskSpec) -> str:
    if spec.known_fix_ref is not None:
        return git_rev_parse(repo_dir, spec.known_fix_ref)

    log_output = run(
        "git",
        "log",
        "--format=%H%x09%s",
        f"{spec.pre_fix_ref}..{spec.post_fix_ref}",
        cwd=repo_dir,
    )
    for line in log_output.splitlines():
        if not line.strip():
            continue
        commit, _, subject = line.partition("\t")
        if any(term in subject for term in spec.fix_search_terms):
            return commit
    return ""


def remove_existing_task(cache_dir: Path, task_dir: Path) -> None:
    repo_dir = task_dir / "repo"
    run("git", "worktree", "prune", cwd=cache_dir)
    if repo_dir.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(repo_dir)],
            cwd=cache_dir,
            check=False,
            capture_output=True,
            text=True,
        )
    if task_dir.exists():
        shutil.rmtree(task_dir)


def create_worktree(cache_dir: Path, task_dir: Path, ref: str) -> None:
    repo_dir = task_dir / "repo"
    run("git", "worktree", "prune", cwd=cache_dir)
    run("git", "worktree", "add", "--force", "--detach", str(repo_dir), ref, cwd=cache_dir)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n")


def write_task_files(task_dir: Path, spec: TaskSpec, cache_dir: Path) -> None:
    pre_fix_commit = git_rev_parse(cache_dir, spec.pre_fix_ref)
    post_fix_commit = git_rev_parse(cache_dir, spec.post_fix_ref)
    known_fix_commit = detect_fix_commit(cache_dir, spec)

    task_json = {
        "task_id": spec.task_id,
        "phase": spec.phase,
        "repo_path": "repo",
        "issue_url": spec.issue_url,
        "difficulty": spec.difficulty,
        "pre_fix_ref": spec.pre_fix_ref,
        "pre_fix_commit": pre_fix_commit,
        "post_fix_ref": spec.post_fix_ref,
        "post_fix_commit": post_fix_commit,
        "known_fix_commit": known_fix_commit,
        "allowed_files": list(spec.allowed_files),
        "file_budget": spec.file_budget,
        "test_command": spec.test_command,
        "reproduction_command": spec.reproduction_command,
        "setup_command": spec.setup_command,
        "acceptance_description": spec.acceptance_description,
        "expected_runtime": spec.expected_runtime,
        "max_total_tokens": spec.max_total_tokens,
        "max_single_agent_turns": 8,
        "max_planner_passes": 3,
        "max_implementer_passes": 2,
        "max_reviewer_passes": 1,
        "max_implementer_steps": 6,
        "max_tool_iterations": 12,
    }

    instructor_json = {
        "issue_number": spec.issue_number,
        "title": spec.title,
        "pre_fix_ref": spec.pre_fix_ref,
        "pre_fix_commit": pre_fix_commit,
        "post_fix_ref": spec.post_fix_ref,
        "post_fix_commit": post_fix_commit,
        "known_fix_commit": known_fix_commit,
        "issue_url": spec.issue_url,
        "extra_urls": list(spec.extra_urls),
        "allowed_files": list(spec.allowed_files),
        "test_command": spec.test_command,
        "reproduction_command": spec.reproduction_command,
    }

    checkout_md = f"""# Checkout

This task pack was staged from `pallets/click`.

- Repo URL: `{REPO_URL}`
- Pre-fix ref: `{spec.pre_fix_ref}`
- Pre-fix commit: `{pre_fix_commit}`
- Post-fix ref: `{spec.post_fix_ref}`
- Post-fix commit: `{post_fix_commit}`

## Reset Commands

```bash
git -C repo reset --hard
git -C repo clean -fd
```

## Refresh From Cache

```bash
git -C "{cache_dir}" fetch --tags --prune origin
git -C "{cache_dir}" worktree remove --force "{task_dir / 'repo'}"
git -C "{cache_dir}" worktree add --detach "{task_dir / 'repo'}" "{spec.pre_fix_ref}"
```
"""

    readme_md = f"""# {spec.title}

- Repo: `pallets/click`
- Issue: `{spec.issue_number}`
- Phase: `{spec.phase}`
- Difficulty: `{spec.difficulty}`
- Repo checkout: `repo/`
- Expected runtime: `{spec.expected_runtime}`
- Setup command: `{spec.setup_command}`
- File budget: `{spec.file_budget}`
- Allowed files: `{", ".join(spec.allowed_files)}`
- Acceptance: `{spec.acceptance_description}`
- Reproduction command: `{spec.reproduction_command}`
- Canonical test command: `{spec.test_command}`
- Suggested token budget: `{spec.max_total_tokens}`

This task directory was generated by `scripts/stage_click_tasks.py`.
`ISSUE.md` contains the packaged issue statement, and `task.json` contains the pinned refs.
"""

    run_tests_sh = spec.run_tests_script or f"""#!/bin/sh
set -eu
cd "$(dirname "$0")/repo"
{spec.test_command}
"""

    write_text(task_dir / "ISSUE.md", spec.issue_markdown)
    write_text(task_dir / "REPO_SUMMARY.md", spec.repo_summary_markdown)
    write_text(task_dir / "CHECKOUT.md", checkout_md)
    write_text(task_dir / "README.md", readme_md)
    write_text(task_dir / "run_tests.sh", run_tests_sh)
    write_text(task_dir / "task.json", json.dumps(task_json, indent=2, sort_keys=True))
    write_text(
        task_dir / "INSTRUCTOR_ONLY.json",
        json.dumps(instructor_json, indent=2, sort_keys=True),
    )


def stage_tasks(base_dir: Path, cache_dir: Path, force: bool) -> None:
    ensure_clone(cache_dir)

    for spec in TASKS:
        task_dir = base_dir / spec.slug
        if task_dir.exists():
            if not force:
                raise RuntimeError(
                    f"{task_dir} already exists; rerun with --force to replace it"
                )
            remove_existing_task(cache_dir, task_dir)

        task_dir.mkdir(parents=True, exist_ok=True)
        create_worktree(cache_dir, task_dir, spec.pre_fix_ref)
        write_task_files(task_dir, spec, cache_dir)
        print(f"staged {spec.slug} at {task_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch pallets/click and stage local lab task packs."
    )
    parser.add_argument(
        "--base-dir",
        default="tasks",
        help="Directory where task packs will be created",
    )
    parser.add_argument(
        "--cache-dir",
        default=".cache/repos/click",
        help="Local clone used to create worktrees",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing task directories",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        stage_tasks(
            base_dir=Path(args.base_dir).resolve(),
            cache_dir=Path(args.cache_dir).resolve(),
            force=args.force,
        )
    except subprocess.CalledProcessError as err:
        sys.stderr.write(err.stderr or str(err) + "\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"{err}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
