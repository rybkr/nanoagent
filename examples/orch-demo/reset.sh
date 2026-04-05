#!/bin/sh

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR/repo"

git -C "$REPO_DIR" reset --hard HEAD
git -C "$REPO_DIR" clean -fd
