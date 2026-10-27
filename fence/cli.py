"""Typer entrypoints. Commands are added as each milestone lands."""

import typer

from fence import __version__

app = typer.Typer(add_completion=False, help="Fence: know why code exists before you remove it.")


@app.command()
def version() -> None:
    """Print the Fence version."""
    typer.echo(__version__)
