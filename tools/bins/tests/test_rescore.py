"""Does the loader apply the re-score table, and refuse to run without one it was told to use?

The table is how a cursor-caused stream shift reaches the bins: one row per object whose hit
and aim error lazer measured at a neighbour's press. Two ways to get it wrong and still
produce a plausible findings table: the override silently not matching its geometry row
(keys are replay plus start time, and a rounding mismatch would drop every one), and a side
loading without its table while the other side has one.
"""
from __future__ import annotations

import csv
import os
import tempfile

import pytest

from .. import load, rescore
from . import synthetic


def _player(directory: str, table: str | None) -> load.Source:
    return load.Source("player", os.path.join(directory, "geometry.csv"),
                       os.path.join(directory, "rhythm.csv"),
                       os.path.join(directory, "corpus.json"), rescore=table)


def test_override_replaces_both_errors_and_marks_the_observation():
    with tempfile.TemporaryDirectory() as directory:
        synthetic.corpus(directory)
        with open(os.path.join(directory, "geometry.csv"), newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r["hitError"] and r["aimErrorRadii"]]
        a, b = rows[0], rows[1]
        table = os.path.join(directory, "rescore.csv")
        with open(table, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(rescore.COLUMNS)
            w.writerow((a["replay"], a["startTime"], "stray-nothing", -12.5, 1.25,
                        a["hitError"], a["aimErrorRadii"]))
            # No press was meant for this one: lazer's values are known wrong and nothing
            # replaces them.
            w.writerow((b["replay"], b["startTime"], "cursor left early", "", "",
                        b["hitError"], b["aimErrorRadii"]))

        seen = {(o.replay, o.start_time): o for o in load.observations(_player(directory, table))}
        oa = seen[(a["replay"], float(a["startTime"]))]
        ob = seen[(b["replay"], float(b["startTime"]))]
        assert (oa.hit_error, oa.aim_error, oa.rescored) == (-12.5, 1.25, True)
        assert (ob.hit_error, ob.aim_error, ob.rescored) == (None, None, True)
        others = [o for k, o in seen.items() if k not in {(oa.replay, oa.start_time), (ob.replay, ob.start_time)}]
        assert others and not any(o.rescored for o in others)


def test_a_named_table_that_is_missing_stops_the_load():
    with tempfile.TemporaryDirectory() as directory:
        synthetic.corpus(directory)
        source = _player(directory, os.path.join(directory, "absent.csv"))
        with pytest.raises(SystemExit):
            next(load.observations(source))


def test_no_table_named_loads_lazers_values():
    with tempfile.TemporaryDirectory() as directory:
        synthetic.corpus(directory)
        assert not any(o.rescored for o in load.observations(_player(directory, None)))


def test_without_rescore_switches_both_sides_together():
    player, reference, collected = load.PLAYER, load.REFERENCE_NM, load.COLLECTED
    try:
        load.without_rescore()
        assert load.PLAYER.rescore is None and load.REFERENCE_NM.rescore is None
        assert load.PLAYER.geometry == player.geometry
        assert load.REFERENCE_NM.geometry == reference.geometry
        assert load.COLLECTED.rescore is None
    finally:
        load.PLAYER, load.REFERENCE_NM, load.COLLECTED = player, reference, collected
