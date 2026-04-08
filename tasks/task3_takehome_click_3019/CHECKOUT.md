# Checkout

This task pack was staged from `pallets/click`.

- Repo URL: `https://github.com/pallets/click.git`
- Pre-fix ref: `8.2.1`
- Pre-fix commit: `d44436997f26cb2890ff3c094352540473c69777`
- Post-fix ref: `8.3.1`
- Post-fix commit: `f80cf4b09fbe36099b7bfc065fbde30d053da46d`

## Reset Commands

```bash
git -C repo reset --hard
git -C repo clean -fd
```

## Refresh From Cache

```bash
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" fetch --tags --prune origin
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" worktree remove --force "/Users/ryanbaker/School/ece50874/nanoagent/tasks/task3_takehome_click_3019/repo"
git -C "/Users/ryanbaker/School/ece50874/nanoagent/.cache/repos/click" worktree add --detach "/Users/ryanbaker/School/ece50874/nanoagent/tasks/task3_takehome_click_3019/repo" "8.2.1"
```
