# How this repository gets worked on

Two live projects share this repository — **Ajax HQ** (`hq/`) and **Bay Four**
(`studio/`) — and both are worked on daily, mostly by AI agents, with the owner
checking in once a day rather than steering every step. This document is the
plan for doing that efficiently. It exists because the first two days of
parallel-agent work cost more than they needed to, and the fix is a set of
defaults, not a one-time cleanup.

---

## What went wrong, concretely

On 2026-07-26, four agents were dispatched in parallel to build three HQ
commands and start Bay Four. All four were useful and all four shipped. But:

- **Two of the four were killed mid-task** by a monthly API spend limit,
  leaving unfinished test files that had to be completed by hand.
- **Parallel dispatch was chosen by default**, not because the tasks were
  interdependent or urgent — sequential would have cost less and finished
  everything, just later in the day.
- **CI was added but only tested one of the two packages** for a full day,
  because nobody re-checked the workflow against the newer project it was
  supposed to also cover.

None of these were correctness failures — every shipped module passed its
tests and matched the house style. They were **spend and attention** failures:
work that cost more compute or produced a false sense of coverage than the
task required.

## The default going forward: one agent, working the roadmap

**Dispatch a single agent per session**, and have it work the top item of
`hq/ROADMAP.md` or `studio/ROADMAP.md` to completion — module, tests,
real-data verification, changelog entry — before picking up the next one.

Parallel dispatch is the exception, reserved for tasks that are:

1. **Genuinely independent** — no shared files, no shared module the parent
   has to wire together afterward, no risk of two agents' edits colliding.
2. **Roughly equal in size** — four agents where one takes 4 minutes and
   another takes 13 is four agents' worth of context and coordination for
   what a single sequential run would have done with less overhead.
3. **Worth the wall-clock savings** — if nobody is waiting on the result
   today, sequential is strictly cheaper for the same outcome.

When parallel dispatch is used anyway, the parent session should still hold
back from wiring shared integration points (a CLI, a shared config file) until
every agent reports — that discipline was already followed on 2026-07-26 and
is worth keeping.

## Spend awareness

An agent that gets killed mid-task by a spend limit is the worst outcome
available: partial, uncommitted work, and a dangling test file that someone
else has to notice is missing. Two mitigations:

- **Fewer, longer-lived agents** rather than many short ones — the default
  above already does this.
- **If a limit is hit, say so plainly and stop dispatching**, rather than
  quietly completing the gap and moving on as though nothing happened. The
  2026-07-26 report to the user did disclose this; keep doing that rather than
  smoothing it over.

## The daily routine

The single biggest lever for "live and ongoing" is a scheduled routine that
wakes a session once a day, reads the roadmap, does one thing well, and
reports — without the owner having to ask. That routine has been proposed and
requires an explicit approval this session has not yet received. Until it is
approved, progress on both projects is gated on the owner prompting it, which
is a materially different thing than "ongoing."

The call, ready to fire the moment it is approved:

```
create_trigger(
  name="Ajax HQ / Bay Four — daily build",
  cron_expression="0 14 * * *",   # or whatever hour the owner prefers
  create_new_session_on_fire=True,
  notifications={"push": True},
  prompt=<one-agent, top-of-roadmap instructions, per-project>
)
```

## Git identity

Commits made by this session already carry the correct identity
(`user.email noreply@anthropic.com`, `user.name Claude`) and show as verified.
The two commits the stop hook flagged as unverified (`4518e69`, `8b93498`) are
**GitHub's own merge commits**, created when the owner clicked "Merge pull
request" in the GitHub UI — GitHub sets the committer to itself for those,
and that is normal, not a defect. Rewriting already-merged history on `main`
to "fix" it would be a destructive, force-pushed change to public history for
a cosmetic label, and is deliberately not done here. Nothing needs fixing.

## What this document is not

Not a roadmap — the roadmap is `hq/ROADMAP.md` and `studio/ROADMAP.md`, and
work is picked from there, not from here. This document governs *how* work
happens; those govern *what*.
