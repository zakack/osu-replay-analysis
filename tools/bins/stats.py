"""Reduce observations to per-cell statistics in one pass, holding no samples.

The reference side is millions of clicks across a leaderboard corpus, so the obvious
shape — a dict of cell to list of floats, which is what ``step5.py`` did at one player's
scale — does not fit in memory and never will. Everything here is therefore a running
accumulator: constant space per cell, one pass over the input, no second visit.

Welford rather than power sums. Power sums are shorter to write and lose precision
exactly where this corpus lives, at large n with a mean far from zero; Welford costs
three extra lines and removes the question. The spread reported is the *population*
standard deviation, matching what steps 5 and 6 already published, so numbers here stay
comparable to numbers already written down.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from .schema import CellStats, Observation

# (target, fromSlider, stratum, cell). The facets are part of the key rather than a
# filter because slider aim demand is a different demand, not a subset of the same one.
CellKey = tuple[str, str, str, tuple[str, ...]]


class Accumulator:
    """Running statistics for one cell: counts and Welford moments, never samples.

    Hit error and aim error are tracked as two independent streams because their
    populations genuinely differ — a missed object carries neither, and the loader leaves
    each field empty on its own terms — so a single shared count would silently misreport
    whichever one it did not belong to.
    """

    __slots__ = ("total", "misses", "hit_n", "aim_n",
                 "_hit_mean", "_hit_m2", "_aim_mean", "_aim_m2")

    def __init__(self) -> None:
        self.total = 0
        self.misses = 0
        self.hit_n = 0
        self.aim_n = 0
        self._hit_mean = 0.0
        self._hit_m2 = 0.0
        self._aim_mean = 0.0
        self._aim_m2 = 0.0

    def add(self, observation: Observation) -> None:
        self.total += 1
        if observation.result == "Miss":
            self.misses += 1

        value = observation.hit_error
        if value is not None:
            self.hit_n += 1
            delta = value - self._hit_mean
            self._hit_mean += delta / self.hit_n
            self._hit_m2 += delta * (value - self._hit_mean)

        value = observation.aim_error
        if value is not None:
            self.aim_n += 1
            delta = value - self._aim_mean
            self._aim_mean += delta / self.aim_n
            self._aim_m2 += delta * (value - self._aim_mean)

    def result(self) -> CellStats | None:
        """``None`` only when nothing at all was added.

        A cell that collected observations but no hit errors — every object missed, say —
        still reports, with ``n`` zero and a miss rate of 1. Dropping it here would lose
        the one fact it does carry; the min-n gate in ``compare`` is where it stops.
        """
        if self.total == 0:
            return None
        return CellStats(
            n=self.hit_n,
            hit_mean=self._hit_mean,
            hit_sd=math.sqrt(self._hit_m2 / self.hit_n) if self.hit_n else 0.0,
            aim_n=self.aim_n,
            aim_mean=self._aim_mean,
            aim_sd=math.sqrt(self._aim_m2 / self.aim_n) if self.aim_n else 0.0,
            total=self.total,
            miss_rate=self.misses / self.total,
        )


def aggregate(observations: Iterable[Observation],
              keyer: Callable[[Observation], tuple[str, ...] | None],
              ) -> dict[CellKey, CellStats]:
    """Group observations into cells and reduce each to ``CellStats``.

    ``keyer`` returns the cell's axis labels, or ``None`` for an observation the scheme
    cannot place — a missing angle, a snap outside the set — which is dropped rather
    than pooled.

    Every observation is counted twice on the player side: once under its era and once
    under ``"all"``. Eras exist because the player's input regime changed under them
    (see ``schema.ERAS``) and every tap feature steps at those dates, but a per-era table
    is also a fraction of the data, so both readings have to be available and neither is
    worth a second pass over millions of rows to get. An observation with no era — the
    reference corpus, which has none — lands in ``"all"`` once and is not double-counted.
    """
    cells: dict[CellKey, Accumulator] = {}

    for observation in observations:
        cell = keyer(observation)
        if cell is None:
            continue

        target = observation.target
        from_slider = observation.from_slider

        key = (target, from_slider, "all", cell)
        accumulator = cells.get(key)
        if accumulator is None:
            accumulator = cells[key] = Accumulator()
        accumulator.add(observation)

        era = observation.era
        if era is not None:
            key = (target, from_slider, era, cell)
            accumulator = cells.get(key)
            if accumulator is None:
                accumulator = cells[key] = Accumulator()
            accumulator.add(observation)

    return {key: stats for key, accumulator in cells.items()
            if (stats := accumulator.result()) is not None}


def aggregate_many(observations: Iterable[Observation],
                   keyers: dict[str, Callable[[Observation], tuple[str, ...] | None]],
                   ) -> dict[str, dict[CellKey, CellStats]]:
    """``aggregate`` for several schemes at once, over a single pass of the input.

    The schemes are independent, so running them separately is correct and obvious — and
    it rescans four hundred megabytes of reference CSV once per scheme to produce it. The
    input is an iterator that cannot be rewound anyway, so the choice is this or a list
    of a million Observations in memory.
    """
    tables: dict[str, dict[CellKey, Accumulator]] = {name: {} for name in keyers}

    for observation in observations:
        target = observation.target
        from_slider = observation.from_slider
        era = observation.era

        for name, keyer in keyers.items():
            cell = keyer(observation)
            if cell is None:
                continue

            table = tables[name]
            for stratum in ("all", era) if era is not None else ("all",):
                key = (target, from_slider, stratum, cell)
                accumulator = table.get(key)
                if accumulator is None:
                    accumulator = table[key] = Accumulator()
                accumulator.add(observation)

    return {name: {key: stats for key, accumulator in table.items()
                   if (stats := accumulator.result()) is not None}
            for name, table in tables.items()}
