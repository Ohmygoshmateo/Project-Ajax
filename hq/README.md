# Ajax HQ

A read-only operations centre for tracking agents and what they build, styled as
a Korean conglomerate's headquarters.

Every figure it shows was measured from local records. Nothing is simulated,
no panel invents state to look busy, and where something *is* decoration — the
floor animation — it is labelled as decoration.

```bash
pip install -e hq
ajax-hq brief      # what the agents did since yesterday
ajax-hq agents     # every agent that has run, in the terminal
ajax-hq lineage    # reporting lines — who dispatched whom
ajax-hq trends     # how the figures have moved across snapshots
ajax-hq floor      # the virtual office floor, one desk per agent
ajax-hq play       # the floor as a live game — agents roam until work arrives
ajax-hq play --web # the same simulation, on a canvas at 127.0.0.1:8788
ajax-hq status     # division summary
ajax-hq build      # writes hq/out/index.html
ajax-hq serve      # live view on http://127.0.0.1:8787
ajax-hq snapshot   # archive this history into the repo
```

---

## Where the data comes from

Claude Code writes complete transcripts to disk, and HQ reads them:

| Source | Yields |
| --- | --- |
| `~/.claude/projects/<project>/<session>.jsonl` | Tool calls, timestamps, git branch, model, tokens |
| `~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl` | Each subagent's own full transcript |
| `~/.claude/sessions/*.json` | Session name, cwd, start time, client version |
| `~/.claude/plans/*.md` | Planned work |
| `git log` / `git status` | Commits, churn, branches, uncommitted state |
| Workspace walk | Projects, language, LOC, test counts |
| a project module under `sources/modules/` | figures a project supplies itself |

**These are undocumented internals.** The layout above was observed on client
2.1.220 and can change without notice. Parsing is deliberately paranoid: every
line is guarded, every field optional, and anything unreadable is *counted and
skipped*. A format change costs you panels, never the page — and the masthead
shows a schema-drift banner with the detected version and the unparsed count, so
drift is visible rather than silent.

---

## The agent roster

The centrepiece, and entirely real. For every subagent ever dispatched:

task description, type, dispatch time, wall-clock elapsed, status, tool calls
broken down by tool, output size, files touched — and an expandable personnel
file containing the full prompt it was given and the report it returned.

Two columns need reading carefully:

- **Elapsed** is wall-clock from dispatch to final record. An agent resumed hours
  later shows the whole span, not time spent working.
- **Output** is characters of text actually emitted, measured from the content
  itself. It is *not* the reported output-token count: that field is a
  placeholder in every subagent transcript observed so far, reporting 28 tokens
  for a 46,861-character response. Sessions, whose counts are sound, still show
  real tokens — with cached context listed separately, because on a long session
  it exceeds fresh usage by two orders of magnitude and would read as
  consumption rather than reuse.

---

## Divisions

The chaebol structure is the organising metaphor; the figures beneath it are
derived from actual tool usage.

| Code | Division | Derived from |
| --- | --- | --- |
| `EXO` | Executive Office · 비서실 | Sessions, plans, decision points |
| `RND` | Research & Development · 연구개발부 | Explore / Plan / research subagents |
| `ENG` | Engineering · 엔지니어링부 | Write and Edit calls → files built |
| `QA` | Quality Assurance · 품질관리부 | Test and lint invocations |
| `OPS` | Operations · 운영부 | Commits, branches, repository state |
| `AST` | Asset Management · 자산운용부 | An installed project module, if any |

Status is `NEVER ACTIVE` / `ACTIVE` / `IDLE` / `DEGRADED`, derived from real
timestamps. A division with no history says so instead of showing zeros.

`divisions.py` is a registry — adding a division is appending one builder. There
are deliberately no stubs for projects that do not exist yet.

---

## Persistence

This container is ephemeral. When it is reclaimed, `~/.claude` goes with it and
every transcript is lost.

`ajax-hq snapshot` writes a compact JSON summary into `hq/data/history/`, which
is committed, so the record accumulates across containers. On load, history is
merged beneath live data — anything currently on disk wins, and restored entries
are badged as archival.

**Snapshots contain no prompt or response text.** Drill-down text exists only in
the locally-generated page. Prompts can contain anything, and these files go into
git where removing something later is much harder than never writing it. That
guarantee lives in one function (`snapshot.to_payload`) and is enforced by tests
that assert known prompt fragments never appear in the output — including a check
that the fixture really carried the secret in the first place, so the test cannot
pass for the wrong reason.

---

## The floor as a game

`ajax-hq play` runs the six wings as a live office. Each agent has a desk, drifts
around its own wing when nothing is happening, and walks somewhere when work
actually arrives. `--web` renders the same simulation on a canvas at
`127.0.0.1:8788`; both views are driven by one engine, so they never disagree.

What moves an actor is a real record. The tailer follows `~/.claude` as it is
written, and each tool call sends its agent to the wing that tool implies — the
same classification that seats agents on the static floor, so an errand and a
desk always agree:

| Record | Where the actor goes |
| --- | --- |
| `Write` / `Edit` | Engineering |
| `Read` / `Grep` / `WebSearch` | Research & Development |
| `Bash` running `pytest`, `ruff`, `mypy` | Quality Assurance |
| `Bash` running `git commit` / `push` | Operations |
| `Agent` / `AskUserQuestion` | Executive Office |
| `Bash` running `ls`, `git status` | nowhere — no evidence, no movement |

**Where the honesty line falls.** Who is on the floor is real: every actor is an
agent or session found in a transcript, and an id that appears mid-run means an
agent was just dispatched, so it walks in. Whether an actor is busy is real, and
so is every move between wings. The drifting *inside* a wing is decoration — a
motionless grid reads as broken, and nothing on disk records where anyone
stands. The HUD says exactly that, and a test asserts an idle actor never leaves
its own wing, because crossing the floor would be a claim only an event may
make.

On a machine where no agent is currently running, live mode is a still office.
`ajax-hq play --replay` plays back the events already on disk instead, labelled
`REPLAY` in the header and stated in the footer — history, not live activity.

---

## What HQ will not do

It is a dashboard, not a control panel. It dispatches nothing, runs nothing, and
changes no configuration. Its only writes are its own output and snapshot files.

The server binds `127.0.0.1` and nothing else — not configurable, asserted in
tests. The page renders full agent prompts, so exposing it on a routable
interface would publish everything those agents were ever asked to do.

---

## Testing

```bash
cd hq && pytest -q
```

352 tests, network-free. Weighted toward the things that would be quietly wrong
otherwise: transcript parsing against malformed, truncated, and unknown-type
records; agent extraction and linkage; division status derivation at the window
boundary; snapshot privacy; HTML self-containment (no external resource refs);
escaping of transcript text into the page; empty-state rendering; and the
server's bind address.

The game adds its own: every desk reachable from every other desk, a torn final
line retried rather than dropped, no record delivered twice, no actor without a
record behind it, and — the one that keeps the whole thing honest — an idle
actor never leaving its wing without an event.
