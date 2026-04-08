# `flag_value` is not taken into account with `envvar`

Source: https://github.com/pallets/click/issues/2746

Issue: `pallets/click#2746`
Opened: June 24, 2024
Labels: `bug`
Milestone: `8.2.0`

## Original Description

When using the `DEBUG` environment variable in the sample command, the debug value is not correctly set (`flag_value`). It is expected to be either `logging.DEBUG` or `None`, but it seems to be getting the integer value directly from the environment variable.

Sample:

```python
import logging
import os
import sys

import click

# Works as expected
# sys.argv = ['', '--debug']

# Does not work as expected
# os.environ['DEBUG'] = '1'

@click.command()
@click.option('--debug', is_flag=True, flag_value=logging.DEBUG, envvar='DEBUG')
def sample(debug):
    click.echo(f"DEBUG: {debug}")
    assert debug in [logging.DEBUG, None], (
        f"Invalid debug value: {debug} - expected >{logging.DEBUG}< or None"
    )

if __name__ == '__main__':
    sample()
```

There is no difference in using `os.environ` or `DEBUG=1 python cli.py`.

```text
DEBUG=8 python cli.py
```

prints:

```text
8
```

Environment:
- Python version: `3.12.4`
- Click version: `8.1.7`
