# Ajax HQ

An operations centre for the AI agents working for you — a roster of who ran,
what they built, and a virtual office floor you can watch them work in.

Styled as a Korean conglomerate's headquarters. Every figure it shows was
measured from records on your own machine. Nothing is simulated, no panel
invents state to look busy, and where something *is* decoration the page says so.

```bash
pip install -e hq

ajax-hq play       # the office as a live game — agents roam until work arrives
ajax-hq floor      # a static floor plan, one desk per agent
ajax-hq agents     # the roster, as a table
ajax-hq status     # division summary
ajax-hq serve      # full dashboard at http://127.0.0.1:8787
ajax-hq snapshot   # archive this history into the repo
```

Full documentation — data sources, division derivation, and the honesty rules —
is in [hq/README.md](hq/README.md).

---

## What it reads

Claude Code writes complete transcripts to disk, and HQ reads them: every tool
call with timestamps, each subagent's own transcript, the session registry, the
plans directory, `git log`, and a walk of your workspace. From those it derives
six divisions, a personnel roster, and a floor plan — all real, all local.

These are undocumented internals and they change between client versions, so
parsing is deliberately paranoid: every field optional, every unreadable line
counted and skipped, and a schema-drift banner reporting what it could not read.
A format change costs you panels, never the page.

## The floor

`ajax-hq play` runs the six wings as a pixel-art office. Each agent has a desk
and a character, drifts around its own wing when nothing is happening, and walks
somewhere when work actually arrives — an `Edit` sends it to Engineering,
`pytest` to Quality Assurance, `git push` to Operations. An activity feed lists
the records driving it, and every line of that feed exists on disk.

The animation is decoration and is labelled as such. What is real: who is on the
floor, whether they are busy, and every walk between wings.

## Requirements

Python 3.11 or newer. Two dependencies, `typer` and `rich`. No network access,
no external services, no accounts.

## Getting started

```bash
git clone https://github.com/Ohmygoshmateo/Project-Ajax.git
cd Project-Ajax
python3 -m venv .venv
source .venv/bin/activate
pip install -e hq
ajax-hq agents
```

Every later session needs the last two lines again — `ajax-hq` lives inside the
virtual environment.

---

## History

This repository previously held a quant options trading system. That project was
retired and its code removed from the working tree; it remains in git history and
in pull requests #1 and #3 if it is ever wanted back.
# Project-Ajax
