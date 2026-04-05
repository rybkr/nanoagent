This fixture provides a disposable nested repo and task directory for testing `orchestrator.py`.

Layout:
- `repo/`: tiny Python project with one intentional bug
- `task/`: orchestrator task inputs targeting that repo

Run:

```sh
export GENAI_STUDIO_API_KEY=your_key_here
cd /Users/ryanbaker/School/ece50874/nanoagent
python3 orchestrator.py --task examples/orch-demo/task
```

Reset after a run:

```sh
cd /Users/ryanbaker/School/ece50874/nanoagent/examples/orch-demo
./reset.sh
```

Expected outcome:
- The orchestrator should change only `calc.py`
- The nested repo tests should pass
- The final JSON should report `"accepted": true`
