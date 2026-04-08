# Checkout

This task pack was staged from `pallets/click`.

- Repo URL: `https://github.com/pallets/click.git`
- Pre-fix ref: `8.1.7`
- Pre-fix commit: `006ae84d651846c944331fff78e340701fe58562`
- Post-fix ref: `8.1.8`
- Post-fix commit: `d4ecb8a89a52ad3faacf3f1c65065d08c1d11c14`

## Reset Commands

```bash
git -C repo reset --hard
git -C repo clean -fd
```

## Refresh From Cache

```bash
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" fetch --tags --prune origin
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" worktree remove --force "/Users/ryanbaker/School/ece50874/nanoagent/tasks/task1_in_class_click_2697/repo"
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" worktree add --detach "/Users/ryanbaker/School/ece50874/nanoagent/tasks/task1_in_class_click_2697/repo" "8.1.7"
```
