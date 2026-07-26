# Changelog

What actually shipped, newest first. One entry per working day.

This file is the daily progress record: if something is not listed here, it did
not ship, however far along it might be. Entries name what changed and what it
cost — a change that traded something away says so.

---

## 2026-07-26

**Live activity feed and per-activity animation.** Each character on the floor
now carries an activity derived from the record that produced it — editing,
researching, running checks, shipping, in conversation, writing up — shown in
its pose and in a matching bubble icon. Both renderers gained a feed of the last
fourteen records with timestamps. The activity expires with the record that
justified it: when nothing recent remains the actor goes idle rather than being
handed something plausible to look busy with.

**The repository became Ajax HQ.** The quant options trading system was retired
and removed from the working tree (74 files). It remains in git history and in
PRs #1 and #3. HQ's Asset Management division, which had been derived from the
trading database, is now a generic project-module slot that states it is empty
rather than rendering zeros.

**CI.** Every push and pull request now runs the suite and the linter. The
project is worked on daily and largely by agents, so the tests have to be what
says a change is sound.

## 2026-07-25

**The floor became a game.** `ajax-hq play` runs the six wings as a live office:
walls, doors, a corridor spine, one desk per agent. Two renderers over one
engine — a Rich loop in the terminal and a pixel-art canvas on `127.0.0.1:8788`.
Movement is driven by transcripts, not by a timer: each tool call sends its
agent to the wing that tool implies. A `--replay` mode plays recorded history,
labelled as history.

**Pixel characters.** Sprites with hair, skin and shirt palettes seeded from the
agent id, four-phase walk cycles, per-direction facing, a seated pose, an idle
breathing bob. Drawn from rectangles rather than a sprite sheet, so the page
stays self-contained.

**Agents are seated by behaviour, not by declared type.** An agent's
`subagent_type` records how it was dispatched, not what it did. Wings are now
scored from the tool record, weighted 3:1 toward producing over reading. Shell
commands are classified at parse time into counts, because the commands
themselves are never archived.

**Measured output replaced reported tokens.** `output_tokens` is unusable in
every subagent transcript observed — one reported 28 tokens for a
46,861-character response. The roster now shows characters actually emitted.

## 2026-07-24

**Ajax HQ shipped.** Roster, six divisions, project cards, snapshot history, an
HTML dashboard, and a loopback server. Everything derived from Claude Code's
local transcripts, git history, and a workspace walk.
