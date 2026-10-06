"""Read what a command documents without letting the terminal decide the answer.

Typer renders help through Rich, and a test that greps the rendered screen is
testing the terminal as much as the CLI. Two separate effects were observed:

* Rich styles every switch, so ``--release-id`` arrives as escape-separated
  fragments and ``"--release-id" in result.stdout`` is false whenever colour is
  on. GitHub Actions sets ``FORCE_COLOR``, which takes precedence over
  ``NO_COLOR``, so the ambient environment cannot be talked out of it.
* Rich fits the options panel to the terminal width, and when it does not fit it
  **truncates** -- at width 34 the switch renders as ``--rel…``. Those characters
  are gone, so no amount of text normalisation recovers them. Width is therefore
  something a test must *set*, never inherit.

So the switch names and the documented guarantees are read from Click's own
command and parameter objects, which is the text the CLI will render and is
independent of any terminal; and ``rendered`` additionally runs the CLI in a
fresh process at a width this module pins, with colour off, to confirm the text
really does reach a help screen -- which the declarations alone cannot show,
since a parameter can be declared and hidden. ``squash`` removes any escape that
survives and all whitespace, so a line break inside a phrase cannot matter
either; squash the needle as well as the output.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import click
import typer.main

#: CSI sequences, which is all Rich emits here. The final-byte class covers the
#: whole CSI range, so a cursor or erase sequence is removed too.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

#: Wide enough that Rich has no reason to truncate or wrap any switch this repo
#: declares. The value is pinned here rather than inherited from the runner.
HELP_WIDTH = 200


def squash(text: str) -> str:
    """Strip escape sequences and every whitespace character."""
    return re.sub(r"\s+", "", _ANSI.sub("", text))


def _command(app: typer.Typer, path: tuple[str, ...]) -> click.Command:
    command = typer.main.get_command(app)
    ctx = click.Context(command)
    for name in path:
        # Duck-typed rather than ``isinstance(command, click.Group)``: Click 8.5
        # moved TyperGroup's base out of the public ``click`` namespace, so an
        # isinstance check there silently stops matching.
        lookup = getattr(command, "get_command", None)
        assert lookup is not None, f"{' '.join(path)}: {name!r} has no parent group"
        found = lookup(ctx, name)
        assert found is not None, f"no such command: {' '.join(path)}"
        command, ctx = found, click.Context(found, parent=ctx)
    return command


def switches(app: typer.Typer, *path: str) -> set[str]:
    """Every switch the command declares, from Click's parameter objects."""
    return {opt for p in _command(app, path).params for opt in (*p.opts, *p.secondary_opts)}


def documented(app: typer.Typer, *path: str) -> str:
    """The command's own help text and each parameter's, before Rich touches it."""
    command = _command(app, path)
    parts = [command.help or "", command.short_help or ""]
    parts += [p.help or "" for p in command.params if hasattr(p, "help")]
    return "\n".join(parts)


def rendered(*path: str, width: int = HELP_WIDTH) -> tuple[int, str]:
    """Run ``<path> --help`` in a fresh process at a pinned width, uncoloured.

    A child process is what makes the width effective: Typer reads
    ``TERMINAL_WIDTH`` once, at import. Returns the exit status and the output
    with escape sequences removed.
    """
    muted = {"FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "COLUMNS"}
    env = {k: v for k, v in os.environ.items() if k not in muted}
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["TERMINAL_WIDTH"] = str(width)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from seq2lead.cli import app; app()", *path, "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return proc.returncode, _ANSI.sub("", proc.stdout + proc.stderr)
