# Roadmap

What Ajax HQ is being built toward, in priority order. Work is picked from the
top of this list. Anything shipped moves to [CHANGELOG.md](CHANGELOG.md) and is
deleted from here — this file describes the future only, so that it cannot
quietly become a second, flattering record of the past.

An item earns a place here by being **derivable from real records**. If HQ
cannot measure it from transcripts, git, or the filesystem, it does not belong
on this list however good it would look on a dashboard.

---

## In flight

- **Watch mode** — `ajax-hq agents --watch` and a live-refreshing roster, so a
  running session can be followed without re-running commands.
- **Cross-project view** — HQ currently walks one workspace root. Multiple roots,
  with per-project division figures.
- **Per-agent file attribution** — every agent currently places in R&D or QA
  because subagents write through a shared tree and their `Write`/`Edit` calls
  are not being credited to them individually. Engineering has never had staff.
  Either the attribution is recoverable from the subagent transcripts or it is
  not; find out, and if it is not, say so on the floor rather than leaving the
  wing quietly empty.

## Next

- **Session detail** — a single session's full arc: turns, decisions, agents
  dispatched, files touched, in one view.
- **Cost accounting** — token spend per session and per model over time, from the
  fields that are actually sound. Agent-level output tokens stay excluded until
  the vendor field becomes trustworthy.
- **Search** — across transcripts, locally. Never leaves the machine.
- **Snapshot on a schedule** — `trends` is only as good as the archive, and the
  archive currently grows when someone remembers to run `ajax-hq snapshot`. That
  irregularity is disclosed in the output, but regular captures would make the
  series worth more.

## Later

- **Meeting rooms on the floor** — an `AskUserQuestion` is a real conversation
  between the principal and the user; the floor could show it as one.
- **Day/night on the floor** — lighting from the actual clock of the records
  being replayed, so a 3am session looks like a 3am session.
- **A wing per project** — the six divisions are functional. Once several
  projects are tracked, a spatial split by project may read better.
- **Plugin API for project modules** — the Asset Management slot proved the
  shape; make it a documented interface rather than a convention.

## Explicitly not planned

- **Dispatching agents from HQ.** It is a dashboard, not a control panel. It
  reads; it does not act.
- **A hosted or shared deployment.** The page renders full agent prompts. It
  stays on loopback.
- **Simulated activity of any kind.** The floor animates, and says plainly that
  the animation is decoration. No panel will ever show invented work.
- **Estimated or interpolated figures.** A gap in the data is reported as a gap.
