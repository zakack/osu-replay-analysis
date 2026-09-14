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
from collections.abc import Iterable, Iterator, MutableMapping
from dataclasses import dataclass

from .schema import (FINDINGS_COLUMNS, METRICS, MIN_N_PLAYER, MIN_N_REFERENCE,
                     SCHEMA_VERSION, CellStats)
from .cells import describe
from .schema import CONTROLS
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
    controlEffect: float | None
    relativeEffect: float | None
    detrended: int


def compare(player: dict[CellKey, CellStats], reference: dict[CellKey, CellStats],
            *, scheme: str, detrended: bool = False,
            min_n_player: int = MIN_N_PLAYER,
            min_n_reference: int = MIN_N_REFERENCE,
            metrics: tuple[str, ...] | None = None,
            control: tuple[dict[CellKey, CellStats], dict[CellKey, CellStats]] | None = None,
            skipped: MutableMapping[str, int] | None = None) -> list[Finding]:
    """Every cell both sides populate well enough, once per metric in ``schema.METRICS``.

    Reference cells are matched on ``(target, fromSlider, cell)`` at stratum ``"all"``.
    The reference corpus has no eras — it is other people's replays, and the input-regime
    dates are a fact about this player's machine — so splitting it by anything would only
    shrink it against no gain.

    ``control``, if given, is ``(player, reference)`` for this scheme's control scheme
    (see ``schema.CONTROLS``) — the same observations binned with the geometry taken out. Each
    row then also reports what the same metric did in its control cell, and its own effect
    with that divided or subtracted away.

    This is the difference between a table that says something and one that says
    everything. When a player's whole baseline is two and a half times the reference,
    every geometry cell reports two and a half times at overwhelming significance, and
    reading which cells are worse *than that baseline* means dividing one row of this
    table by another. That is classification, and it does not get handed upward.

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

            # The control cell is keyed on the leading axes this scheme shares with its
            # control, which for every scheme that has one means the snap alone.
            control_effect = relative = None
            if control is not None:
                controls = _control_effect(metric, control, target, from_slider,
                                           stratum, cell, scheme)
                if controls is not None:
                    control_effect = controls
                    relative = (effect / control_effect if kind == "ratio"
                                else effect - control_effect)
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
                controlEffect=control_effect,
                relativeEffect=relative,
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


def _control_effect(metric: str,
                    control: tuple[dict[CellKey, CellStats], dict[CellKey, CellStats]],
                    target: str,
                    from_slider: str, stratum: str, cell: tuple[str, ...],
                    scheme: str) -> float | None:
    """This cell's effect as its control scheme measures it, geometry removed.

    The control cell is found by taking the scheme's axes that the control also has. The
    facets and the stratum come along unchanged: a slider-exit turn in the overlapping
    era is controlled against slider-exit turns in the overlapping era, not against the
    corpus at large, or the control would be removing the facet as well as the geometry.
    """
    axes = describe(scheme)
    control_axes = describe(CONTROLS[scheme][0])

    try:
        key = tuple(cell[axes.index(name)] for name in control_axes)
    except ValueError:
        return None

    player_control, reference_control = control
    p = player_control.get((target, from_slider, stratum, key))
    r = reference_control.get((target, from_slider, "all", key))
    if p is None or r is None:
        return None

    outcome = _statistic(metric, p, r)
    return None if isinstance(outcome, str) else outcome[2]


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


def read(path: str) -> Iterator[Finding]:
    """Read a findings CSV back into Findings, so ranking is a pass over the artifact
    rather than a second trip through the corpus."""
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            yield Finding(
                schemaVersion=int(row["schemaVersion"]), scheme=row["scheme"],
                stratum=row["stratum"], cell=row["cell"], target=row["target"],
                fromSlider=row["fromSlider"], metric=row["metric"],
                playerN=int(row["playerN"]), playerValue=float(row["playerValue"]),
                referenceN=int(row["referenceN"]), referenceValue=float(row["referenceValue"]),
                effect=float(row["effect"]), effectKind=row["effectKind"],
                z=float(row["z"]),
                controlEffect=float(row["controlEffect"]) if row["controlEffect"] else None,
                relativeEffect=float(row["relativeEffect"]) if row["relativeEffect"] else None,
                detrended=int(row["detrended"]))


def _format(value: object) -> str:
    if value is None:
        return ""
    return f"{value:.6g}" if isinstance(value, float) else str(value)


# ---------------------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recoverable:
    """How much of a player's total timing error lives in one cell, and is recoverable.

    Effect size is the wrong thing to rank on, and the corpus shows it plainly: sorted by
    effect, the worst cells are seventy-click slider exits worth two thousandths of the
    player's error. What a player wants to know is not where they are worst but where the
    damage is, which is the effect *weighted by how often they meet it*.

    The quantity is mean squared error, so a systematic offset and a spread both count and
    count commensurably — being ten milliseconds early every time is as much error as
    scattering by ten, and only the fix differs. `share` is this cell's excess as a
    fraction of the player's whole mean-square pool, which makes it readable as "hitting
    reference level here removes this much of your total error".
    """

    scheme: str
    stratum: str
    cell: str
    target: str
    from_slider: str
    n: int
    player_sd: float
    player_mean: float
    reference_sd: float
    reference_mean: float
    share: float
    ratio: float      # against the reference
    relative: float   # against the player's own baseline for this rhythm


def rank(findings: Iterable[Finding], *, scheme: str, stratum: str = "all",
         detrended: bool = False) -> list[Recoverable]:
    """Cells worst-damage-first, by recoverable share of the player's error.

    Cells partition the observations within a scheme, so the shares are commensurable and
    a cumulative total means what it looks like. Cells where the player is already at or
    below reference contribute nothing recoverable and are dropped rather than shown with
    a negative share, which would invite reading the list as a balance sheet.
    """
    spread: dict[tuple, Finding] = {}
    location: dict[tuple, Finding] = {}

    for f in findings:
        if f.scheme != scheme or f.stratum != stratum or bool(f.detrended) != detrended:
            continue
        key = (f.cell, f.target, f.fromSlider)
        if f.metric == "hitSd":
            spread[key] = f
        elif f.metric == "hitMean":
            location[key] = f

    pool = sum(f.playerN * (f.playerValue ** 2
                            + (location[k].playerValue ** 2 if k in location else 0.0))
               for k, f in spread.items())
    if pool <= 0:
        return []

    rows = []
    for key, f in spread.items():
        bias = location.get(key)
        player = f.playerValue ** 2 + (bias.playerValue ** 2 if bias else 0.0)
        reference = f.referenceValue ** 2 + (bias.referenceValue ** 2 if bias else 0.0)
        excess = f.playerN * (player - reference)

        if excess <= 0:
            continue

        rows.append(Recoverable(
            scheme=scheme, stratum=stratum, cell=f.cell, target=f.target,
            from_slider=f.fromSlider, n=f.playerN,
            player_sd=f.playerValue, player_mean=bias.playerValue if bias else 0.0,
            reference_sd=f.referenceValue, reference_mean=bias.referenceValue if bias else 0.0,
            share=excess / pool,
            ratio=math.sqrt(player / reference) if reference > 0 else float("inf"),
            relative=f.relativeEffect if f.relativeEffect is not None else 1.0,
        ))

    rows.sort(key=lambda r: -r.share)
    return rows


def verdict(rows: list[Recoverable], *, concentrated_share: float = 0.05,
            concentrated_relative: float = 1.2) -> str:
    """Whether this player has a pattern problem or a baseline problem.

    The system has to be able to say "nothing specific here", or it will always find
    something and always be believed. A finding is worth naming when one cell carries a
    real share of the damage *and* the player is distinctly worse there than their own
    general standard; when the damage is spread evenly across hundreds of cells at a flat
    ratio, the honest answer is that the baseline is the problem and no amount of pattern
    practice addresses it.

    The test is the *control-normalised* ratio, never the raw one against the reference.
    A player whose whole baseline sits two and a half times the reference has every cell
    at two and a half times, and gating on that would report a pattern problem for all of
    them — which is the same mistake the control column was added to stop, made again one
    layer up.
    """
    if not rows:
        return "no cell has recoverable error against the reference."

    top = rows[0]
    named = [r for r in rows
             if r.share >= concentrated_share and r.relative >= concentrated_relative]

    if named:
        return (f"concentrated: {len(named)} cell(s) carry at least {concentrated_share:.0%} "
                f"of the damage each while running {named[0].relative:.2f}x your own "
                f"baseline for that rhythm. Pattern-specific practice applies.")

    worst = max(rows[:20], key=lambda r: r.relative)
    return (f"diffuse: the largest cell is {top.share:.1%} of the damage but only "
            f"{top.relative:.2f}x your own baseline, and across the top twenty the worst "
            f"is {worst.relative:.2f}x. The error is where the clicks are, not where the "
            f"geometry is. This is a baseline, not a pattern, and no cell drill fixes it.")
