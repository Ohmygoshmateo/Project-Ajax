# Project-Ajax

Two live projects, built and maintained mostly by AI agents, tracked daily.

| Project | What it is | Start here |
| --- | --- | --- |
| **[Ajax HQ](hq/)** | An operations centre for the agents working on this repository — a roster, six divisions, and a virtual office floor you can watch them work in. | [hq/README.md](hq/README.md) |
| **[Bay Four](studio/)** | A scripted faceless YouTube series and the pipeline that builds it — episode format, a compliance validator, and a previz renderer. | [studio/README.md](studio/README.md) |

Both are real, both are ongoing, and neither invents state to look busy — see
each project's own honesty rules for what that means concretely.

**[docs/OPERATING_MODEL.md](docs/OPERATING_MODEL.md)** describes how work on
this repository actually happens day to day: when agents are dispatched in
parallel versus one at a time, and the daily routine that keeps both projects
moving without the owner having to ask.

---

## Getting started

```bash
git clone https://github.com/Ohmygoshmateo/Project-Ajax.git
cd Project-Ajax
python3 -m venv .venv
source .venv/bin/activate
pip install -e hq -e studio
```

Every later session needs the venv activated again — `ajax-hq` and
`ajax-studio` live inside it, not on the system path.

```bash
ajax-hq brief        # what the agents did since yesterday
ajax-hq play         # the office as a live pixel-art game

ajax-studio validate         # every episode script against the series bible
ajax-studio previz --episode 1   # a watchable animatic, no paid services required
```

## Requirements

Python 3.11 or newer. No network access, no external services, no accounts —
`hq` depends on `typer` and `rich`; `studio` adds `PyYAML`, `Pillow`, and a
self-contained `ffmpeg` binary via `imageio-ffmpeg`.

## Continuous integration

Every push and pull request runs both packages' test suites, both linters, and
validates every Bay Four script against the series bible — a compliance
failure in a script is a publishing risk, not a code defect, and no unit test
would catch it. See [.github/workflows/ci.yml](.github/workflows/ci.yml).

---

## History

This repository previously held a quant options trading system. That project
was retired and its code removed from the working tree; it remains in git
history and in pull requests #1 and #3 if it is ever wanted back.
