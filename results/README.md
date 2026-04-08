# Results

- `traces.jsonl` is the per-model-call token trace written by `nanoagent.py`.
- `click-<id>__<condition>.json` files are per-run outputs written by `scripts/run_matrix.py`.
- Use the CSV templates in this directory for the in-class and take-home comparison tables.

Each JSONL trace row records:
- `task_id`
- `condition`
- `role`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `cumulative_total`
- `stop_reason`

