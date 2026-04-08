# `click.prompt` and `click.confirm` `prompt_suffix` no longer works when suffix is empty

Source: https://github.com/pallets/click/issues/3019

Issue: `pallets/click#3019`
Opened: July 28, 2025
Milestone: `8.3.1`

## Original Description

After earlier prompt-handling fixes, passing `prompt_suffix=""` no longer behaves like older Click releases.

Before the regression, an empty suffix meant no extra trailing character was added to the prompt:

```python
>>> import click
>>> click.prompt("test", prompt_suffix="")
testfoo
'foo'
```

Current behavior in newer releases incorrectly inserts a space even when the suffix is explicitly empty:

```python
>>> import click
>>> click.prompt("test", prompt_suffix="")
test foo
'foo'
```

This matters for prompts where the user should continue typing immediately after a fixed prefix, for example:

```python
import click
click.prompt("What IP address would you like? : 192.168.1.", prompt_suffix="")
```

Expected display:

```text
What IP address would you like? : 192.168.1.123
```

Actual display:

```text
What IP address would you like? : 192.168.1. 123
```

Environment:
- Python version: `3.13`
- Click version: `8.0.3`
