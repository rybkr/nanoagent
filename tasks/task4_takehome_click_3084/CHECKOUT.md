# Checkout

This task pack was staged from `pallets/click`.

- Repo URL: `https://github.com/pallets/click.git`
- Pre-fix ref: `8.3.1`
- Pre-fix commit: `f80cf4b09fbe36099b7bfc065fbde30d053da46d`
- Post-fix ref: `8.3.2`
- Post-fix commit: `c421c63eb794d98f8b546bd84627f36a5a373970`

## Reset Commands

```bash
git -C repo reset --hard
git -C repo clean -fd
```

## Refresh From Cache

```bash
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" fetch --tags --prune origin
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" worktree remove --force "/Users/ryanbaker/School/ece50874/nanoagent/tasks/task4_takehome_click_3084/repo"
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" worktree add --detach "/Users/ryanbaker/School/ece50874/nanoagent/tasks/task4_takehome_click_3084/repo" "8.3.1"
```
