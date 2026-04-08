#!/bin/sh
set -eu
cd "$(dirname "$0")/repo"
PYTHONPATH=src python3 - <<'PY'
import click
from click.testing import CliRunner


@click.command()
def prompt_cli():
    value = click.prompt(
        "What IP address would you like? : 192.168.1.",
        prompt_suffix="",
    )
    click.echo(f"VALUE={value}")


@click.command()
def confirm_cli():
    value = click.confirm("Continue", prompt_suffix="")
    click.echo(f"CONFIRM={value}")


runner = CliRunner()
prompt_result = runner.invoke(prompt_cli, input="123\n")
assert prompt_result.exit_code == 0, prompt_result.output
assert "192.168.1.123" in prompt_result.output, prompt_result.output
assert "192.168.1. 123" not in prompt_result.output, prompt_result.output

confirm_result = runner.invoke(confirm_cli, input="y\n")
assert confirm_result.exit_code == 0, confirm_result.output
assert "Continue[y/N]" in confirm_result.output, confirm_result.output
assert "Continue [y/N]" not in confirm_result.output, confirm_result.output
PY
