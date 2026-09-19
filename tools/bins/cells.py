"""Assigning an observation to a cell.

Deliberately the dullest module in the package. Cell assignment is the one layer that
cannot afford to be fuzzy - mislabelling a 140-degree turn as a wide-angle jump is
cosmetic, but putting it in the wrong cell poisons every number computed from that cell -
so this is pure, total, and has no state, no I/O and no knowledge of where the data came
from. The schema owns the edges; this owns only the lookup from a field name to a field.
"""
from __future__ import annotations

import itertools
from collections.abc import Iterator

from .schema import CONTROLS, FACETS, SCHEMES, Axis, Categorical, Observation

# Axis name -> the Observation attribute it reads. An axis that is added to the schema
# without a line here fails loudly on the first observation rather than binning nothing.
FIELDS: dict[str, str] = {
    "angle": "angle",
    "spacing": "spacing",
    "velocity": "velocity",
    "snap": "snap",
    "runPosition": "run_index",
    "target": "target",
    "fromSlider": "from_slider",
}


def control_key(observation: Observation, scheme: str) -> tuple[str, ...] | None:
    """The cell in this scheme's control that an observation belongs to.

    None when the scheme has no control. Keyed on a subset of the scheme's own axes, so
    an observation always lands in exactly one control cell and the two tables partition
    the same data.
    """
    control = CONTROLS.get(scheme)
    if control is None:
        return None
    return key(observation, control[0])


def key(observation: Observation, scheme: str) -> tuple[str, ...] | None:
    """The cell this observation falls in, or None if any axis rejects it.

    The facets (target, fromSlider) are NOT part of this tuple - they are reported as
    their own columns - but an observation whose facet value is not in the categorical
    axis is still rejected.
    """
    for facet in FACETS:
        if facet.bin(getattr(observation, FIELDS[facet.name])) is None:
            return None

    cell: list[str] = []
    for axis in SCHEMES[scheme]:
        label = axis.bin(getattr(observation, FIELDS[axis.name]))
        if label is None:
            return None
        cell.append(label)
    return tuple(cell)


def describe(scheme: str) -> tuple[str, ...]:
    """The axis names of a scheme, in order."""
    return tuple(axis.name for axis in SCHEMES[scheme])


def label(cell: tuple[str, ...]) -> str:
    """The cell key as it appears in the findings CSV: axis labels joined by '|'."""
    return "|".join(cell)


def enumerate_cells(scheme: str) -> Iterator[tuple[str, ...]]:
    """Every cell a scheme can produce. Used to prove the table is not missing cells
    that simply never got any data."""
    return itertools.product(*(axis.labels for axis in SCHEMES[scheme]))


def bounds(scheme: str, cell: tuple[str, ...]) -> tuple[tuple[float, float] | None, ...]:
    """The numeric edges of each axis of a cell, or None for a categorical axis.

    A drawing of a cell needs the edges as numbers, not as the labels the findings table
    carries. Reading them back by parsing "1.5:2" would put a second edge-parser in the
    codebase and the two would eventually disagree, so this indexes the axis's own labels
    and returns the edges at that index. The schema stays the only place edges are written.
    """
    out: list[tuple[float, float] | None] = []
    for axis, part in zip(SCHEMES[scheme], cell, strict=True):
        if isinstance(axis, Categorical):
            out.append(None)
            continue
        i = axis.labels.index(part)
        out.append((axis.edges[i], axis.edges[i + 1]))
    return tuple(out)
