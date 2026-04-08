# Bug: Optional value not optional anymore

Source: https://github.com/pallets/click/issues/3084

Issue: `pallets/click#3084`
Opened: September 24, 2025
Milestone: `8.3.2`

## Original Description

Click documents that setting `is_flag=False, flag_value=value` should allow an option to be passed either with an explicit value or with no argument, in which case the value should become `flag_value`.

Example from the documentation:

```python
@click.command()
@click.option("--name", is_flag=False, flag_value="Flag", default="Default")
def hello(name):
    click.echo(f"Hello, {name}!")
```

Observed behavior in Click `8.3.0`:

```text
$ hello --name
Error: Option '--name' requires an argument.
```

Expected behavior:

```text
$ hello --name
Hello, Flag!
```

Environment:
- Python version: `3.11`
- Click version: `8.3.0`
