#!/usr/bin/env bash
# checkout.sh — Task: psf__requests-1921 (simple)
# Removes a default session header by setting it to None

set -e

REPO_URL="https://github.com/rybkr/textstats"
BASE_BRANCH="task-medium"
TASK_DIR="repo"

git clone "$REPO_URL" "$TASK_DIR"
cd "$TASK_DIR"
git checkout "$BASE_BRANCH"
