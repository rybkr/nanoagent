# What Does Orchestration Actually Buy Us?

This repo packages a bounded software-engineering lab for comparing a strong single-agent baseline against a tiny planner-implementer-reviewer workflow.

The core metric is:

```text
CostToSuccess = total tokens spent until the first acceptable solution
```

## Repo Layout

- `nanoagent.py`: minimal Studio-backed coding agent with tool calling, response normalization, and per-call token logging.
- `run_single.py`: single-agent baseline runner.
- `orchestrator.py`: bounded planner -> implementer -> reviewer workflow.
- `task_support.py`: task loading, acceptance checks, repo/tool sandboxing, and diff helpers.
- `main.py`: entry point for either condition.
- `scripts/stage_click_tasks.py`: regenerates the packaged task packs from pinned Click refs.
- `scripts/run_matrix.py`: runs the experiment matrix and writes JSON outputs into `results/`.
- `tasks/`: three in-class packs and two take-home packs.

## Studio Setup

Set these environment variables before running the lab:

```bash
export STUDIO_API_URL="https://genai.rcac.purdue.edu/api/chat/completions"
export STUDIO_API_KEY="..."
export STUDIO_MODEL="gpt-oss:120b"
export STUDIO_TOOL_MODE="native"   # or "json" to emulate tool calls
```

Backwards-compatible names `API_URL`, `API_KEY`, `MODEL`, and `TOOL_MODE` are still accepted.

## Running

Run a single task under one condition:

```bash
python3 main.py --task tasks/task0_in_class_click_2500 --condition single
python3 main.py --task tasks/task0_in_class_click_2500 --condition orchestrated
```

Run the full in-class matrix:

```bash
python3 scripts/run_matrix.py --phase in_class --condition both
```

Token traces append to `results/traces.jsonl`. Per-run JSON outputs go into `results/`.

## Workflow Bounds

The orchestrator is intentionally small:

- planner passes: capped in `task.json` and loaded through `TaskConfig`
- implementer passes: capped and reviewer-triggered
- reviewer critiques: capped
- tool invocations per implementer pass: capped
- total token budget: capped per task

The baseline uses the same repo snapshot, same tool interface, same acceptance function, and same token logging.

## Task Packs

In-class:

- `tasks/task0_in_class_click_2500`
- `tasks/task1_in_class_click_2697`
- `tasks/task2_in_class_click_2746`

Take-home:

- `tasks/task3_takehome_click_3019`
- `tasks/task4_takehome_click_3084`

Each task pack includes:

- `ISSUE.md`
- `REPO_SUMMARY.md`
- `CHECKOUT.md`
- `task.json`
- `run_tests.sh`
- `repo/` at the pinned pre-fix state

## Pre-Lab

Students should:

1. Obtain a GenAI Studio token and run one simple task with `main.py`.
2. Watch a short beginner-oriented LangGraph overview and read selected docs excerpts.
3. Answer these questions briefly:
   1. What problem is orchestration trying to solve that a single long prompt does not?
   2. What are the risks of adding multiple specialized roles?
   3. What would count as evidence that orchestration is worth its overhead?
   4. Why is "the answer looked good" a weak evaluation criterion?

## In-Class Deliverable

Use `results/in_class_results_template.csv` and record:

- task
- condition
- accepted or not
- total tokens
- files edited
- tool calls
- short notes about failure mode or wasted effort

Then answer:

- On which task was orchestration clearly overhead?
- On which task, if any, did orchestration begin to pay for itself?
- Where did the baseline waste effort?
- Where did the orchestrated workflow waste effort?

## Take-Home

Use the two take-home packs and `results/takehome_results_template.csv` to repeat the comparison on moderately harder tasks.

## Regenerating Task Packs

If you need to restage the local Click snapshots:

```bash
python3 scripts/stage_click_tasks.py --force
```
