"""Render a Snapshot to a self-contained HTML page.

Rules enforced here rather than left to the caller:

* every panel names the source it read;
* an absent source renders an empty state naming the command that would fill it,
  never a zeroed chart;
* derived figures are labelled as derived;
* nothing is displayed that was not measured.
"""

from __future__ import annotations

import html
from datetime import datetime

from ajax_hq.assets import CSS, SEAL_SVG, refresh_script
from ajax_hq.model import Agent, Division, Snapshot, Status
from ajax_hq.timeutil import aware, now

MAX_DRILL_CHARS = 6000


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _stamp(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    stamp = aware(value)
    return stamp.strftime(fmt) if stamp else "—"


def _pill(status: Status) -> str:
    return f'<span class="pill {esc(status.value)}">{esc(status.label)}</span>'


def _truncate(text: str | None) -> tuple[str, bool]:
    if not text:
        return "", False
    if len(text) <= MAX_DRILL_CHARS:
        return text, False
    return text[:MAX_DRILL_CHARS], True


# ------------------------------------------------------------------ sections


def _masthead(snapshot: Snapshot) -> str:
    generated = _stamp(snapshot.generated_at, "%Y-%m-%d %H:%M:%S UTC")
    versions = ", ".join(snapshot.schema.client_versions) or "unknown"

    banners = []
    if snapshot.is_empty:
        banners.append(
            '<div class="banner">No agent activity found on this machine. HQ reads '
            "Claude Code transcripts from <code>~/.claude/projects</code>; if this "
            "container is new, there is nothing to report yet.</div>"
        )
    if snapshot.schema.records_unparsed:
        banners.append(
            f'<div class="banner bad">Schema drift: {snapshot.schema.summary}. '
            f"These are undocumented internals (client {esc(versions)}) and the format "
            "may have changed — some panels may be incomplete.</div>"
        )
    if snapshot.schema.token_note:
        banners.append(f'<div class="banner">{esc(snapshot.schema.token_note)}</div>')
    if snapshot.schema.unknown_record_types:
        banners.append(
            '<div class="banner">Unrecognised record types: '
            f"<code>{esc(', '.join(snapshot.schema.unknown_record_types))}</code>. "
            "Ignored safely; the client is likely newer than this reader.</div>"
        )
    if snapshot.restored_sessions:
        banners.append(
            f'<div class="banner ok">{snapshot.restored_sessions} session(s) restored '
            "from committed history — those figures are archival, not live.</div>"
        )
    for warning in snapshot.warnings:
        banners.append(f'<div class="banner bad">{esc(warning)}</div>')

    return f"""
<header class="masthead">
  {SEAL_SVG}
  <div class="brand">
    <h1>AJAX HQ</h1>
    <p class="ko">에이잭스 본사 · OPERATIONS CENTRE</p>
  </div>
  <div class="spacer"></div>
  <div class="clock">
    <strong>{esc(generated)}</strong>
    client {esc(versions)} · {esc(snapshot.schema.summary)}
  </div>
</header>
{"".join(banners)}
"""


def _executive_strip(snapshot: Snapshot) -> str:
    start, end = snapshot.span
    tiles = [
        ("Agents dispatched", f"{len(snapshot.agents)}", "subagents with transcripts"),
        ("Work streams", f"{len(snapshot.sessions)}", "sessions on record"),
        ("Files touched", f"{len(snapshot.files)}", "derived from tool calls"),
        ("Commits", f"{len(snapshot.commits)}", "landed in version control"),
        ("Tool calls", f"{snapshot.total_tool_calls:,}", "principal and agents"),
        ("Tokens", f"{snapshot.total_tokens:,}", "fresh input and output"),
        ("Context reused", f"{snapshot.total_cache_tokens:,}", "cache reads, not new usage"),
        ("Span", snapshot.span_label, f"{_stamp(start, '%b %d')} → {_stamp(end, '%b %d')}"),
    ]
    cells = "".join(
        f'<div class="stat"><div class="k">{esc(k)}</div>'
        f'<div class="v">{esc(v)}</div><div class="s">{esc(s)}</div></div>'
        for k, v, s in tiles
    )
    return f'<section class="section"><div class="stats">{cells}</div></section>'


def _division_card(division: Division) -> str:
    metrics = "".join(
        f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in division.metrics
    ) or "<dt>No activity recorded</dt><dd>—</dd>"

    notes = "".join(f'<p class="note">{esc(n)}</p>' for n in division.notes)
    sources = esc(" · ".join(division.sources)) if division.sources else ""

    return f"""
<article class="division">
  <div class="top">
    <span class="code">{esc(division.code)}</span>
    <h3>{esc(division.name)}<span class="ko">{esc(division.korean)}</span></h3>
    <div class="spacer" style="flex:1"></div>
    {_pill(division.status)}
  </div>
  <p class="mandate">{esc(division.mandate)}</p>
  <dl>{metrics}</dl>
  {notes}
  <p class="src">{sources} · last activity {esc(_stamp(division.last_active))}</p>
</article>
"""


def _divisions(snapshot: Snapshot) -> str:
    if not snapshot.divisions:
        return ""
    cards = "".join(_division_card(d) for d in snapshot.divisions)
    return f"""
<section class="section">
  <h2>Divisions <span class="ko">사업부</span></h2>
  <div class="divisions">{cards}</div>
</section>
"""


def _agent_row(agent: Agent, include_text: bool) -> str:
    files = ""
    if agent.files_touched:
        listed = "".join(f"<div>{esc(p)}</div>" for p in agent.files_touched[:8])
        extra = (
            f"<div class='dim'>+{len(agent.files_touched) - 8} more</div>"
            if len(agent.files_touched) > 8
            else ""
        )
        files = f'<div class="label">Files touched</div><pre>{listed}{extra}</pre>'

    # The drill-down lives in its own full-width row: nesting it in the first
    # cell squeezes the transcript into that column's width, which makes long
    # prompts unreadable.
    drill_row = ""
    if include_text and (agent.prompt or agent.report or files):
        parts = [files]
        if agent.prompt:
            text, cut = _truncate(agent.prompt)
            suffix = "\n\n… truncated" if cut else ""
            parts.append(
                f'<div class="label">Task given</div><pre>{esc(text)}{esc(suffix)}</pre>'
            )
        if agent.report:
            text, cut = _truncate(agent.report)
            suffix = "\n\n… truncated" if cut else ""
            parts.append(
                f'<div class="label">Report returned</div><pre>{esc(text)}{esc(suffix)}</pre>'
            )
        drill_row = f"""
<tr class="drill-row"><td colspan="9">
  <details class="drill">
    <summary>Personnel file — {esc(agent.title)}</summary>
    {"".join(parts)}
  </details>
</td></tr>"""

    # Measured text emitted, not the reported output_tokens — that field is a
    # placeholder in subagent transcripts (see Agent.output_tokens_are_plausible).
    cache = (
        f'<div class="dim" style="font-size:11px">{agent.cache_tokens:,} ctx</div>'
        if agent.cache_tokens
        else ""
    )

    return f"""
<tr>
  <td>
    <strong>{esc(agent.title)}</strong>
    <div class="dim mono">{esc(agent.agent_id[:12])}</div>
  </td>
  <td class="mono">{esc(agent.agent_type or "—")}</td>
  <td>{_pill(agent.status)}</td>
  <td class="num">{esc(agent.duration_label)}</td>
  <td class="num">{esc(agent.tools.total)}</td>
  <td class="dim">{esc(agent.tools.summary())}</td>
  <td class="num">{esc(agent.output_label)}{cache}</td>
  <td class="num">{esc(len(agent.files_touched))}</td>
  <td class="mono dim">{esc(_stamp(agent.started, "%m-%d %H:%M"))}</td>
</tr>{drill_row}
"""


def _roster(snapshot: Snapshot, include_text: bool) -> str:
    if not snapshot.agents:
        return """
<section class="section">
  <h2>Agent roster <span class="ko">인사</span></h2>
  <div class="empty">No subagents have been dispatched in any session on this machine.<br>
  This table fills in when work is delegated to an agent.</div>
</section>
"""

    rows = "".join(_agent_row(a, include_text) for a in snapshot.agents)
    note = (
        "Elapsed is wall-clock from dispatch to final record — an agent resumed later "
        "shows the full span, not time spent working. Output is measured text emitted in "
        "characters, not the reported token count, which is a placeholder in subagent "
        "transcripts; context reuse is listed beneath it."
    )
    return f"""
<section class="section">
  <h2>Agent roster <span class="ko">인사</span></h2>
  <div class="scroll">
    <table>
      <thead><tr>
        <th>Agent</th><th>Type</th><th>Status</th><th>Elapsed</th><th>Tools</th>
        <th>Breakdown</th><th>Output</th><th>Files</th><th>Dispatched</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <p class="src" style="margin-top:8px">{esc(note)}</p>
</section>
"""


def _sessions(snapshot: Snapshot) -> str:
    if not snapshot.sessions:
        return ""
    rows = "".join(
        f"""<tr>
  <td><strong>{esc(s.title)}</strong><div class="dim mono">{esc(s.session_id[:12])}</div></td>
  <td class="mono dim">{esc(s.branch or "—")}</td>
  <td class="num">{esc(s.duration_label)}</td>
  <td class="num">{esc(s.user_turns)}</td>
  <td class="num">{esc(s.decisions)}</td>
  <td class="num">{esc(s.tools.total)}</td>
  <td class="num">{s.total_tokens:,}</td>
  <td class="num dim">{s.cache_tokens:,}</td>
  <td class="num">{esc(len(s.files_touched))}</td>
  <td class="mono dim">{esc(", ".join(m.replace("claude-", "") for m in s.models) or "—")}</td>
</tr>"""
        for s in snapshot.sessions
    )
    return f"""
<section class="section">
  <h2>Work streams <span class="ko">업무</span></h2>
  <div class="scroll">
    <table>
      <thead><tr>
        <th>Session</th><th>Branch</th><th>Elapsed</th><th>Turns</th><th>Decisions</th>
        <th>Tools</th><th>Tokens</th><th>Cached</th><th>Files</th><th>Models</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""


def _projects(snapshot: Snapshot) -> str:
    if not snapshot.projects:
        return """
<section class="section">
  <h2>Holdings <span class="ko">보유 자산</span></h2>
  <div class="empty">No git repositories found in the workspace.</div>
</section>
"""
    rows = "".join(
        f"""<tr>
  <td><strong>{esc(p.name)}</strong><div class="dim mono">{esc(p.path)}</div></td>
  <td class="mono">{esc(p.language or "—")}</td>
  <td class="num">{p.loc:,}</td>
  <td class="num">{esc(p.source_files)}</td>
  <td class="num">{esc(p.test_count)}</td>
  <td class="mono dim">{esc(p.branch or "—")}</td>
  <td>{'<span class="pill degraded">UNCOMMITTED</span>' if p.dirty else '<span class="pill active">CLEAN</span>'}</td>
  <td class="mono dim">{esc(_stamp(p.last_commit))}</td>
</tr>"""
        for p in snapshot.projects
    )
    return f"""
<section class="section">
  <h2>Holdings <span class="ko">보유 자산</span></h2>
  <div class="scroll">
    <table>
      <thead><tr>
        <th>Project</th><th>Language</th><th>Lines</th><th>Files</th><th>Tests found</th>
        <th>Branch</th><th>Tree</th><th>Last commit</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <p class="src" style="margin-top:8px">Tests found counts test functions by text scan — not a run result.</p>
</section>
"""


def _output(snapshot: Snapshot) -> str:
    if not snapshot.files and not snapshot.commits:
        return ""

    top = sorted(snapshot.files, key=lambda f: (-f.touches, f.path))[:14]
    file_rows = "".join(
        f"""<tr>
  <td class="mono">{esc(f.path)}</td>
  <td class="mono dim">{esc(f.project or "—")}</td>
  <td class="num">{esc(f.writes)}</td>
  <td class="num">{esc(f.edits)}</td>
  <td class="mono dim">{esc(_stamp(f.last_seen, "%m-%d %H:%M"))}</td>
</tr>"""
        for f in top
    )

    commit_rows = "".join(
        f"""<tr>
  <td class="mono">{esc(c.short_sha)}</td>
  <td>{esc(c.subject[:88])}</td>
  <td class="mono dim">{esc(c.author)}</td>
  <td class="num">{esc(c.files_changed)}</td>
  <td class="num pos">+{c.insertions:,}</td>
  <td class="num neg">-{c.deletions:,}</td>
  <td class="mono dim">{esc(_stamp(c.timestamp))}</td>
</tr>"""
        for c in snapshot.commits[:14]
    )

    files_block = (
        f"""<h2>Work product <span class="ko">산출물</span></h2>
<div class="scroll"><table>
  <thead><tr><th>Path</th><th>Project</th><th>Created</th><th>Revised</th><th>Last touched</th></tr></thead>
  <tbody>{file_rows}</tbody>
</table></div>
<p class="src" style="margin-top:8px">Derived from Write/Edit tool inputs — intent to change, not a filesystem diff.</p>"""
        if file_rows
        else ""
    )

    commits_block = (
        f"""<h2 style="margin-top:32px">Shipped <span class="ko">출고</span></h2>
<div class="scroll"><table>
  <thead><tr><th>SHA</th><th>Subject</th><th>Author</th><th>Files</th><th>+</th><th>-</th><th>When</th></tr></thead>
  <tbody>{commit_rows}</tbody>
</table></div>"""
        if commit_rows
        else ""
    )

    return f'<section class="section">{files_block}{commits_block}</section>'


def _timeline(snapshot: Snapshot) -> str:
    events: list[tuple[datetime, str]] = []
    for agent in snapshot.agents:
        if agent.started:
            events.append(
                (aware(agent.started), f"Dispatched <strong>{esc(agent.title)}</strong> "
                                       f'<span class="dim">({esc(agent.agent_type or "agent")})</span>')
            )
    for commit in snapshot.commits:
        if commit.timestamp:
            events.append(
                (aware(commit.timestamp),
                 f'Committed <span class="mono">{esc(commit.short_sha)}</span> {esc(commit.subject[:70])}')
            )
    if not events:
        return ""

    events.sort(key=lambda e: e[0], reverse=True)
    items = "".join(
        f'<div class="event"><span class="t">{esc(_stamp(when, "%m-%d %H:%M"))}</span>{text}</div>'
        for when, text in events[:20]
    )
    return f"""
<section class="section">
  <h2>Activity <span class="ko">활동 기록</span></h2>
  <div class="timeline">{items}</div>
</section>
"""


def _footer(snapshot: Snapshot) -> str:
    rows = "".join(
        f"<tr><td>{esc(s.label)}</td>"
        f'<td class="mono">{esc(s.path)}</td>'
        f"<td>{'read' if s.exists else 'not present'}</td>"
        f"<td>{esc(s.age_label(now()) if s.exists else '—')}</td></tr>"
        for s in snapshot.sources
    )
    return f"""
<footer>
  <table><tbody>{rows}</tbody></table>
  <p style="margin-top:12px">
    Ajax HQ is read-only. It dispatches nothing, runs nothing, and changes no configuration.
    Every figure above was measured from local records; nothing is simulated.
  </p>
</footer>
"""


# --------------------------------------------------------------------- page


def render(snapshot: Snapshot, *, include_text: bool = True, live: bool = False,
           refresh_seconds: int = 20) -> str:
    """Build the complete page.

    ``include_text`` controls whether agent prompts and reports are embedded.
    ``live`` adds the poll-and-reload script, used only by the local server.
    """
    script = f"<script>{refresh_script(refresh_seconds)}</script>" if live else ""
    generated = snapshot.generated_at.isoformat(timespec="seconds")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Ajax HQ — Operations Centre</title>
<style>{CSS}</style>
</head>
<body data-generated="{esc(generated)}">
<div class="wrap">
{_masthead(snapshot)}
{_executive_strip(snapshot)}
{_divisions(snapshot)}
{_roster(snapshot, include_text)}
{_sessions(snapshot)}
{_output(snapshot)}
{_projects(snapshot)}
{_timeline(snapshot)}
{_footer(snapshot)}
</div>
{script}
</body>
</html>
"""
