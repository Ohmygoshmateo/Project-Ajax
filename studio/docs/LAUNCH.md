# Launch plan

What has to happen outside this repository, in order, with the costs and the
gates stated. Nothing here is automated, because none of it should be — every
step is a decision.

---

## The honest position first

**This channel is not a monetisation plan with a short payback.** The YouTube
Partner Programme threshold is 1,000 subscribers plus 4,000 public watch hours in
twelve months (or 10M Shorts views in 90 days). At eight to twelve minutes an
episode, 4,000 hours is roughly 25,000–30,000 full views. A new faceless channel
with no audience typically takes months to reach that, and most never do.

**The policy risk is real and specific.** YouTube's monetisation rules were
updated in July 2025 to target "inauthentic content" — mass-produced, repetitive
material with no meaningful human input. Faceless AI channels are the named
concern. What keeps *this* one on the right side of it is the thing that is
hardest to fake: a genuinely authored, serialised story with a continuous
character arc. That is also why the pipeline in this repository is built to make
**good** episodes cheaper rather than to make **many** episodes automatic. If the
strategy ever becomes volume, the channel is in the category the policy exists to
demote.

**Synthetic narration must be disclosed.** The voice is AI and sounds like a real
person, so YouTube's altered-content disclosure applies on every upload.
`ajax-studio metadata` emits that answer as a field so it is not left to memory.

---

## Phase 0 — before any account exists

Nothing here costs money.

1. **Read the scripts aloud.** All of episode 1, out loud, timed. If it is boring
   in your own voice it will be boring in a synthetic one, and no production
   spend fixes that.
2. **Build the previz cut.** `ajax-studio previz --episode 1` produces a watchable
   animatic with real timing and placeholder visuals. Watch the whole thing. Note
   every place your attention drifts; those are rewrites, and they are free now
   and expensive later.
3. **Rewrite.** Then previz again.

Gate: you would watch episode 1 to the end if a stranger had made it.

## Phase 1 — the pilot, paid

Estimated one-off cost for three episodes: **roughly $50–150** depending on the
voice and visual choices below.

1. **Voice.** A synthetic narration subscription — ElevenLabs' entry tier is the
   usual choice, around $5–22/month. Pick one voice and never change it; the
   voice *is* the channel. Test the same paragraph across five voices before
   committing, and choose the flattest one that stays listenable, not the most
   dramatic.
2. **Visuals.** Two viable routes:
   - **Stock + treatment** (cheaper, safer): licensed clips of corridors, rain,
     dashboards, hands, run through a consistent colour grade. Bland shots plus a
     consistent grade reads as authored; varied shots without one reads as
     scraped.
   - **Generated stills with motion** (more distinctive, more risk): image
     generation plus slow pushes. Watch consistency — the same corridor must look
     like the same corridor across twelve episodes.
3. **Wire the real voice backend.** `voice.py` defines the interface; implement
   one class against your chosen provider. Real audio durations then override the
   estimated ones automatically — the timing of the finished cut comes from the
   recording, not from arithmetic.
4. **Cut episodes 1–3 properly.** Do not publish yet.

Gate: three finished episodes in hand, consistent enough that they are obviously
the same series.

## Phase 2 — publish

1. **Create the channel.** Name, banner, an about page that states the series is
   fiction in its first line.
2. **Publish 1–3 on the same day**, then weekly. A serialised story needs enough
   material for a new viewer to binge into the habit; a single episode has
   nothing to convert with.
3. **Pin a comment** on every episode restating that it is fiction. The comment is
   the disclosure people actually read.
4. **Shorts as the discovery engine.** `ajax-studio shorts` cuts a vertical
   excerpt from the highest-tension window that does not spoil the crisis. Two or
   three per episode, posted between long-form uploads.

## Phase 3 — cross-platform, unpaid first

The original ask included cross-platform ads. Hold that spend until there is
evidence the content converts, because paid traffic to a channel that does not
retain buys nothing but a worse audience signal.

1. **Post the same vertical cuts** to TikTok, Reels and Shorts. Free, and it is
   how faceless channels actually find an audience.
2. **Measure retention before spending.** If the 30-second retention on organic
   Shorts is poor, paid promotion will be worse, not better.
3. **Only then consider paid.** Start at a scale where losing the whole budget
   teaches you something — a few hundred dollars, one platform, one creative
   variant against one control.

Gate for any paid spend: organic Shorts retention and click-through good enough
that the bottleneck is clearly reach rather than the content.

## Phase 4 — the monetisation gate

Do not chase the threshold; chase episode 12. If the season lands, the threshold
follows. If it does not, the threshold would not have saved it.

When YPP is reached: revisit the disclosure surfaces, confirm every video carries
the altered-content answer, and re-read the inauthentic-content policy as it
stands then rather than as summarised here.

---

## What this repository will not do

- **Upload anything.** No API keys, no automated publishing. The last human check
  before something goes public is the point of it, not an inconvenience.
- **Generate episodes on a schedule.** The pipeline builds an episode when asked.
  Nothing here produces content unattended, because that is the exact behaviour
  the policy targets.
- **Fake a voice track.** `voice.py` ships a silence backend for previz and an
  interface for a real one. There is no implementation that pretends to speak.
