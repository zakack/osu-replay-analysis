"""Constructed stream runs, one per failure, and the name the classifier must give each.

Press fates are written the way lazer would leave them for that failure, so the test pins
the classifier's reading of the alignment, not the simulator. The cascade case is the one
that matters: one dropped tap in an overlapping stream, every later circle taken by the tap
meant for its successor, each judged an interval late.

    python3 tools/analysis/test_streams.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from streams import align, classify  # noqa: E402

BEAT = 400.0          # 150 BPM
SNAP = 0.25           # 1/4: a 100 ms tap interval
STEP = BEAT * SNAP
OBJ = [i * STEP for i in range(10)]


def run(presses, results, errors=None):
    return classify(OBJ, results, errors or [0.0] * len(OBJ), presses, SNAP, BEAT)


def test_clean():
    r = run([(t, "hit", t) for t in OBJ], ["Great"] * 10)
    assert r["verdict"] == "ok"
    assert r["tap"] == ["own"] * 10 and r["judge"] == ["self"] * 10
    assert r["strings"] == []


def test_pushback_after_dropped_tap():
    # Tap for circle 3 never comes. The tap meant for 4 lands on 3 (100 ms late), the one
    # meant for 5 on 4, and so on; the last circle is never reached and misses.
    presses = [(t, "hit", t) for t in OBJ[:3]]
    presses += [(OBJ[k], "hit", OBJ[k - 1]) for k in range(4, 10)]
    results = ["Great"] * 3 + ["Meh"] * 6 + ["Miss"]
    errors = [0.0] * 3 + [STEP] * 6 + [None]
    r = run(presses, results, errors)
    assert r["tap"][3] == "dropped", r["tap"]
    assert r["judge"][3:9] == ["next"] * 6, r["judge"]
    assert len(r["strings"]) == 1
    s = r["strings"][0]
    assert s["class"] == "pushback" and s["origin"] == "dropped", s


def test_ahead_after_extra_tap():
    # An extra tap between circles 2 and 3 lands on circle 3 early; from then on every tap
    # meets the circle after the one it was meant for, a whole interval early.
    presses = [(t, "hit", t) for t in OBJ[:3]] + [(OBJ[2] + 40, "hit", OBJ[3])]
    presses += [(OBJ[k], "hit", OBJ[k + 1]) for k in range(3, 9)]
    results = ["Great"] * 3 + ["Meh"] * 7
    errors = [0.0] * 3 + [-60.0] + [-STEP] * 6
    r = run(presses, results, errors)
    s = r["strings"][0]
    assert s["class"] == "ahead" and s["origin"] == "double", (s, r["judge"], r["double"])


def test_ahead_after_skipped_circle():
    # The cursor leaves circle 3 before its tap: that tap lands on circle 4, circle 3 times
    # out, and every later tap meets the circle after the one it was meant for.
    presses = [(t, "hit", t) for t in OBJ[:3]]
    presses += [(OBJ[k], "hit", OBJ[k + 1]) for k in range(3, 9)]
    results = ["Great"] * 3 + ["Miss"] + ["Meh"] * 6
    errors = [0.0] * 3 + [None] + [-STEP] * 6
    r = run(presses, results, errors)
    s = r["strings"][0]
    assert s["class"] == "ahead" and s["origin"] == "cursor left early", (s, r["judge"])


def test_own_late():
    presses = [(t + 70, "hit", t) for t in OBJ]
    r = run(presses, ["Great"] * 2 + ["Meh"] * 5 + ["Great"] * 3, [70.0] * 10)
    assert r["strings"][0]["class"] == "own-late", r["strings"]


def test_notelock():
    presses = [(t, "hit", t) for t in OBJ]
    presses[3] = (OBJ[3], "notelock", OBJ[3])
    r = run(presses, ["Great"] * 10)
    assert r["tap"][3] == "notelock", r["tap"]


def test_double_tap():
    presses = sorted([(t, "hit", t) for t in OBJ] + [(OBJ[5] + 15, "nothing", None)])
    r = run(presses, ["Great"] * 10)
    assert r["double"][5] and sum(r["double"]) == 1, r["double"]
    assert r["tap"] == ["own"] * 10, r["tap"]


def test_phase_shift_to_triplets():
    # A 1/4 stream tapped at 1/3: the presses run on their own grid, 133 ms apart.
    presses = [(i * BEAT / 3, "hit", None) for i in range(8)]
    r = run(presses, ["Meh"] * 10)
    assert r["verdict"] == "phase-shift" and abs(r["fraction"] - 1 / 3) < 1e-9, r
    assert r["strings"][0]["class"] == "phase-shift"


def test_unalignable():
    presses = [(i * 230.0, "hit", None) for i in range(5)]
    r = run(presses, ["Meh"] * 10)
    assert r["verdict"] == "unalignable", r
    assert r["strings"][0]["class"] == "unalignable"


def test_align_is_deterministic_on_a_tie():
    # A press exactly between two circles matches the earlier one, every time.
    pairs = align([0.0, 100.0], [50.0], 100.0)
    assert pairs == [(0, 0), (1, None)], pairs


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
