"""Which presses does a miss get? `build.attribute_misses`, the source of the red miss tick.

The rule: a press that judged nothing goes to the object nearest it in time, and is kept
when that object missed and the press is inside its meh window; first press first. Each
case below is a way that rule can be wrong while still drawing a plausible tick.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build import attribute_misses  # noqa: E402

MEH = 100.0


def objs(*spec):
    """(start, missed) pairs -> the (start, meh, missed) the function takes."""
    return [(t, MEH, m) for t, m in spec]


def test_on_time_press_on_a_miss_is_attributed():
    # A 1/2 jump where the cursor never arrived: the finger was on time and judged nothing.
    out = attribute_misses(objs((1000, False), (1250, True), (1500, False)),
                           [(1000, "hit", 0, 0), (1262, "nothing", 5, 6), (1500, "hit", 0, 0)])
    assert out == {1: [(1262, 5, 6)]}


def test_presses_that_judged_something_are_never_attributed():
    out = attribute_misses(objs((1000, True)), [(1010, "hit", 0, 0)])
    assert out == {}


def test_notelock_and_window_outcomes_count_as_judging_nothing():
    out = attribute_misses(objs((1000, True), (2000, True)),
                           [(990, "notelock", 1, 1), (2030, "window", 2, 2)])
    assert out == {0: [(990, 1, 1)], 1: [(2030, 2, 2)]}


def test_press_outside_the_meh_window_is_not_attributed():
    out = attribute_misses(objs((1000, True)), [(1000 + MEH + 1, "nothing", 0, 0)])
    assert out == {}


def test_press_nearer_a_hit_neighbour_is_not_given_to_the_miss():
    # Inside the miss's window, but nearer the next object, which was hit by its own press:
    # this is an early stray for the next object, not the miss's attempt.
    out = attribute_misses(objs((1000, True), (1150, False)),
                           [(1080, "nothing", 0, 0), (1150, "hit", 0, 0)])
    assert out == {}


def test_two_presses_on_one_miss_first_is_the_attempt():
    # Short then long on one jump: both inside the window, the first is the attempt and the
    # second is kept after it, not dropped.
    out = attribute_misses(objs((1000, True)),
                           [(1040, "nothing", 1, 1), (985, "nothing", 2, 2)])
    assert out == {0: [(985, 2, 2), (1040, 1, 1)]}


def test_first_wins_even_when_a_later_press_is_nearer():
    out = attribute_misses(objs((1000, True)),
                           [(940, "nothing", 1, 1), (1005, "nothing", 2, 2)])
    assert out[0][0] == (940, 1, 1)


def test_equidistant_press_goes_to_the_earlier_object():
    out = attribute_misses(objs((1000, True), (1200, True)), [(1100, "nothing", 0, 0)])
    assert out == {0: [(1100, 0, 0)]}


def test_no_objects():
    assert attribute_misses([], [(1, "nothing", 0, 0)]) == {}
