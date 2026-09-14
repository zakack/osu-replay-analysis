"""The bin scheme, and the shape of everything downstream of it.

This module is the contract. Every number the analysis produces is an aggregate over
cells defined here, so an edge moved is every finding changed — which is the whole
reason the edges live in one versioned file instead of being written inline at three
call sites the way ``step5.py`` wrote them.

Two rules the rest of the package inherits:

* **Bins, not names.** A cell is a tuple of interval labels. "Wide-angle jump" is a
  presentation label applied afterwards, by the layer allowed to be wrong about it.
* **Nothing is collapsed to make a table smaller.** Chirality is kept, slider-adjacent
  turns get their own cells rather than falling into circle cells, and correlated
  findings are emitted rather than deduplicated. Deduplication is the model's job.

Bumping ``SCHEMA_VERSION`` is the signal that cached findings are stale.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SCHEMA_VERSION = 3

DEG = 180 / math.pi

# --------------------------------------------------------------------------------------
# Axes
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Axis:
    """One dimension of a cell key.

    ``edges`` are ascending bin boundaries, each bin half-open as ``[lo, hi)``. A value
    below the first edge or at or above the last is outside the axis and drops the
    observation rather than being clamped into an end bin, because a clamped bin is a
    silent lie about what is in it.
    """

    name: str
    edges: tuple[float, ...]
    unit: str

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self._label(i) for i in range(len(self.edges) - 1))

    def _label(self, i: int) -> str:
        lo, hi = self.edges[i], self.edges[i + 1]
        fmt = lambda v: ("inf" if v == math.inf else "-inf" if v == -math.inf
                         else f"{v:g}")
        return f"{fmt(lo)}:{fmt(hi)}"

    def bin(self, value: float | None) -> str | None:
        if value is None or math.isnan(value):
            return None
        # The top edge is inclusive, and only the top edge. An angle of exactly 180 is a
        # perfectly straight turn, which is common rather than exotic, and dropping it
        # would quietly remove the easiest geometry from every table.
        if value == self.edges[-1]:
            return self._label(len(self.edges) - 2)
        for i in range(len(self.edges) - 1):
            if self.edges[i] <= value < self.edges[i + 1]:
                return self._label(i)
        return None


@dataclass(frozen=True)
class Categorical:
    """An axis whose values are already discrete, e.g. rhythm snap or object kind."""

    name: str
    values: tuple[str, ...]
    unit: str = ""

    @property
    def labels(self) -> tuple[str, ...]:
        return self.values

    def bin(self, value: str | None) -> str | None:
        return value if value in self.values else None


# The turn at an object, signed. The sign is the cross product of (previous -> apex) and
# (apex -> next) in osu! coordinates, where y increases *downward* — so a positive value
# is a turn that reads clockwise on screen. Note Geometry.cs documents this as
# counter-clockwise; the arithmetic is the same either way, only the word differs, and
# the word matters once these labels reach a player.
#
# Magnitudes match lazer's convention: 180 is straight through, 0 is a full reversal.
ANGLE = Axis(
    "angle",
    (-180.0, -150.0, -120.0, -90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0),
    "degrees, signed",
)

# Spacing in circle radii rather than osu!pixels, so CS drops out of the comparison.
# Above six radii the original top bin held everything from a comfortable jump to a
# full-playfield leap, which is fine while the question is streams and useless the moment
# it is jumps. The upper edges were added when the ranking started returning wide-spacing
# cells and could not say which kind of wide.
SPACING = Axis(
    "spacing",
    (0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.5, 6.0, 8.0, 10.0, math.inf),
    "radii",
)

# Required cursor velocity: spacing / strain time. The axis the charter expects to
# predict error, and the one that puts a 3-radii 1/2 jump and a 1.5-radii 1/4 jump at
# the same BPM in the same place.
VELOCITY = Axis(
    "velocity",
    (0.0, 0.01, 0.02, 0.03, 0.045, 0.065, 0.09, math.inf),
    "radii per ms",
)

# Rhythm as a fraction of the beat, taken from the quantised value the rhythm pass
# already computed. Anything not in this set is dropped rather than pooled into an
# "other" bin that would mix triplets with dotted rhythms with timing-point noise.
# Position within a constant-snap run. The one axis that is not geometry at all, and the
# one the project's existing finding lives on: timing spread grows from the start of a
# stream to the end of it. Banded rather than per-index, because position 14 only exists
# inside long streams and long streams sit on harder maps, so an unbanded trend measures
# map difficulty as much as it measures fatigue.
RUN_POSITION = Axis(
    "runPosition",
    (0.0, 2.0, 5.0, 9.0, 14.0, math.inf),
    "index in run",
)

SNAP = Categorical(
    "snap",
    ("0.125", "0.1667", "0.25", "0.3333", "0.5", "0.75", "1", "1.5", "2"),
    "beats",
)

# Always part of the key, never a scheme choice. Slider aim demand is not captured by
# head position, so a turn leaving a slider is a different demand from the same turn
# between two circles and must not share a cell with it.
TARGET = Categorical("target", ("circle", "slider"))
FROM_SLIDER = Categorical("fromSlider", ("0", "1"))

FACETS: tuple[Categorical, ...] = (TARGET, FROM_SLIDER)

SCHEMES: dict[str, tuple[Axis | Categorical, ...]] = {
    # The charter's worked example: "error concentrates in the 120-150 x 2.5-3.5 radii
    # x 1/2-snap cell".
    "angle-spacing-snap": (ANGLE, SPACING, SNAP),
    # Angle against the demand axis directly, which pools maps of different tempo that
    # ask for the same thing.
    "angle-velocity": (ANGLE, VELOCITY),
    # Where in a stream the click fell, with no geometry. Steps 5 and 6 found the
    # player's spread growing through a run and had to say so in prose; this is that
    # finding with a cell to live in.
    "snap-runposition": (SNAP, RUN_POSITION),
    # Geometry removed entirely. The control for every finding above: if a cell in one
    # of the schemes above is only as bad as its snap alone predicts, the geometry is
    # not what is wrong.
    "snap-only": (SNAP,),
}

# Which scheme each scheme is measured against, and on which of its axes the lookup is
# keyed. Without this the table is unreadable in a specific way: when the player's whole
# baseline is two and a half times the reference, every one of six hundred geometry cells
# reports "two and a half times, hugely significant", and separating the cells that are
# worse than that baseline from the ones merely carrying it becomes arithmetic the
# reading layer has to do on two rows of this table. That arithmetic is classification,
# so it belongs here.
CONTROLS: dict[str, tuple[str, str]] = {
    "angle-spacing-snap": ("snap-only", "snap"),
    "snap-runposition": ("snap-only", "snap"),
}

# --------------------------------------------------------------------------------------
# Strata
# --------------------------------------------------------------------------------------

# The player's own input regime, measured 2026-09-12 over the local corpus by checking
# whether a replay ever holds both buttons in the same frame. The tool that makes them
# mutually exclusive was on, off, then on again, and every tap-timing feature steps at
# those two dates for a reason that has nothing to do with the player. Dates are the
# first day of the regime; February 2025 is a transition month and is reported as part
# of the overlapping era rather than being dropped.
ERAS: tuple[tuple[str, str, str], ...] = (
    ("exclusive-1", "2024-11-01", "2025-02-01"),
    ("overlapping", "2025-02-01", "2026-01-01"),
    ("exclusive-2", "2026-01-01", "2099-01-01"),
)

# --------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One judged click, with everything a cell key or a metric needs and nothing else.

    Built by ``load``; consumed by ``cells``, ``stats`` and ``maptiming``. Fields are
    ``None`` where the source CSV was empty, and a cell key that needs a ``None`` field
    drops the observation.
    """

    replay: str
    player: str
    beatmap_md5: str
    era: str | None            # player side only; None for reference replays
    start_time: float          # ms, the object's time — the join key across players
    target: str                # "circle" | "slider"
    from_slider: str           # "0" | "1"
    angle: float | None        # degrees, signed
    spacing: float | None      # radii
    velocity: float | None     # radii per ms
    delta_time: float | None   # ms
    snap: str | None           # beats, quantised, as a label
    run_index: int | None      # position within a constant-snap run
    run_size: int | None
    hit_error: float | None    # ms, signed; negative is early
    aim_error: float | None    # radii
    result: str


@dataclass(frozen=True, slots=True)
class CellStats:
    """What one side of the comparison did in one cell.

    Three counts, not one, because the three metrics are measured over three different
    populations and each one's standard error needs its own. A missed object has a hit
    error but no aim error, so ``aim_n`` is below ``n`` wherever a cell contains misses;
    an object that was never clicked has neither, so ``total`` is above both and is the
    denominator ``miss_rate`` was computed against. Using ``n`` for all three makes the
    aim significance optimistic and the miss-rate significance conservative, in the same
    table, which is the kind of error that survives review because it is invisible.
    """

    n: int              # observations with a hit error; the denominator for hit metrics
    hit_mean: float
    hit_sd: float
    aim_n: int          # observations with an aim error
    aim_mean: float
    aim_sd: float
    total: int          # every observation in the cell; the denominator for miss_rate
    miss_rate: float


# Effect kind decides whether player and reference are compared as a ratio or a
# difference: a spread is a scale and compares as a ratio, a signed mean is a location
# and compares as a difference.
# Metrics a finding can be about: (name, higher-is-worse, effect kind, population).
# The population names which of CellStats' three counts the metric's standard error and
# its reported n are taken from.
METRICS: tuple[tuple[str, bool, str, str], ...] = (
    ("hitSd", True, "ratio", "n"),
    ("hitMean", True, "difference", "n"),
    ("aimMean", True, "ratio", "aim_n"),
    ("missRate", True, "difference", "total"),
)

# A cell needs this many observations on each side before it is reported at all. A
# spread estimate on ninety samples is not a number, and a table that footnotes weak
# cells instead of dropping them is a table that will be read past the footnote.
MIN_N_PLAYER = 50
MIN_N_REFERENCE = 200

# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

# The findings table: one row per cell x metric, and the whole input to the layer that
# selects and narrates. Column order is part of the schema.
FINDINGS_COLUMNS: tuple[str, ...] = (
    "schemaVersion",
    "scheme",
    "stratum",        # "all" or an era name
    "cell",           # the axis labels, joined by "|", in scheme order
    "target",
    "fromSlider",
    "metric",
    "playerN",        # the count for this metric's population, not always the hit count
    "playerValue",
    "referenceN",
    "referenceValue",
    "effect",         # ratio or difference, per METRICS
    "effectKind",
    "z",              # effect over its standard error; sign is "worse than reference"
    "controlEffect",  # the same effect in this cell's control cell, or empty if none
    "relativeEffect", # effect once the control is divided or subtracted out
    "detrended",      # 1 if hit errors had the per-object reference median removed
)

# The map-timing table. Written alongside the findings so that a cell sitting on a
# section the whole leaderboard plays late can be discounted rather than believed.
MAP_BIAS_COLUMNS: tuple[str, ...] = (
    "schemaVersion",
    "beatmapMd5",
    "startTime",
    "referenceN",
    "referenceMedian",  # ms; the map's own bias at this object
    "referenceIqr",
)
