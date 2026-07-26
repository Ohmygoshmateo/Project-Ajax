# Bay Four

A scripted faceless series, and the pipeline that builds it.

One episode is one night shift in a hospital emergency department, narrated by a
nurse keeping a voice diary. Episodic on the surface, serialised underneath: the
first episode contains a decision she does not report, and the twelve-episode
season is the cost of it.

Everything in it is invented, and says so.

```bash
pip install -e studio

ajax-studio validate            # check every script against the bible
ajax-studio plan --episode 1    # the cue schedule, and where the runtime lands
ajax-studio previz --episode 1  # render a watchable animatic to studio/out/
ajax-studio shorts --episode 1  # a vertical excerpt for cross-platform
ajax-studio metadata --episode 1  # titles, description, tags, disclosures
```

- **[series/BIBLE.md](series/BIBLE.md)** — the persona, the episode shape, the
  season arc, and the boundaries. Read this before touching a script.
- **[docs/LAUNCH.md](docs/LAUNCH.md)** — what has to happen outside this
  repository, with the real costs and the gates.

---

## Why a pipeline at all

The writing is the product. Everything here exists to make a good episode cheaper
to produce, and specifically to let the pacing be judged **before** any money is
spent on a voice or on visuals.

`ajax-studio previz` renders the episode with real timing, real captions and
placeholder cards. It is watchable. You can see where the middle sags, rewrite
it, and render again for nothing. Once a real voice track exists for a beat, its
measured duration replaces the estimated one automatically, so the finished cut
is timed from the recording rather than from arithmetic.

Every placeholder frame is marked as a placeholder, so a previz cut can never be
mistaken for a finished one.

## The episode format

A script is plain YAML — the writing has to be editable by someone who does not
write Python. Each beat carries an act, an in-world clock label, the narration,
a shot description, and the writer's own tension reading:

```yaml
- id: crisis-01
  act: crisis
  clock: "02:14"
  tension: 5
  vo: >
    And now we're back at the top. Bay four. Me in the room, chart in my hand,
    eleven seconds where I'm the only one who knows what's already gone in.
  shot:
    description: Corridor at speed again, same shot as the cold open, held longer.
    motion: push
  caption: "02:14"
```

`shot.asset` is empty until a real image exists; the renderer draws a card from
the description until then, and switches to the asset the moment one appears. The
pipeline keeps working as assets arrive one at a time, which is how they actually
arrive.

## What `validate` enforces

Structure, from the bible: five acts in order, the cold open inside its ceiling,
the hook landing inside fifteen seconds, tension peaking in the crisis, an
unresolved ending. Runtime outside the 8–12 minute target is a **warning** — a
short episode is a note to the writer, not a broken build.

Compliance, also from the bible, as **errors**: no drug doses, no protocol a
viewer could follow, no second-person medical instruction, nothing graphic. A
drama about competence must not become a how-to, and the check is a linter rather
than a memo because a memo does not fail a build.

Each compliance rule is tested against a near miss that must *not* fire. A rule
that cries wolf gets switched off, which is worse than not having it.

## Honesty rules

The same ones as the rest of this repository, adapted to a thing that is
deliberately fiction.

1. **The fiction is disclosed, everywhere.** On screen, in the first line of
   every description, and in the pinned comment. A synthetic voice narrating a
   realistic person is exactly what YouTube's altered-content disclosure exists
   for, and `metadata` emits that answer as a field rather than leaving it to
   memory.
2. **No estimate is presented as a measurement.** A duration derived from word
   count is labelled derived; a duration read from real audio is labelled
   measured. They are different artefacts and the pipeline never blurs them.
3. **No fake implementations.** `voice.py` ships a silence backend for previz and
   an interface for a real provider. Nothing here pretends to speak.
4. **Volume is not the strategy.** The pipeline builds an episode when asked. It
   does not generate content unattended — that is the behaviour YouTube's
   inauthentic-content policy targets, and the only durable defence is that the
   writing is genuinely authored.
5. **Nothing is published from here.** No keys, no upload path. The last human
   check before something goes public is the point.

## Testing

```bash
cd studio && pytest -q
```

Network-free. The render tests produce real MP4 files from synthetic three-beat
episodes and assert the output is a playable file of the expected duration —
a pipeline that passes its unit tests but produces an unplayable video is not
tested.
