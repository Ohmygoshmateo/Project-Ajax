"""Where an agent sits, derived from what it actually did.

Placement could have been read off the declared subagent type, but that type
records how an agent was *dispatched*, not what it turned out to do. A
"general-purpose" agent that spent its run writing files belongs in Engineering;
one that spent it searching belongs in R&D. The tool record is the stronger
signal, so it is the one used.

This lives apart from :mod:`ajax_hq.floor` because the transcript parser needs
the command classifier too — shell commands are never archived (they can carry
credentials as arguments), so the *counts* derived from them have to be computed
while the commands are still in hand.
"""

from __future__ import annotations

from ajax_hq.model import Agent

BUILD_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}
RESEARCH_TOOLS = {
    "WebSearch", "WebFetch", "Read", "Grep", "Glob", "ToolSearch", "NotebookRead",
}

# Shell commands that ship something, as opposed to merely inspecting. Read-only
# git (log, status, diff) is investigation and scores as research — an agent
# running `git log` to understand a repo is not doing release engineering.
SHIP_PATTERNS = ("git commit", "git push", "git merge", "git tag", "git rebase")
VERIFY_PATTERNS = ("pytest", "ruff", "mypy", "npm test", "cargo test", "go test",
                   "eslint", "tox", "unittest")

# One write, test run, or commit outweighs three reads. A read is a step towards
# almost any kind of work, so it is weak evidence on its own; producing a file,
# a verification result, or a commit *is* the work. The 3:1 ratio is enough for
# an agent that edits ten files while reading twenty to place in Engineering,
# and not so steep that a single incidental write drags a research agent out of
# R&D.
PRODUCING_WEIGHT = 3
RESEARCH_WEIGHT = 1

# Tie-break order. R&D is last so it acts as the fallback: an agent with no
# signal at all still gets a desk rather than being dropped.
WING_PRECEDENCE = ("ENG", "QA", "OPS", "RND")


def count_commands(commands: list[str]) -> tuple[int, int]:
    """Split shell commands into (verification runs, shipping actions).

    Counted at parse time and stored as integers, because the commands
    themselves must never reach a committed snapshot.
    """
    verify = ship = 0
    for command in commands:
        lowered = command.lower()
        if any(pattern in lowered for pattern in VERIFY_PATTERNS):
            verify += 1
        if any(pattern in lowered for pattern in SHIP_PATTERNS):
            ship += 1
    return verify, ship


def wing_scores(agent: Agent) -> dict[str, int]:
    """Weighted evidence for each wing. All zeros is a valid answer."""
    counts = agent.tools.counts
    build = sum(count for name, count in counts.items() if name in BUILD_TOOLS)
    research = sum(count for name, count in counts.items() if name in RESEARCH_TOOLS)
    return {
        "ENG": build * PRODUCING_WEIGHT,
        "QA": agent.verify_runs * PRODUCING_WEIGHT,
        "OPS": agent.ship_actions * PRODUCING_WEIGHT,
        "RND": research * RESEARCH_WEIGHT,
    }


def wing_for(agent: Agent) -> str:
    """The wing an agent belongs in.

    An agent with no recognisable signal lands in R&D rather than being dropped:
    losing a real agent to a classification gap would be a worse error than a
    rough placement, and the desk displays its actual type and tool count
    regardless.

    One caveat applies to agents restored from a committed snapshot. Tool counts
    survive archiving, so Engineering and R&D placement is unaffected, but
    ``verify_runs`` and ``ship_actions`` are only as good as the snapshot that
    carried them — an older snapshot written before those fields existed reports
    zero, and such an agent places on its tool record alone.
    """
    scores = wing_scores(agent)
    best = max(scores.values())
    if best == 0:
        return "RND"
    return next(code for code in WING_PRECEDENCE if scores[code] == best)
