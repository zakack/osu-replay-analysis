"""Does a planted defect come out on top?

Every other test in this package checks a formula against a closed form, which proves
arithmetic and nothing else. This one asks the only question that matters about the layer
as a whole: given a player who is worse in exactly one cell and ordinary everywhere else,
does the findings table put that cell first?

It runs on written CSVs rather than on constructed Observations, so the rhythm join and
the radians-to-degrees conversion are both inside the test rather than assumed. Both fail
silently when wrong — a mis-joined snap just moves observations to a neighbouring cell,
and an unconverted angle puts every one of them in the middle bin — and a fixture that
skipped the loader would pass happily through either.
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile

from .. import cells, compare, load, maptiming, stats
from ..schema import CONTROLS, SCHEMES
from . import synthetic

SCHEME = "angle-spacing-snap"
DEFECT = "|".join(synthetic.DEFECT)


def _sources(directory: str) -> tuple[load.Source, load.Source]:
    paths = {
        "player_geometry": os.path.join(directory, "geometry.csv"),
        "player_rhythm": os.path.join(directory, "rhythm.csv"),
        "player_corpus": os.path.join(directory, "corpus.json"),
        "reference_geometry": os.path.join(directory, "geometry-reference.csv"),
        "reference_rhythm": os.path.join(directory, "rhythm-reference.csv"),
        "reference_corpus": os.path.join(directory, "corpus-nm.json"),
        "manifest": os.path.join(directory, "manifest.json"),
    }
    return (
        load.Source("player", paths["player_geometry"], paths["player_rhythm"],
                    paths["player_corpus"]),
        load.Source("reference", paths["reference_geometry"], paths["reference_rhythm"],
                    paths["reference_corpus"], paths["manifest"]),
    )


def _findings(directory, *, detrend=False, min_n_player=None, **corpus):
    synthetic.corpus(directory, **corpus)
    player, reference = _sources(directory)
    shared = load.shared_maps(player, reference)
    assert shared, "the fixture's two sides share no beatmaps"

    bias = {}
    if detrend:
        # Filtered the way the command line filters it. A test that detrends against
        # every estimate is not testing the path that ships.
        estimated = maptiming.object_bias(load.observations(reference, maps=shared))
        bias = {k: b for k, b in estimated.items()
                if b.n >= 20 and abs(b.median) >= 5.0}

    def stream(source):
        observations = load.observations(source, maps=shared)
        return maptiming.detrend(observations, bias) if bias else observations

    control_scheme = CONTROLS[SCHEME][0]
    keyers = {name: (lambda o, s=name: cells.key(o, s))
              for name in (SCHEME, control_scheme)}
    reference_tables = stats.aggregate_many(stream(reference), keyers)
    player_tables = stats.aggregate_many(stream(player), keyers)

    skipped: dict[str, int] = {}
    gate = {} if min_n_player is None else {"min_n_player": min_n_player}
    rows = compare.compare(player_tables[SCHEME], reference_tables[SCHEME],
                           scheme=SCHEME, detrended=bool(bias), skipped=skipped,
                           control=(player_tables[control_scheme],
                                    reference_tables[control_scheme]), **gate)
    return rows, skipped, bias


def _top(rows, metric, stratum="all"):
    """The worst cell for a metric, by the significance the table itself reports.

    Ranked on absolute z because a signed mean is worse in both directions: playing a
    section eight milliseconds early is the same defect as playing it eight late.
    """
    candidates = [r for r in rows if r.metric == metric and r.stratum == stratum]
    assert candidates, f"no {metric} rows at stratum {stratum}"
    return max(candidates, key=lambda r: abs(r.z))


def test_shift_surfaces():
    """A cell hit fifteen milliseconds late, and nothing else wrong anywhere."""
    with tempfile.TemporaryDirectory() as directory:
        rows, skipped, _ = _findings(directory, repeats=20, players=40, beatmaps=3,
                                     defect_shift=15.0, defect_widen=1.0)

        top = _top(rows, "hitMean")
        assert top.cell == DEFECT, f"expected {DEFECT} on top, got {top.cell} (z={top.z:.1f})"
        assert 12.0 < top.effect < 18.0, f"recovered shift {top.effect:.2f}ms, expected ~15"

        # The defect is a location, not a scale, so the spread table must stay quiet.
        # If widening shows up here too, something is leaking between the metrics.
        spread = _top(rows, "hitSd")
        assert abs(spread.z) < abs(top.z), "a pure shift moved the spread ranking too"
        return rows, skipped


def test_widening_surfaces():
    """The other defect shape: same timing on average, twice the spread."""
    with tempfile.TemporaryDirectory() as directory:
        rows, _, _ = _findings(directory, repeats=20, players=40, beatmaps=3,
                               defect_shift=0.0, defect_widen=2.0)

        top = _top(rows, "hitSd")
        assert top.cell == DEFECT, f"expected {DEFECT} on top, got {top.cell} (z={top.z:.1f})"
        assert 1.7 < top.effect < 2.3, f"recovered ratio {top.effect:.2f}, expected ~2"

        # The control column is the one that makes the table readable, so it gets its own
        # assertion. The defect cell must stay wide once its own snap's baseline is
        # divided out, and every other cell at that snap must collapse to about one —
        # otherwise the geometry axes are taking credit for a rhythm-wide effect.
        assert top.relativeEffect is not None, "no control effect was computed"
        assert 1.6 < top.relativeEffect < 2.4, (
            f"defect cell relative effect {top.relativeEffect:.2f}, expected ~2")

        siblings = [r.relativeEffect for r in rows
                    if r.metric == "hitSd" and r.stratum == "all"
                    and r.cell.endswith("|" + synthetic.DEFECT[2])
                    and r.cell != DEFECT and r.relativeEffect is not None]
        assert siblings, "no sibling cells at the defect's snap to compare against"

        # On the median sibling, not the extreme one. At sixty observations a cell's
        # standard deviation carries about nine percent of sampling error on its own, so
        # the widest of thirty clean cells is several of those out by construction and
        # asserting on it would only test the seed. The typical clean cell is the claim.
        #
        # It lands just under one rather than at one because the control pools the defect
        # cell in with the rest of its snap: a baseline that excluded the cell being
        # measured would be a different cell for every row, and not a baseline.
        typical = statistics.median(siblings)
        assert 0.85 < typical < 1.15, f"clean cells read {typical:.2f} after control"
        assert top.relativeEffect > max(siblings) * 1.5, (
            f"defect {top.relativeEffect:.2f} does not stand clear of the cleanest "
            f"sibling {max(siblings):.2f}")


def test_clean_player_finds_nothing():
    """The negative control, and the one that catches a leaking cell key.

    With no defect at all, the largest |z| in the table is sampling noise over about
    seven hundred comparisons. A layer that reports a finding here reports findings
    about anybody.
    """
    with tempfile.TemporaryDirectory() as directory:
        rows, _, _ = _findings(directory, repeats=20, players=40, beatmaps=3,
                               defect_shift=0.0, defect_widen=1.0)

        worst = max(rows, key=lambda r: abs(r.z))
        assert abs(worst.z) < 5.0, (
            f"clean player produced {worst.metric} z={worst.z:.1f} on {worst.cell}")


def test_map_bias_is_not_the_player():
    """A mistimed map is the confound this whole layer would otherwise get wrong.

    Everyone, subject and reference alike, is twelve milliseconds late over one stretch
    of one beatmap. Nobody is playing badly: the map is snapped wrong. Undetrended, both
    sides carry it and it cancels — so the test that matters is that detrending does not
    invent a defect where there was none, and does not erase the real one.
    """
    stretch = {(0, i): 12.0 for i in range(200, 400)}

    with tempfile.TemporaryDirectory() as directory:
        rows, _, bias = _findings(directory, detrend=True, repeats=20, players=40,
                                  beatmaps=3, defect_shift=15.0, map_bias=stretch)

        assert bias, "no per-object bias was estimated at all"
        biased = [b for b in bias.values() if abs(b.median) > 6.0]
        assert len(biased) >= 150, f"only {len(biased)} objects carried the planted bias"

        top = _top(rows, "hitMean")
        assert top.cell == DEFECT, (
            f"detrending lost the real defect: {top.cell} on top (z={top.z:.1f})")


def test_eras_are_separable():
    """The subject's three input regimes each get their own stratum, plus a pooled one.

    The min-n gate is lowered here on purpose. The fixture gives each era a single
    beatmap, so a per-era cell holds a fraction of what the pooled one does — which is
    exactly the real trade-off, and the reason both readings are kept rather than one.
    """
    with tempfile.TemporaryDirectory() as directory:
        rows, _, _ = _findings(directory, repeats=20, players=40, beatmaps=3,
                               min_n_player=10)

        strata = {r.stratum for r in rows}
        assert "all" in strata
        assert strata >= {"exclusive-1", "overlapping", "exclusive-2"}, (
            f"eras missing from the table: {strata}")

        # A pooled cell must hold more than any one era's, or the strata are not
        # partitioning the same observations they claim to.
        pooled = _top(rows, "hitMean", stratum="all")
        era_rows = [r for r in rows if r.stratum != "all" and r.cell == pooled.cell
                    and r.metric == "hitMean"]
        assert era_rows, "the pooled worst cell has no per-era counterpart"
        assert pooled.playerN > max(r.playerN for r in era_rows)


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0

    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")
        else:
            print(f"ok    {test.__name__}")

    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
