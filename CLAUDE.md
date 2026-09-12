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

The shim is not write-once, and the line count will grow: steps 4-6 need slider travel,
per-object strain and difficulty attributes that step 1 does not. What is frozen is the
*direction* — C# does extraction, simulation and score arithmetic, never analysis — and
that its JSON output is versioned.

One thing does get reimplemented: **the input-to-judgement state machine**. Lazer produces
judgements in the drawable layer (`DrawableHitObject.UpdateAfterChildren` runs every frame,
`DrawableHitCircle` only registers a press `if (IsHovered)`, notelock reads the live
drawable pool), so getting it from lazer means hosting a game loop. The port boundary:

- **Never reimplemented** — stack offsets, slider path approximation, beatmap conversion,
  `ApplyDefaults`, mod application, hit-window *values*, score arithmetic. `ScoreProcessor`
  runs unloaded with no host, so the ordered judgement list goes straight back into it.
- **Reimplemented** — the judgement loop only. Notelock is 106 lines of pure logic. Slider
  tracking and spinner accumulation are the fiddly parts.

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
osu-tools, nothing to construct — and for lazer replays it is richer than the legacy five
counts, since version 30000001+ carries a JSON blob with full `statistics` and
`maximum_statistics` including `slider_tail_hit` and `large_tick_hit`. Total score is the
weak assertion, not the strong one: it is int32-truncated and means different things for
lazer and stable scores. Assert counts and max combo.

There is no local Replays folder. The corpus is lazer's exports, and beatmaps are not
reachable by path at all — the `.osr` header carries the beatmap's MD5 while lazer's file
store is addressed by SHA-256, and only the realm database holds the mapping. Build that
index first, from a *copy* of the realm, and never traverse or brute-force hash the file
store.

The oracle settles *rules*, not *parameters*. Whether `TryJudgeNestedObject` was ported
correctly is a question about semantics, and a divergence there is a defect. How often the
judgement loop is evaluated is a property of the machine, and the oracle's machine is a
headless test host, not a player's. Those need different references and different numbers:
the header is the only evidence about real clients, so it sets the production sampling step,
while oracle diffs run at whatever step matches the host, so that a divergence means a rule
is wrong rather than that two machines sampled differently. Never let one substitute for the
other, and keep both numbers written down with what they were measured against.

Build the differential oracle early, not late. Host lazer's real gameplay headlessly, play
the replay, and record what it judged object by object. The header gives totals, which tell
you a run is wrong and nothing about where; the oracle names the object and the millisecond.
It is quarantined in a test project and must never be referenced from the pipeline.

Aim for exact reproduction, but do not treat every shortfall as a bug, and do not treat the
header as the target. The right gate is agreement with the differential oracle, which is
what proves the port, plus a header residual every part of which has a measured cause. The
match rate against the header alone is not a meaningful number, because most of the corpus
disagrees for reasons that have nothing to do with whether the ruleset was reimplemented
correctly. Stage the work — circles, then sliders, then spinners — and make the deliverable
a *mismatch taxonomy*: every residual assigned to a named cause with a minimal reproducing
test. The failures teach the parts of the ruleset that aren't documented anywhere —
notelock especially (object n+1 cannot be judged before n resolves), and lazer slider tail
vs Classic.

Four causes account for the local corpus, and only the last is a defect:

- **The score was set under different hit windows.** Every edge moved half a millisecond on
  lazer `2025.710.0`, the first release carrying ppy/osu `0f078ee550`. Before it the window
  was the raw difficulty range and could sit anywhere, including on the whole-millisecond
  grid that replay frame times occupy; after it every edge is an integer minus 0.5 and is
  immune to that rounding. Judging an older score against the current ruleset moves roughly
  two to three judgements per replay off a window edge, always outward. This is the single
  largest class, and it is a rules change, not an error. Bucket by `ScoreInfo.ClientVersion`
  before drawing any conclusion from a match rate.
- **The replay stopped before the beatmap did.** A failed or abandoned play records nothing
  past its end. Judging past the last frame invents misses.
- **Slider tracking.** See the trap below: lazer's own tracking is framerate-dependent by a
  mechanism ppy has open as a bug, so tails are not reproducible even in principle.
- **A click judgement differs on a modern client's completed play.** This bucket should be
  empty. It is the only place a disagreement is evidence of a bug in the port.

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
  Note lazer's own `OsuDifficultyHitObject.Angle` is `Math.Abs(Math.Atan2(det, dot))` and,
  for slider-preceded objects, `Math.Min(angle, sliderAngle)` — an unsigned difficulty
  heuristic, not the feature wanted here. Take *positions* from lazer (`StackedPosition`,
  `LazyEndPosition`), which is the part with the edge cases, and compute the signed angle
  here. Emit lazer's value alongside as a cross-check.
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

Available per-object after extraction: signed hit error; cursor position and velocity;
aim error at hit time normalized by CS radius; tap intervals; frametime distribution;
object geometry; per-object aim/speed/rhythm strain.

**What the 60Hz recorder costs, feature by feature.** The two most important quantities in
the whole design survive intact: a button change forces a frame, so cursor position at the
moment of a click and the time of that click are both sampled exactly, not interpolated.
Aim error at hit time and tap interval are therefore as precise as the client was. What
does not survive is the derivative stack. **Jerk is out** — at a 17ms floor it is almost
entirely differencing artifact. Acceleration is marginal and should not carry a finding on
its own. Velocity is fine computed over a window of several samples, and wrong computed
between adjacent ones.

**Tap alternation *is* recoverable from lazer replays.** This note previously said the
opposite and was wrong. `OsuReplayFrame.ToLegacy` emits `Left1` for `OsuAction.LeftButton`
and `Right1` for `OsuAction.RightButton`, and osu! has exactly those two gameplay actions,
so nothing is lost: both arrive in the legacy bitfield as M1 and M2. Measured on three
exported lazer replays, presses alternate between the two 58%, 69% and 97% of the time,
which is a player alternating, not an artefact.

What is genuinely unavailable is which *physical* input produced an action, since lazer
binds any key or button to one of the two. So "did this player alternate" and "what is the
interval between taps on the same finger" are both answerable; "was this a mouse button or
a keyboard key" is not.

Conditioning worth running: aim error bucketed by jump angle × spacing × BPM; tap interval
regularity by position within a stream; hit error drift across map length; cursor velocity profile in the 200ms before a miss vs the same pattern class
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
- **Hit windows moved half a millisecond, and it splits the corpus in two.** ppy/osu
  `0f078ee550` (2025-04-18, shipped in `2025.710.0`) changed `OsuHitWindows.SetDifficulty`
  from the raw `DifficultyRange` value to `Math.Floor(range) - 0.5`. The stated purpose was
  to end exactly the problem this project ran into: replay frame times are whole
  milliseconds, so a window edge sitting on or near an integer lets playback reach a
  different judgement from the play. Moving every edge to a half-integer immunises it. The
  consequence for us is that a score set before that release was judged under rules the
  current ruleset no longer implements, and the difference is visible as two to three
  judgements per replay drifting outward from Great. Read `ScoreInfo.ClientVersion` and
  branch. Related ppy issues, all describing the same thing from the player's side: #28744,
  #29217, #11311.
- **Slider tracking is framerate-dependent by design defect, and ppy has it open as #34016.**
  The follow radius on frame n+1 depends on whether tracking was active on frame n, so the
  tracking decision feeds back into its own tolerance. Combined with the tail's 36ms
  leniency window, the same replay judged at two different sampling rates reaches different
  answers — ppy's own report demonstrates a tail that is missed at normal speed and hit at
  0.05x. This is *not* only the 60Hz position floor. Even with perfect cursor data, tails
  would not be reproducible without knowing the original client's frame rate, which the file
  does not record. Treat tail and large-tick counts as a bounded interval, never as an
  equality.
- **Replay frames are not a uniform sample.** Stable ties frames to client framerate, so
  temporal resolution varies with the player's rig, and interpolation happens at roughly
  the precision being measured. Characterize this before building anything on derivatives
  — which is why jerk is not in the feature list at all.
- **A replay does not reproduce the play it came from, and the loss is specific.** The
  recorder stores cursor position at a fixed 60Hz (`ReplayRecorder.RecordFrameRate = 60`),
  taking extra frames only when a button changes state. Tracking was judged during the play
  against the true cursor at the client's real frame rate; those positions are not in the
  file, so anything replaying it interpolates between 17ms samples. The differential oracle
  shows the live game replaying a replay disagreeing with the header that same play wrote,
  which is also why judgement counts can differ between the results screen and watching the
  replay back — ppy #28744, closed, and #34016, open.
  What survives exactly, and it is the important half: every click. A button change forces a
  frame, so the press time and the cursor position at the press are recorded rather than
  interpolated, and on a modern client a completed play's Great/Ok/Meh/Miss counts reproduce
  exactly. The header is unreachable on *tracking-dependent* judgements specifically, and
  reachable on the rest.
- **The replay handler's "important section" rule never applies.**
  `FramedReplayInputHandler` refuses mid-frame times while a button is held — but only when
  `FrameAccuratePlayback` is true, and across the whole `ppy/osu` tree that public field is
  assigned in exactly one place: `osu.Game.Tests/NonVisual/FramedReplayInputHandlerTest.cs`.
  Never in game code. Implementing the rule as written is measurably wrong, because it
  suppresses fine sampling exactly where slider tracking is decided.
- **Replays end when the play ends.** A failed or abandoned play simply has no frames past
  that point, and lazer judges nothing after it. Simulating to the end of the beatmap
  invents a miss for every remaining object — which looks like a catastrophic ruleset bug
  and is not one.
- **Read error vs aim error can look identical in the data.** A misread usually shows the
  cursor travelling confidently to the wrong place; an aim error shows it travelling to
  the right place imprecisely. That separation is inference, and it's where a model will
  confabulate hardest.
- **Rhythm misreads** show cleanly (error clusters at a beat-fraction offset rather than
  scattering around zero) but the *cause* — the map snapped to a music layer the player
  wasn't tracking — is not recoverable from the beatmap alone.

## Licensing

**This project is non-commercial only.** That decision settles most of what used to be a
long section here, because nearly every constraint in it existed to keep a commercial
option open.

- `ppy/osu` and `osu-framework` are **MIT** — keep the copyright and licence notice.
- **osu-framework is in the build, unavoidably.** `osu.Game` references it directly, and
  `PathApproximator` — the piecewise-linear approximator that *is* the slider path for
  every gameplay purpose — lives there, not in `ppy/osu`. It pulls BASS transitively, and
  `osu.Game` pulls `ppy/osu-resources` (CC-BY-NC) too. Under non-commercial use both are
  fine: BASS is free for non-commercial use and the NC clause is satisfied rather than
  tripped.
- **Still never construct a `GameHost`, `AudioManager` or `OsuGameBase`** — but for
  engineering reasons now, not licensing ones. A hosted game loop makes extraction
  framerate-coupled, which is the one property the extraction layer cannot have, and drags
  realm, SQLite, the skin manager and the beatmap manager into a process whose job is to
  read two files. `HostGuardTests` asserts this by checking `/proc/self/maps`.
- **Never ship audio or beatmap files.** This one is unchanged and unconditional. Beatmaps
  are user submissions of third-party music, ppy claims no rights over distribution, so the
  burden is entirely ours — and none of that depends on commercial status. Only the
  Featured Artist catalogue is blanket-cleared, and even there, tracks by a featured artist
  that aren't in their listing aren't licensed. Tests build beatmaps in code rather than
  carrying fixtures.
- **ppy assets in the UI** are now permitted, but an independent visual language is still
  the better call for a tool that should look like its own thing.
- **Naming.** "osu!" and "ppy" are trademarks and ppy asks to be contacted for clearance.
  Lower stakes for a non-commercial tool, but a rename is still cheap.
- **Top-50 corpus.** Other players' data through an API anyone with an account can use. An
  email to ppy before pulling at volume is good manners.

## Non-goals

- LLM pattern classification.
- VLM on gameplay frames as a perception layer.
- Video rendering (danser-go / o!rdr) as the output medium.
- Porting lazer's geometry, in any language. The viewer draws the shim's precomputed
  polyline and never recomputes curves — reimplementing them in JS is the same non-goal
  wearing a different hat.
- The replay viewer before steps 1–6 are done.
