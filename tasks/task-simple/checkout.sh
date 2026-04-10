#!/usr/bin/env bash

set -e

REPO_URL="https://github.com/rybkr/textstats"
BASE_BRANCH="task-simple"
TASK_DIR="repo"

rm -rf "$TASK_DIR"
git clone "$REPO_URL" "$TASK_DIR"
cd "$TASK_DIR"
git checkout "$BASE_BRANCH"
