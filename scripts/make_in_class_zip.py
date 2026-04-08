#!/usr/bin/env python3
"""Build the student-facing in-class lab zip."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "nanoagent_lab_in_class"
DEFAULT_OUTPUT = ROOT / f"{PACKAGE_NAME}.zip"

ROOT_FILES = (
    "README.md",
    "main.py",
    "nanoagent.py",
    "run_single.py",
    "orchestrator.py",
    "task_support.py",
    "pyproject.toml",
    "uv.lock",
)

RESULT_FILES = (
    "README.md",
    "in_class_results_template.csv",
)

TASKS = (
    "task0_in_class_click_2500",
    "task1_in_class_click_2697",
    "task2_in_class_click_2746",
)

TASK_FILE_DENYLIST = {
    ".git",
    "INSTRUCTOR_ONLY.json",
}


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree_filtered(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part in TASK_FILE_DENYLIST for part in rel.parts):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        copy_file(path, target)


def build_staging_tree(staging_root: Path) -> Path:
    package_root = staging_root / PACKAGE_NAME
    package_root.mkdir(parents=True, exist_ok=True)

    for relative in ROOT_FILES:
        src = ROOT / relative
        if not src.exists():
            raise FileNotFoundError(f"missing required file: {src}")
        copy_file(src, package_root / relative)

    results_root = package_root / "results"
    for relative in RESULT_FILES:
        src = ROOT / "results" / relative
        if not src.exists():
            raise FileNotFoundError(f"missing required results file: {src}")
        copy_file(src, results_root / relative)

    tasks_root = package_root / "tasks"
    for task_name in TASKS:
        src = ROOT / "tasks" / task_name
        if not src.exists():
            raise FileNotFoundError(f"missing required task directory: {src}")
        copy_tree_filtered(src, tasks_root / task_name)

    return package_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the student-facing in-class lab zip."
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Destination zip path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nanoagent_in_class_zip_") as tmp_dir:
        staging_root = Path(tmp_dir)
        package_root = build_staging_tree(staging_root)
        archive_base = output.with_suffix("")
        created = shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=package_root.parent,
            base_dir=package_root.name,
        )

    final_path = Path(created)
    if final_path != output:
        if output.exists():
            output.unlink()
        final_path.replace(output)

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
