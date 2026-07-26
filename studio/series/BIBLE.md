# BAY FOUR — series bible

A faceless, first-person diary series. One episode is one night shift in the
emergency department of a mid-sized city hospital, narrated by a nurse who is
telling you what happened and, gradually, what she is not telling anyone else.

Everything in it is invented. That is stated on screen and in every description,
and the rule is absolute — see [Boundaries](#boundaries).

---

## The pitch, in one line

*A night-shift ER nurse keeps a voice diary of her shifts, and it slowly becomes
the record of a mistake she is covering for.*

## Why this shape

The format has to solve three problems at once, and each choice below is aimed
at one of them.

**A faceless channel has no face to hold attention**, so the voice has to. A
first-person diary is the one narration style where having no face is *correct*
rather than a limitation — you are listening to a recording someone made alone
in a car at 7am, and seeing her would break it.

**Episodic content does not compound.** A viewer who liked one "day in the life"
video has no reason to watch the next. So there is a **serial thread**: a
mistake in episode 1 that Rowan does not report, which costs more every episode.
Episodic shift-of-the-week on the surface, one continuous story underneath.

**Suspense needs structure, not shocks.** Each episode opens on the worst
thirty seconds of the shift, out of order, then rewinds to the beginning. The
audience knows something is coming and spends the episode waiting for it. That
is dread, and dread retains better than surprise.

---

## Rowan

Not a real person, and not based on one.

- **Rowan Sayers**, 34, six years on nights in the emergency department at
  St. Brendan's — an invented hospital in an unnamed mid-sized city.
- **Voice:** dry, precise, tired. Undersells everything. Says "that was a bad
  one" about the worst night of her life. Never dramatises; the writing supplies
  the drama and she supplies the flatness, which is what makes it land.
- **Why she records:** her therapist suggested a voice journal after a shift two
  years ago that she still will not describe. She kept it up because it is the
  only place she says anything true.
- **What she wants:** to be good at this. Competence is her whole identity.
- **What she fears:** that she is not, and that one night will prove it.
- **The flaw that drives the series:** she protects people. She covers for a
  new grad's error in episode 1 because she remembers being the new grad. It is
  a kindness, and it is the wrong call, and both stay true all season.

### The people around her

Introduced by voice only — Rowan quotes them, we never hear them.

- **Deshawn** — charge nurse, unflappable, has seen everything and files it all.
  Rowan's closest thing to a friend, and the person she is lying to.
- **Priya** — new grad, four months in, earnest and fast and not yet careful.
  The error in episode 1 is hers.
- **Dr. Whitlock** — attending, brilliant, brusque, gone the moment a case is
  handed off. Not a villain; just never there when it matters.
- **Marguerite** — night-shift cleaner, seventies, knows everyone's business.
  The one person who notices Rowan is off, and says nothing until she does.

---

## Episode shape

Eight to twelve minutes, in five acts. Every episode uses this skeleton, and
`ajax-studio validate` enforces it.

| Act | Runtime | What it does |
| --- | --- | --- |
| **Cold open** | ≤ 45s | The worst thirty seconds of the shift, out of order. No context. Ends on a hard cut. |
| **Setup** | ~2 min | 19:00. The shift begins. Ordinary, warm, specific. Plant the thing that will matter. |
| **Rise** | ~4 min | Complications compound. Two or three cases, at least one funny, at least one that seems minor and is not. |
| **Crisis** | ~2 min | The cold open, now in context, now unbearable. |
| **Fallout** | ~2 min | 07:10, in the car. What it cost. One sentence she should say to someone and does not. |

**The hook has to land in the first fifteen seconds** — a line in the cold open
that states the stakes without explaining them. `validate` checks that the
first beat is short enough to clear that deadline.

**Tension must rise across the episode.** Every beat carries a 1–5 tension
reading; the validator checks the curve peaks in the crisis and does not flatten
in the rise. It is a crude measure of a real thing, and the writer can override
it, but it has to be overridden on purpose.

**Every episode ends unresolved.** Not a cliffhanger stunt — a question the
audience now holds. Episode 1 ends with Rowan not filing the report.

---

## The season arc

Twelve episodes. The thread is one continuous line and each episode advances it
exactly one step, whatever the shift-of-the-week is doing on the surface.

1. **Bay Four** — Priya's medication error. Rowan catches it, fixes it, does not
   report it. The patient is fine. That is what makes it easy.
2. **Boarding** — the patient comes back. Different complaint, same chart. Rowan
   sees her own correction in the notes and realises it is legible.
3. **Wait Times** — Deshawn asks a question about that night that he should not
   know to ask.
4. **The Good One** — a shift where nothing goes wrong, which is worse, because
   it gives her time to think.
5. **Incident** — an unrelated incident report goes around. Rowan reads it looking
   for her own name.
6. **Handover** — Priya thanks her. Explicitly. Out loud. In front of someone.
7. **Codes** — the season's set-piece. A genuine emergency, brilliantly handled,
   and Rowan is the best nurse in the room. It changes nothing.
8. **Marguerite** — the cleaner tells Rowan what she saw that night, and what she
   has decided to do about it.
9. **Chart Review** — a routine audit, scheduled months ago, lands on that week.
10. **Two Weeks** — Rowan drafts the report. Does not send it. Drafts it again.
11. **Nights** — she asks to move to days, and cannot explain why.
12. **Seven Ten** — she tells Deshawn. In the car park, at 07:10, mid-sentence,
    and the season ends before we hear his answer.

---

## Look and sound

The visuals are deliberately cheap to produce and consistent, because the writing
is where the effort goes.

- **Never Rowan's face.** Hands, corridors, a windscreen at dawn, a vending
  machine, a whiteboard, a badge on a lanyard. Shot as if she filmed it herself
  and mostly forgot to.
- **Palette:** sodium-orange corridor light, clinical green, night blue. High
  contrast, deep blacks — the pixel-lit hospital at 3am.
- **Nothing graphic.** No wounds, no blood, no bodies. The horror is entirely in
  the narration, which is both better writing and advertiser-safe.
- **Clock cards** between beats — `23:41` in the corner — carry the passage of
  the shift and cost nothing.
- **Sound:** room tone, distant monitors, a door. Music only under the cold open
  and the fallout, and never under the crisis, where silence is louder.

---

## Boundaries

These are not stylistic preferences. Breaking one puts the channel at risk, and
`ajax-studio validate` enforces the ones a linter can check.

1. **Fully fictional, and disclosed.** Rowan, St. Brendan's, every patient and
   colleague are invented. A card at the start and a line at the top of every
   description say so. No real hospital, city, person, or case is referenced.
2. **Synthetic narration is disclosed.** The voice is AI-generated and labelled
   as such, both in the description and via YouTube's altered-content
   disclosure. Realistic synthetic speech presented as a real recording is
   exactly what that disclosure exists for.
3. **Never medical advice, and never actionable.** No drug names with doses, no
   protocols a viewer could follow, no "what you should do if". The validator
   scans for dosage patterns and instructional phrasing and fails the build. A
   drama about competence must not become a how-to.
4. **No patient is a punchline.** The comedy is in the institution — the
   printer, the waiting room television, the manager's email — never in someone
   having a bad night.
5. **Volume is not the strategy.** YouTube's monetisation policy targets
   mass-produced repetitive content with no meaningful human input, and a
   faceless AI channel is squarely in its sights. What keeps this channel on the
   right side of it is that the writing is genuinely authored and the series is
   genuinely a story. Twelve good episodes beat a hundred generated ones, and
   the pipeline in this repository is deliberately built to make good episodes
   cheaper — not to make many episodes automatic.
