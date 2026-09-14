"""Player cells against reference cells, one row per cell per metric.

This module is deliberately dumb. It emits every comparison that is arithmetically
sound and says nothing about which ones matter: no ranking, no deduplication, no
significance filter. Cursor overshoot on wide angles, aim error on wide angles and a
velocity spike before wide-angle misses are one problem wearing three hats, and
collapsing them here would be a classification decision made by whoever wrote the
collapsing rule. That is the selection layer's job, and it reads this table.

The only thing dropped is a cell too thin to estimate from, and a comparison whose
standard error does not exist.

**Sign convention.** ``z`` is positive when the player is worse than the reference.
Every entry in ``schema.METRICS`` is flagged higher-is-worse, so the raw statistic
already carries that sense and nothing is negated here.
"""
from __future__ import annotations

import csv
import math
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass

from .schema import (FINDINGS_COLUMNS, METRICS, MIN_N_PLAYER, MIN_N_REFERENCE,
                     SCHEMA_VERSION, CellStats)
from .stats import CellKey


@dataclass(frozen=True)
class Finding:
    """One row of the findings table.

    Field names and order are ``schema.FINDINGS_COLUMNS`` verbatim, camel case included,
    so that writing the CSV is a ``getattr`` in column order and a renamed column cannot
    silently reorder the file.
    """

    schemaVersion: int
    scheme: str
    stratum: str
    cell: str
    target: str
    fromSlider: str
    metric: str
    playerN: int
    playerValue: float
    referenceN: int
    referenceValue: float
    effect: float
    effectKind: str
    z: float
    detrended: int


def compare(player: dict[CellKey, CellStats], reference: dict[CellKey, CellStats],
            *, scheme: str, detrended: bool = False,
            min_n_player: int = MIN_N_PLAYER,
            min_n_reference: int = MIN_N_REFERENCE,
            metrics: tuple[str, ...] | None = None,
            skipped: MutableMapping[str, int] | None = None) -> list[Finding]:
    """Every cell both sides populate well enough, once per metric in ``schema.METRICS``.

    Reference cells are matched on ``(target, fromSlider, cell)`` at stratum ``"all"``.
    The reference corpus has no eras — it is other people's replays, and the input-regime
    dates are a fact about this player's machine — so splitting it by anything would only
    shrink it against no gain.

    ``metrics`` restricts which of ``schema.METRICS`` are emitted. It exists for one
    case: hit errors with a map's own bias removed are sound for a *location* metric and
    not for a *scale* one. The bias is a per-object constant, so subtracting it shifts a
    mean exactly and leaves a single object's spread untouched — but across a cell it is
    a different estimate per object, each carrying about three milliseconds of its own
    noise, and that noise lands on the player side while the reference side is detrended
    against estimates computed from itself. The result is a quiet lean toward "player
    worse" on precisely the spread ratio this project's headline rests on. So detrended
    runs emit location metrics only, and the scale metrics come from the raw pass.

    ``skipped``, if given, is incremented per drop reason: the return type is fixed by the
    contract, so a count of what did not make the table has nowhere else to go, and a
    caller that does not report it will quietly believe a short table is a clean one.
    """
    counts = skipped if skipped is not None else {}
    findings: list[Finding] = []

    for key in sorted(player):
        target, from_slider, stratum, cell = key
        p = player[key]
        r = reference.get((target, from_slider, "all", cell))

        if r is None:
            _count(counts, "no-reference-cell")
            continue
        if p.n < min_n_player or r.n < min_n_reference:
            _count(counts, "min-n")
            continue
        if p.n <= 1 or r.n <= 1:
            _count(counts, "n<=1")
            continue

        for metric, _worse, kind, population in METRICS:
            if metrics is not None and metric not in metrics:
                continue
            outcome = _statistic(metric, p, r)
            if isinstance(outcome, str):
                _count(counts, f"{metric}:{outcome}")
                continue
            value_p, value_r, effect, z = outcome
            # The count reported is the one this metric was actually measured over, which
            # is not the same column for all four. A reader comparing a miss rate against
            # the hit-error count would be reading a denominator that never applied.
            n_p = getattr(p, population)
            n_r = getattr(r, population)
            findings.append(Finding(
                schemaVersion=SCHEMA_VERSION,
                scheme=scheme,
                stratum=stratum,
                cell="|".join(cell),
                target=target,
                fromSlider=from_slider,
                metric=metric,
                playerN=n_p,
                playerValue=value_p,
                referenceN=n_r,
                referenceValue=value_r,
                effect=effect,
                effectKind=kind,
                z=z,
                detrended=1 if detrended else 0,
            ))

    return findings


def _statistic(metric: str, p: CellStats, r: CellStats,
               ) -> tuple[float, float, float, float] | str:
    """``(playerValue, referenceValue, effect, z)``, or a reason the row cannot exist.

    Each metric uses its own population, named by ``schema.METRICS``: hit metrics over
    the hit-error count, ``aimMean`` over the aim-error count, ``missRate`` over every
    observation in the cell. They genuinely differ — a miss carries no hit error and
    usually no aim error either — and sharing one count between them would make the aim
    significance optimistic and the miss-rate significance conservative in the same table.
    """
    if metric == "hitSd":
        # A scale compares as a ratio, and the log of a ratio is what is symmetric and
        # roughly normal; se is the standard large-sample error of a log sd.
        if p.hit_sd <= 0 or r.hit_sd <= 0:
            return "zero-sd"
        se = math.sqrt(1 / (2 * (p.n - 1)) + 1 / (2 * (r.n - 1)))
        if se == 0:
            return "zero-se"
        effect = p.hit_sd / r.hit_sd
        return p.hit_sd, r.hit_sd, effect, math.log(effect) / se

    if metric == "aimMean":
        # Delta-method error of a log mean: each side's coefficient of variation over its
        # own count.
        if p.aim_mean <= 0 or r.aim_mean <= 0:
            return "zero-mean"
        if p.aim_n <= 1 or r.aim_n <= 1:
            return "aim-n<=1"
        se = math.sqrt((p.aim_sd / p.aim_mean) ** 2 / p.aim_n
                       + (r.aim_sd / r.aim_mean) ** 2 / r.aim_n)
        if se == 0:
            return "zero-se"
        effect = p.aim_mean / r.aim_mean
        return p.aim_mean, r.aim_mean, effect, math.log(effect) / se

    if metric == "hitMean":
        # A signed location compares as a difference. Worse here means *larger magnitude*,
        # not larger value — playing 8ms early is the same defect as playing 8ms late — so
        # the selection layer reads abs(z) for this metric and only this one.
        se = math.sqrt(p.hit_sd ** 2 / p.n + r.hit_sd ** 2 / r.n)
        if se == 0:
            return "zero-sd"
        effect = p.hit_mean - r.hit_mean
        return p.hit_mean, r.hit_mean, effect, effect / se

    if metric == "missRate":
        effect = p.miss_rate - r.miss_rate
        variance = (p.miss_rate * (1 - p.miss_rate) / p.total
                    + r.miss_rate * (1 - r.miss_rate) / r.total)
        # Two cells nobody has ever missed in have no standard error and no disagreement
        # either. The row is worth keeping — it says the cell is clean on both sides — so
        # emit it at z = 0 rather than dropping it or dividing by zero.
        z = effect / math.sqrt(variance) if variance > 0 else 0.0
        return p.miss_rate, r.miss_rate, effect, z

    raise ValueError(f"unknown metric {metric!r}")


def _count(counts: MutableMapping[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def write(findings: Iterable[Finding], destination: str) -> None:
    """Write the findings CSV, columns exactly ``schema.FINDINGS_COLUMNS``.

    Floats go out at six significant figures, close to the C# side's ``0.#####``: this is
    a table to be read and selected from, not re-differentiated, and full repr precision
    only makes the columns unreadable.
    """
    with open(destination, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FINDINGS_COLUMNS)
        for finding in findings:
            writer.writerow([_format(getattr(finding, column))
                             for column in FINDINGS_COLUMNS])


def _format(value: object) -> str:
    return f"{value:.6g}" if isinstance(value, float) else str(value)
