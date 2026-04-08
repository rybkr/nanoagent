#!/bin/sh
set -eu
cd "$(dirname "$0")/repo"
python3 -m pytest -q tests/test_types.py
