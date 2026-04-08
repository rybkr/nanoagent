# Empty string default values are not displayed

Source: https://github.com/pallets/click/issues/2500

Issue: `pallets/click#2500`
Opened: April 28, 2023
Milestone: `8.1.8`

## Original Description

If an option has a default value of `""`, the `show_default` option does not display anything.

Reproducible example:

```python
import click

@click.group()
def test_cli() -> None:
    pass

@test_cli.command(help="Test CLI")
@click.option("--test_value", type=str, default="", show_default=True)
def test_function(test_value: str) -> None:
    print(test_value)

@click.group()
def cli() -> None:
    pass

cli.add_command(test_cli)
cli()
```

Expected to see a default displayed.

Environment:
- Python version: `3.11.3`
- Click version: `8.0.3`
