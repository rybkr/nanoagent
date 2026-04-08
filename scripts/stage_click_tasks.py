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
    pre_fix_ref: str
    post_fix_ref: str
    known_fix_ref: str | None
    fix_search_terms: tuple[str, ...]
    issue_url: str
    extra_urls: tuple[str, ...]
    issue_markdown: str


TASKS = (
    TaskSpec(
        slug="task0_in_class_click_2500",
        task_id="click-2500",
        issue_number=2500,
        title="Empty string default values are not displayed",
        difficulty="simple",
        pre_fix_ref="8.1.7",
        post_fix_ref="8.1.8",
        known_fix_ref="d3e3852",
        fix_search_terms=("#2500", "#2724"),
        issue_url="https://github.com/pallets/click/issues/2500",
        extra_urls=(
            "https://github.com/pallets/click/pull/2724",
            "https://github.com/pallets/click/releases",
        ),
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
        pre_fix_ref="8.1.7",
        post_fix_ref="8.1.8",
        known_fix_ref=None,
        fix_search_terms=("#2697",),
        issue_url="https://github.com/pallets/click/issues/2697",
        extra_urls=("https://github.com/pallets/click/releases",),
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
        pre_fix_ref="8.1.7",
        post_fix_ref="8.2.0",
        known_fix_ref=None,
        fix_search_terms=("#2746", "#2788"),
        issue_url="https://github.com/pallets/click/issues/2746",
        extra_urls=(
            "https://github.com/pallets/click/pull/2788",
            "https://github.com/pallets/click/releases",
        ),
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
        run("git", "fetch", "--tags", "--prune", "origin", cwd=cache_dir)
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
    run("git", "worktree", "add", "--detach", str(repo_dir), ref, cwd=cache_dir)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n")


def write_task_files(task_dir: Path, spec: TaskSpec, cache_dir: Path) -> None:
    pre_fix_commit = git_rev_parse(cache_dir, spec.pre_fix_ref)
    post_fix_commit = git_rev_parse(cache_dir, spec.post_fix_ref)
    known_fix_commit = detect_fix_commit(cache_dir, spec)

    task_json = {
        "task_id": spec.task_id,
        "repo_path": "repo",
        "issue_url": spec.issue_url,
        "difficulty": spec.difficulty,
        "pre_fix_ref": spec.pre_fix_ref,
        "pre_fix_commit": pre_fix_commit,
        "post_fix_ref": spec.post_fix_ref,
        "post_fix_commit": post_fix_commit,
        "known_fix_commit": known_fix_commit,
        "allowed_files": [],
        "test_command": "",
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
- Difficulty: `{spec.difficulty}`
- Repo checkout: `repo/`

This task directory was generated by `scripts/stage_click_tasks.py`.
`ISSUE.md` contains the packaged issue statement, and `task.json` contains the pinned refs.
"""

    write_text(task_dir / "ISSUE.md", spec.issue_markdown)
    write_text(task_dir / "CHECKOUT.md", checkout_md)
    write_text(task_dir / "README.md", readme_md)
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
