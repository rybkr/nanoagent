#!/bin/sh
set -eu
cd "$(dirname "$0")/repo"
PYTHONPATH=src python3 - <<'PY'
import click
from click.testing import CliRunner


@click.command()
@click.option("--name", is_flag=False, flag_value="Flag", default="Default")
def hello(name):
    click.echo(f"Hello, {name}!")


@click.command()
@click.option("--count", is_flag=False, flag_value="1", type=int, default=0)
def repeat(count):
    for i in range(count):
        click.echo(f"Line {i + 1}")


runner = CliRunner()
hello_result = runner.invoke(hello, ["--name"])
assert hello_result.exit_code == 0, hello_result.output
assert hello_result.output == "Hello, Flag!\n", hello_result.output

repeat_result = runner.invoke(repeat, ["--count"])
assert repeat_result.exit_code == 0, repeat_result.output
assert repeat_result.output == "Line 1\n", repeat_result.output
PY
