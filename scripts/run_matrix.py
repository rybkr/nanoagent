#!/usr/bin/env python3
"""Run the lab matrix across selected task packs and conditions."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results"


def load_task_metadata(task_dir: Path) -> dict[str, object]:
    return json.loads((task_dir / "task.json").read_text())


def iter_task_dirs(phase: str) -> list[Path]:
    task_dirs = sorted(path for path in TASKS_DIR.iterdir() if (path / "task.json").exists())
    if phase == "all":
        return task_dirs
    return [
        path
        for path in task_dirs
        if str(load_task_metadata(path).get("phase", "in_class")) == phase
    ]


def selected_conditions(condition: str) -> list[str]:
    return ["single", "orchestrated"] if condition == "both" else [condition]


def run_once(task_dir: Path, condition: str, model: str | None) -> dict[str, object]:
    cmd = [sys.executable, "main.py", "--task", str(task_dir), "--condition", condition]
    if model:
        cmd.extend(["--model", model])
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "run failed")
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the single-agent and/or orchestrated workflow across task packs."
    )
    parser.add_argument(
        "--phase",
        choices=("in_class", "takehome", "all"),
        default="in_class",
        help="Which task packs to run",
    )
    parser.add_argument(
        "--condition",
        choices=("single", "orchestrated", "both"),
        default="both",
        help="Which workflow(s) to run",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model override passed through to main.py",
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for task_dir in iter_task_dirs(args.phase):
        metadata = load_task_metadata(task_dir)
        task_id = str(metadata["task_id"])
        for condition in selected_conditions(args.condition):
            result = run_once(task_dir, condition, args.model)
            output_path = RESULTS_DIR / f"{task_id}__{condition}.json"
            output_path.write_text(json.dumps(result, indent=2) + "\n")
            print(f"wrote {output_path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
