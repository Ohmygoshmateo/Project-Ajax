"""Bay Four command line.

Deliberately has no `publish`. Nothing here uploads, and no credential is ever
read — the last human check before something goes public is the point of this
pipeline, not an inconvenience it should route around.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ajax_studio.model import Episode, load_series

app = typer.Typer(
    help="Bay Four — validate, schedule, and previz the series.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

SERIES_DIR = Path(__file__).resolve().parents[2] / "series" / "episodes"
OUT_DIR = Path(__file__).resolve().parents[2] / "out"


def _episodes(directory: Path | None, number: int | None) -> list[Episode]:
    """The episodes a command should act on, or exit saying why it cannot."""
    source = directory or SERIES_DIR
    found = load_series(source)
    if not found:
        console.print(f"[yellow]No episodes found in {source}.[/]")
        raise typer.Exit(1)
    if number is None:
        return found

    chosen = [e for e in found if e.number == number]
    if not chosen:
        have = ", ".join(str(e.number) for e in found)
        console.print(f"[red]No episode {number}.[/] Available: {have}")
        raise typer.Exit(1)
    return chosen


@app.command()
def validate(
    episode: int = typer.Option(None, help="One episode number; default is all."),
    directory: Path = typer.Option(None, help="Episode directory."),
) -> None:
    """Check scripts against the series bible. Errors fail; warnings inform."""
    from ajax_studio.validate import check
    from ajax_studio.validate import render as render_report

    failed = False
    for item in _episodes(directory, episode):
        report = check(item)
        render_report(report, console)
        failed = failed or not report.ok

    # A non-zero exit only for errors. A short episode is a note to the writer,
    # not a broken build, and CI should not treat it as one.
    raise typer.Exit(1 if failed else 0)


@app.command()
def plan(
    episode: int = typer.Option(1, help="Episode number."),
    gap: float = typer.Option(1.2, help="Seconds between beats."),
    directory: Path = typer.Option(None, help="Episode directory."),
) -> None:
    """The cue schedule, and where the runtime lands against the target."""
    from ajax_studio.timeline import build as build_timeline
    from ajax_studio.timeline import render as render_timeline

    for item in _episodes(directory, episode):
        render_timeline(build_timeline(item, gap=gap), console)


@app.command()
def previz(
    episode: int = typer.Option(1, help="Episode number."),
    out: Path = typer.Option(None, help="Output MP4 path."),
    gap: float = typer.Option(1.2, help="Seconds between beats."),
    directory: Path = typer.Option(None, help="Episode directory."),
) -> None:
    """Render a watchable animatic — real timing, placeholder visuals.

    The point is to judge pacing before paying for a voice or for assets. Every
    placeholder frame is marked as one, so a previz cut cannot be mistaken for a
    finished one.
    """
    from ajax_studio.render import previz as render_previz
    from ajax_studio.timeline import build as build_timeline

    (item,) = _episodes(directory, episode)
    schedule = [(c.beat, c.start, c.duration) for c in build_timeline(item, gap=gap).cues]

    target = out or (OUT_DIR / f"{item.slug}-previz.mp4")
    target.parent.mkdir(parents=True, exist_ok=True)

    console.print(f"[dim]Rendering {len(schedule)} beats…[/]")
    result = render_previz(item, target, schedule=schedule)

    console.print(f"[green]Wrote[/] {target}")
    console.print(
        f"[dim]{result.duration_s / 60:.1f} min · {result.size_bytes / 1e6:.1f} MB · "
        f"{result.placeholder_beats} placeholder beat(s), {result.asset_beats} with assets[/]"
    )
    console.print(f"[yellow]{result.audio_note}[/]")


@app.command()
def shorts(
    episode: int = typer.Option(1, help="Episode number."),
    seconds: float = typer.Option(45.0, help="Target clip length."),
    out: Path = typer.Option(None, help="Output MP4 path."),
    directory: Path = typer.Option(None, help="Episode directory."),
) -> None:
    """Cut a vertical excerpt for cross-platform posting."""
    from ajax_studio.shorts import pick, render_vertical

    (item,) = _episodes(directory, episode)
    excerpt = pick(item, seconds=seconds)

    target = out or (OUT_DIR / f"{item.slug}-short.mp4")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = render_vertical(item, excerpt, target)

    console.print(f"[green]Wrote[/] {target}")
    console.print(f"[dim]{excerpt.summary if hasattr(excerpt, 'summary') else excerpt}[/]")
    console.print(f"[dim]{result.duration_s:.1f}s vertical[/]")


@app.command()
def metadata(
    episode: int = typer.Option(1, help="Episode number."),
    directory: Path = typer.Option(None, help="Episode directory."),
) -> None:
    """Titles, description, tags, chapters, and the disclosures they must carry."""
    from ajax_studio.metadata import build as build_metadata
    from ajax_studio.metadata import check as check_metadata

    (item,) = _episodes(directory, episode)
    meta = build_metadata(item)

    console.print("\n[bold]Title variants[/] [dim](A/B — the first is the default)[/]")
    for variant in meta.titles:
        console.print(f"  {variant}")

    console.print("\n[bold]Chapters[/]")
    for chapter in meta.chapters:
        console.print(f"  {chapter.line()}")

    console.print(f"\n[bold]Tags[/]\n  {', '.join(meta.tags)}")
    console.print(f"\n[bold]Description[/]\n{meta.description}")

    console.print(
        f"\n[bold]Altered content disclosure:[/] {meta.altered_content.form_answer} — "
        f"[dim]{meta.altered_content.reason}[/]"
    )

    problems = check_metadata(meta)
    if problems:
        console.print("\n[red]Problems[/]")
        for problem in problems:
            console.print(f"  [red]•[/] {problem}")
        raise typer.Exit(1)
    console.print("\n[green]No problems.[/]")


if __name__ == "__main__":  # pragma: no cover
    app()
