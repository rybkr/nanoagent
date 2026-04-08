# Broken message about invalid argument value for template "File ... is a directory"

Source: https://github.com/pallets/click/issues/2697

Issue: `pallets/click#2697`
Opened: March 30, 2024
Milestone: `8.1.8`

## Original Description

User actions:

```bash
mkdir $'my\n dir'
my-tool $'my\n dir'
```

Expected output:

```text
Invalid value for 'PATH': File 'my\ndir' is a directory.
```

Actual output:

```text
Invalid value for 'PATH': File 'my
dir' is a directory.
```

Code:

```python
from pathlib import Path
from typing import Annotated

import typer

def main(path: Annotated[Path, typer.Argument(dir_okay=False)]) -> None:
    pass

if __name__ == "__main__":
    typer.run(main)
```

Cause noted by the reporter:

```text
You clearly forgot !r on this line and are using quotes instead.
```

Relevant nearby templates from `click.types`:

```python
_("{name} {filename!r} does not exist.").format(
_("{name} {filename!r} is a file.").format(
_("{name} '{filename}' is a directory.").format(
_("{name} {filename!r} is not readable.").format(
_("{name} {filename!r} is not writable.").format(
_("{name} {filename!r} is not executable.").format(
```
