# osu! replay analysis

Deterministic analyzer for osu! **lazer** replays. Turns `.osu` + `.osr` into a large
derived feature set, finds where error concentrates, and explains it in player vocabulary.

Existing tooling (Rewind, Circleguard, abraker's analyzer) *renders* the data. None of it
*diagnoses*. The target output is of the form: "your cursor undershoots wide-angle jumps
above 200bpm; tap deviation drifts positive after 8-note streams; here are five maps."

**Positioning matters.** The osu! community's reflex is that automated tooling = cheating.
Nobody objects to Rewind; people object to "AI coaches." This is an analyzer that happens
to explain itself. Keep the surface a scrubbable annotated viewer, not paragraphs of model
prose.

## Division of labor — non-negotiable

Deterministic extraction → deterministic classification → *then* the model sees a table of
labelled, already-significant findings.

The LLM's only jobs:

- **Selection.** Hundreds of statistically real findings, most of which are one problem
  wearing three hats (cursor overshoot on wide angles / aim error on wide angles /
  velocity spike before wide-angle misses). Dedup and rank by actionability.
- **Vocabulary.** Mapping a bin onto the words players use — "burst-into-jump transition,"
  "wide-angle exit," "inverted stream," "1/2 into 1/4 rhythm switch."
- **Narration and layout** of the artifact.
- **Hypothesis search.** Agentic loop: propose a slice, run it, read the residual, propose
  the next one. The part that isn't a single regression and isn't hand-enumerable.

The LLM never decides which objects are in a group. If a model does the labelling, the
diagnosis becomes nondeterministic in the one layer that can't afford it, and a regression
suite (corpus of replays with known problems, assert the tool finds them) becomes
impossible. Keep the fuzziness where being wrong is cosmetic: calling a 140° flow pattern
a "wide-angle jump" harms nobody; mis-assigning objects to a bin poisons every number
downstream.

Side benefit: with classification deterministic, most output is templated and cached, and
model spend is only on ordering and narrative. The audience is free-to-play and cannot
absorb a large model call per replay.

Constrain the model to speak only from computed features and to cite the number. It has
absorbed a lot of forum folklore about tablet area and smoothing and will produce causal
stories in that register on request.

## Architecture

**C# extraction shim, ~200 lines, written once.** Reference `osu.Game` and
`osu.Game.Rulesets.Osu` (MIT) as packages. Decode `.osu` and `.osr` through the real code
paths, walk the hit objects, dump JSON: per-object angle, jump distance, strain time,
stack offsets, slider travel, decoded replay frames. Then never touch C# again.

This is deliberate. Do **not** port the geometry. Porting means owning edge-case fidelity
to someone else's implementation forever — collinear fallback thresholds, stack leniency,
path extension, path approximation tolerance. Those don't crash; they shift positions a
few units and corrupt bins silently. The shim deletes that entire risk class in an
afternoon.

Everything above the shim is Python (analysis, binning) and JS (artifact). If any
reference geometry *is* reimplemented, build a differential harness first: run the
reference over a corpus, dump attributes, assert the reimplementation matches within
epsilon. Without an oracle, no model's output here is trustworthy.

**Output surface is a canvas replay viewer in the artifact, not video.** danser-go and
o!rdr can render `.osr` + `.osu` → video headlessly, but reimplementing playback on a
canvas is a couple hundred lines and gets scrubbing, frame stepping, variable speed, and
overlays video can't carry: your cursor path against the aggregate top-50 path on the same
pattern, hit error as colour along the trail, the 200ms approach window highlighted. It
shows the comparison rather than just the moment, and it makes the artifact the product
rather than a report format.

**No VLM on gameplay frames in the analysis path.** A frame of osu! is a cursor, circles
and approach rings; a VLM extracts strictly less than the numbers already contain, less
precisely. Clips are a UI affordance, not a perception one.

## Build order

Follow this order. Each step is a precondition for the next, not a preference.

**1. Reproduce the score exactly.** Before binning, before any analysis. Every `.osr`
header carries 300/100/50/miss counts, max combo and total score. If resimulating frames
against hit objects produces those exact numbers, that simultaneously proves object
association, hit windows, stack offsets, slider path, slider tail judgement and mod
handling. Off by a single 100 → something is broken and every bin built on top is
plausible-looking garbage. The ground truth ships inside the input file: no API, no
osu-tools, nothing to construct. Run it over the whole local Replays folder and iterate to
100%. The failures teach the parts of the ruleset that aren't documented anywhere —
notelock especially (object n+1 cannot be judged before n resolves), and lazer slider tail
vs Classic.

**2. The two-click flam — as a debugging instrument, not a feature.** Immediately after
extraction works, before any analysis. Two synthesized clicks per object: one at the
object time from the `.osu`, one at the actual hit time from the `.osr`. A systematic
offset in hit times is instantly audible and invisible in JSON. Ears beat eyes at exactly
the error most likely to be introduced here.

  Then it becomes the feature: flam width tracks error magnitude, detune or pan the tap
  click by error sign so early/late is distinguishable without thinking, 0.5x playback
  makes stream drift audible as the tap click sliding away from the reference. Auditory
  temporal resolution is ~an order of magnitude better than visual — a flashing visual
  metronome is a poor instrument for a 15ms offset. Everything here is synthesized Web
  Audio from timing points, so it carries no licensing exposure.

**3. The smallest real question with no geometry in it.** Hit error as a function of
position through the map. Does error mean drift late in long maps; does variance widen
after the first minute. Two columns, time and error. Ship it, confirm it says something
true about actual play, and the pipeline shape is validated before the hard part.

**4. Geometry. 5. Bins. 6. Top-50 reference corpus. 7. Viewer — last, always.** The viewer
is the most fun and the least informative; building it early will eat the project.

Steps 1–5 run entirely on the local Replays folder: no auth, no rate limits, no questions
about other people's data.

## Feature design

Rotation and scale invariance come free by never classifying on absolute position:

- **Angle** at object n formed by (n-1, n, n+1). Rotation-invariant by construction.
- **Spacing** in circle radii, not osu!pixels, so CS drops out.
- **Rhythm** as Δt / beat length, giving 1/2, 1/4, 1/3 snap rather than milliseconds.
- **Required cursor velocity** = spacing / strain time, in radii per ms. This is the axis
  that predicts error. A 3-radii 1/2 jump at 180bpm and a 1.5-radii 1/4 jump at 180bpm
  land in the same bin and are the same demand — which absolute-position matching misses.

**Preserve chirality.** Use signed angle; do not collapse CW and CCW into one bin.
Handedness asymmetry is real — the same player often shows different error distributions
left-to-right vs right-to-left at the same magnitude, and tablet users frequently have a
dead quadrant. Taking the absolute value erases a genuinely useful finding.

**Bins, not names.** The diagnosis is "error concentrates in the 120–150° × 2.5–3.5 radii
× 1/2-snap cell" — a groupby. The phrase "wide-angle jump" is a presentation label applied
to the cell afterwards.

Read ppy's difficulty calculator before writing any of this. Lazer already computes
per-object angle, radius-normalized jump distance and strain time, and its aim evaluator
already applies separate wide-angle and acute-angle bonuses. It's the reference
implementation of rotation-invariant featurization, open source, maintained, and it
produces the star rating that would be compared against.

Available per-object after extraction: signed hit error; cursor position, velocity,
acceleration, jerk; aim error at hit time normalized by CS radius; key state (tap
intervals, K1/K2 alternation); frametime distribution; object geometry; per-object
aim/speed/rhythm strain.

Conditioning worth running: aim error bucketed by jump angle × spacing × BPM; tap interval
regularity by position within a stream; hit error drift across map length; K1 vs K2 error
asymmetry; cursor velocity profile in the 200ms before a miss vs the same pattern class
when hit.

## The oracle — top-50 replays

The thing nobody has used. Top-50 replays for any ranked map are pullable through the API
(abraker's analyzer has had a script dumping hit timing to CSV for years). For any pattern
failed, compute how people who don't fail it move through it: entry velocity, where in the
approach they commit, overshoot-and-correct vs arriving straight.

This is the reference-lap structure from sim racing, in a game where nobody's built the
comparison. It also escapes the correlational trap that killed the 2019 AI-coaching cohort
— the output isn't "have a 2-digit's UR," it's a specific kinematic difference on a
specific pattern.

Second use: **mistimed maps.** If the whole top-50 shows the same error sign in the same
section, it's the map, not the player.

## Traps

- **Notelock.** Object n+1 cannot be judged before n resolves.
- **Stack offsets.** The `.osu` stores pre-stack positions; the client applies a stack
  offset from stack leniency at load. Get it wrong and stacked patterns report as
  zero-spacing. Algorithm is in the source, and it's fiddly.
- **Slider paths.** Four types: `L` linear, `P` perfect circle through three points, `B`
  bezier with repeated control points marking red-anchor segment breaks, `C` Catmull-rom
  (legacy, rare, still in old maps). `pixelLength` truncates or extends the computed curve.
  `P` silently falls back to bezier when the three points are near-collinear or the arc is
  too large. Repeats multiply everything.
- **Lazer does not use the analytic curve.** It approximates to piecewise-linear within a
  tolerance, and that polyline *is* the path for every gameplay purpose. A mathematically
  correct bezier disagrees with the game slightly everywhere — the failure mode that
  poisons spacing bins without ever throwing.
- **Slider aim demand** isn't captured by head position. Needs travel distance and travel
  time along the path, plus: the effective jump out of a slider starts from wherever the
  cursor actually was at slider end, not from the tail. Messiest part of the calc.
- **Lazer slider tail judgement is strict; the Classic mod restores old behaviour.** A
  "miss" means different things depending on mod list. Branch on it.
- **Lazer allows freely-set rate multipliers**, not just DT/HT. Read the actual rate off
  the mod; never assume 1.5 for BPM and strain-time normalization.
- **Replay frames are not a uniform sample.** Stable ties frames to client framerate, so
  temporal resolution varies with the player's rig, and interpolation happens at roughly
  the precision being measured. Characterize this before building anything on derivatives
  — jerk especially will be mostly artifact if handled carelessly.
- **Read error vs aim error can look identical in the data.** A misread usually shows the
  cursor travelling confidently to the wrong place; an aim error shows it travelling to
  the right place imprecisely. That separation is inference, and it's where a model will
  confabulate hardest.
- **Rhythm misreads** show cleanly (error clusters at a beat-fraction offset rather than
  scattering around zero) but the *cause* — the map snapped to a music layer the player
  wasn't tracking — is not recoverable from the beatmap alone.

## Licensing lines to hold

These are cheap to hold now and a per-layer rewrite to fix later. None of it bites on
paperwork; it bites on architecture, and all of it gets decided in week one.

- `ppy/osu` and `osu-framework` are **MIT** — fine commercially, keep the copyright and
  licence notice.
- **No osu-framework in the pipeline.** It depends on BASS, which is free for
  non-commercial use only and needs a paid un4seen licence commercially. The JSON-shim
  approach avoids this entirely — but verify early that referencing
  `osu.Game.Rulesets.Osu` doesn't drag framework audio in.
- **No ppy assets in the UI.** `ppy/osu-resources` is CC-BY-NC 4.0, and NC restricts *use*,
  not just distribution — server-side rendering with default skin assets is as much a
  problem as shipping them. It's a transitive NuGet dependency of `osu.Game`, so check at
  build time whether anything is actually *loaded* rather than merely referenced. Cheap to
  avoid with an independent visual language from day one; a rewrite if the look is built
  around ppy's sprites.
- **Never ship audio or beatmap files.** Beatmaps are user submissions of third-party
  music; ppy claims no rights over distribution, so the burden is entirely ours. Only the
  Featured Artist catalogue is blanket-cleared, and even there, tracks by a featured artist
  that aren't in their listing aren't licensed. The canvas-viewer design dodges this
  cleanly as long as audio is user-supplied local files or synthesized clicks.
- **Naming.** "osu!" and "ppy" are trademarks; ppy asks to be contacted for clearance.
  Trivially cheap to change now, expensive after a domain and users.
- **Top-50 corpus** is other players' data through an API anyone with an account can use,
  with no contract protecting access. That's a business risk, not a legal one — the right
  move is an email to ppy.

Hold those and going commercial later costs a rename and an email. The general caveat on
NC licences is that nobody agrees where "commercial" starts (ads, donations, Patreon are
all genuinely contested); if real money shows up, that's when to pay someone who does this
for a living.

## Non-goals

- LLM pattern classification.
- VLM on gameplay frames as a perception layer.
- Video rendering (danser-go / o!rdr) as the output medium.
- Porting lazer's geometry to Python.
- The replay viewer before steps 1–6 are done.
