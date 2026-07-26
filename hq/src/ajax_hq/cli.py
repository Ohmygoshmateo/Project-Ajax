"""Ajax HQ command line."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ajax_hq import snapshot as snapshot_mod
from ajax_hq.collect import DEFAULT_WORKSPACE, collect
from ajax_hq.game.web import DEFAULT_GAME_PORT
from ajax_hq.render import render
from ajax_hq.serve import DEFAULT_PORT, LOOPBACK
from ajax_hq.sources.transcripts import DEFAULT_CLAUDE_HOME

app = typer.Typer(
    help="Ajax HQ — read-only operations centre for agent work.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

OUT_DIR = Path(__file__).resolve().parents[2] / "out"


def _build(claude_home: Path | None, workspace: Path | None, use_history: bool):
    snapshot = collect(claude_home=claude_home, workspace=workspace)
    if use_history:
        snapshot_mod.merge_history(snapshot)
    return snapshot


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING, format="%(message)s"
    )


@app.command()
def build(
    output: Path = typer.Option(None, help="Where to write the page."),
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
    no_history: bool = typer.Option(False, help="Ignore committed snapshots."),
    no_text: bool = typer.Option(False, help="Omit prompts and reports from the page."),
) -> None:
    """Generate the dashboard as a self-contained HTML file."""
    snapshot = _build(claude_home, workspace, not no_history)
    html = render(snapshot, include_text=not no_text)

    target = output or (OUT_DIR / "index.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)

    console.print(f"[green]Wrote[/] {target}  ({len(html):,} bytes)")
    console.print(
        f"[dim]{len(snapshot.agents)} agents · {len(snapshot.sessions)} sessions · "
        f"{len(snapshot.files)} files · {len(snapshot.commits)} commits · "
        f"{snapshot.schema.summary}[/]"
    )
    if snapshot.is_empty:
        console.print("[yellow]No agent activity found — the page will show empty states.[/]")


@app.command()
def serve(
    port: int = typer.Option(DEFAULT_PORT, help="Port on localhost."),
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
) -> None:
    """Serve the live dashboard on localhost."""
    from ajax_hq.serve import serve as run_server

    console.print(f"[bold]Ajax HQ[/] on [cyan]http://{LOOPBACK}:{port}[/]")
    console.print("[dim]Loopback only — the page contains full agent transcripts.[/]")
    console.print("[dim]Ctrl-C to stop.[/]")
    try:
        run_server(port, claude_home=claude_home, workspace=workspace,
                   history_dir=snapshot_mod.DEFAULT_HISTORY_DIR)
    except OSError as exc:
        console.print(f"[red]Could not bind port {port}:[/] {exc}")
        raise typer.Exit(1) from exc


@app.command()
def snapshot(
    directory: Path = typer.Option(None, help="History directory."),
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
) -> None:
    """Write a metadata snapshot so this history survives the container.

    Prompts and reports are never included — see src/ajax_hq/snapshot.py.
    """
    built = collect(claude_home=claude_home, workspace=workspace)
    path = snapshot_mod.write(built, directory)

    console.print(f"[green]Wrote[/] {path}")
    console.print(
        f"[dim]{len(built.agents)} agents · {len(built.sessions)} sessions · "
        f"{len(built.files)} files · {len(built.commits)} commits[/]"
    )
    console.print("[dim]Metadata only — no prompt or response text is written.[/]")


@app.command()
def agents(
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
    no_history: bool = typer.Option(False, help="Ignore committed snapshots."),
) -> None:
    """List every agent that has run, without opening a browser."""
    built = _build(claude_home, workspace, not no_history)

    if not built.agents:
        console.print("[yellow]No subagents found in any session on this machine.[/]")
        raise typer.Exit(0)

    table = Table(title=f"Agent roster — {len(built.agents)} dispatched")
    table.add_column("Agent", max_width=32, overflow="ellipsis", no_wrap=True)
    table.add_column("Type", max_width=15, overflow="ellipsis", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    # "Output" is measured text emitted, not the reported token count — that
    # field is a placeholder in subagent transcripts.
    for column in ("Elapsed", "Tools", "Output", "Files"):
        table.add_column(column, justify="right", no_wrap=True)

    for agent in built.agents:
        table.add_row(
            agent.title,
            agent.agent_type or "—",
            agent.status.label,
            agent.duration_label,
            str(agent.tools.total),
            agent.output_label,
            str(len(agent.files_touched)),
        )
    console.print(table)
    console.print(f"[dim]{built.schema.summary}[/]")
    console.print("[dim]Output is measured text emitted, in characters.[/]")

    note = built.schema.token_note
    if note:
        console.print(f"[yellow]⚠ {note}[/]")


@app.command()
def floor(
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
    no_history: bool = typer.Option(False, help="Ignore committed snapshots."),
) -> None:
    """The virtual floor — a terminal office plan of who works here."""
    from ajax_hq.floor import render as render_floor

    render_floor(_build(claude_home, workspace, not no_history), console)


@app.command()
def lineage(
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
    no_history: bool = typer.Option(False, help="Ignore committed snapshots."),
) -> None:
    """Reporting lines — which session dispatched which agent."""
    from ajax_hq.lineage import build as build_org
    from ajax_hq.lineage import render as render_org

    render_org(build_org(_build(claude_home, workspace, not no_history)), console)


@app.command()
def play(
    web: bool = typer.Option(False, "--web", help="Run in a browser instead of the terminal."),
    replay: bool = typer.Option(
        False, "--replay", help="Replay transcript history instead of following live."
    ),
    port: int = typer.Option(DEFAULT_GAME_PORT, help="Port on localhost, with --web."),
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
    no_history: bool = typer.Option(False, help="Ignore committed snapshots."),
) -> None:
    """The floor as a live game — agents roam their wing until work arrives.

    Live mode follows ``~/.claude`` as it is written, so an agent walks to
    Engineering when it actually edits a file. On a machine where nothing is
    running that is a still office; ``--replay`` plays back what already
    happened instead, clearly labelled as history.
    """
    from ajax_hq.game.sim import Simulation

    snapshot = _build(claude_home, workspace, not no_history)
    home = claude_home or DEFAULT_CLAUDE_HOME

    if web:
        from ajax_hq.game.web import LOOPBACK as GAME_LOOPBACK
        from ajax_hq.game.web import serve as serve_game

        console.print(f"[bold]Ajax HQ floor[/] on [cyan]http://{GAME_LOOPBACK}:{port}[/]")
        console.print("[dim]Loopback only — the state names what each agent is doing.[/]")
        console.print("[dim]Ctrl-C to stop.[/]")
        try:
            serve_game(snapshot, home, port=port, live=not replay)
        except OSError as exc:
            console.print(f"[red]Could not bind port {port}:[/] {exc}")
            raise typer.Exit(1) from exc
        return

    from ajax_hq.game.tui import play as play_tui

    sim = Simulation.create(snapshot, home, live=not replay)
    if not sim.floor.actors:
        console.print("[yellow]Nobody to put on the floor — no agents or sessions found.[/]")
        raise typer.Exit(0)
    play_tui(sim, console)


@app.command()
def status(
    claude_home: Path = typer.Option(None, help="Override ~/.claude."),
    workspace: Path = typer.Option(None, help="Workspace root to scan for repos."),
) -> None:
    """Division summary in the terminal."""
    built = _build(claude_home, workspace, True)

    table = Table(title="Divisions")
    for column in ("Code", "Division", "Status", "Headline", "Last active"):
        table.add_column(column)

    for division in built.divisions:
        headline = " · ".join(f"{k} {v}" for k, v in division.metrics[:2]) or "—"
        last = division.last_active.strftime("%Y-%m-%d %H:%M") if division.last_active else "—"
        table.add_row(division.code, division.name, division.status.label, headline, last)

    console.print(table)
    console.print(f"[dim]{built.schema.summary} · sources: {DEFAULT_CLAUDE_HOME}, "
                  f"{DEFAULT_WORKSPACE}[/]")


if __name__ == "__main__":  # pragma: no cover
    app()
