"""Notes: what an agent writing them relies on. Validation catches every mistake at once and
says what to do instead, and a note stops just after the last thing it cites.

The scene document is built here rather than read from a replay, so each case states the
one fact it depends on.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notes  # noqa: E402

PREEMPT = 1200.0


def circle(i, t, **kw):
    o = {"i": i, "t": t, "type": "circle", "result": "Great", "x": 100.0, "y": 100.0}
    o.update(kw)
    return o


def document(*objects):
    return {"objects": list(objects), "beatmap": {"timePreempt": PREEMPT},
            "hitWindows": {"great": 34.5, "ok": 79.5, "meh": 124.5}}


# Object 1 is the case the viewer exists for: a miss whose stray press came after the cursor
# had already left. Object 2 is an ordinary hit.
SCENE = document(
    circle(0, 1000, error=-5, hitTime=995, cl=-10, cd=.2, en=-60, ex=40, ae=.3, rr=-.2, ri=.1,
           sp=2.0, hd=0.5),
    circle(1, 1300, result="Miss", sy=28, zx=120.0, zy=110.0, cl=-67.5, cd=.42, en=-124.5,
           ex=23.1, sp=.14, hd=-.8, tn=-1.5),
    circle(2, 1600, error=11, hitTime=1611, cl=5, cd=.1, en=-40, ex=60, ae=.2, rr=.1, ri=-.1,
           sp=3.0, hd=1.0),
)


def test_valid_note_stops_after_the_last_thing_it_cites():
    out, errors = notes.resolve([{"objects": 1, "text": "deepest {1.deepest}, left {1.left}, "
                                  "press {1.press}"}], SCENE)
    assert errors == []
    # The stray press at +28 is the last cited event; the stop is just after it.
    assert out[0]["stopAt"] == 1300 + 28 + 1
    assert out[0]["stop"] is True
    assert out[0]["from"] == 1300 - PREEMPT


def test_a_pattern_note_anchored_on_one_object_does_not_wait_for_the_pattern_to_end():
    out, errors = notes.resolve([{"objects": [0, 2], "anchor": 1, "text": "{1.press}"}], SCENE)
    assert errors == []
    assert out[0]["stopAt"] == 1300 + 28 + 1


def test_a_note_citing_the_whole_pattern_stops_at_its_end():
    out, errors = notes.resolve([{"objects": [0, 2], "text": "{0-2.misses}"}], SCENE)
    assert errors == []
    assert out[0]["stopAt"] == 1611 + 1


def test_a_note_citing_nothing_stops_after_its_anchor_press():
    out, errors = notes.resolve([{"objects": 2, "text": "plain"}], SCENE)
    assert errors == []
    assert out[0]["stopAt"] == 1611 + 1


def test_time_notes_are_captions_that_do_not_stop_by_default():
    out, errors = notes.resolve({"notes": [{"time": 500, "text": "intro"}]}, SCENE)
    assert errors == []
    assert out[0]["kind"] == "time" and out[0]["stop"] is False
    assert out[0]["to"] == 500 + notes.CAPTION
    assert "at" not in out[0]


def test_every_problem_is_reported_at_once():
    _, errors = notes.resolve([
        {"objects": 1, "text": "{1.reach}"},             # a miss has no reach
        {"objects": 9, "text": "x"},                     # no such object
        {"obj": 1, "text": "x"},                         # typo'd key, and so no objects
        {"objects": 1, "text": "{1.deepst} {brace}"},    # unknown field, not a placeholder
    ], SCENE)
    text = "\n".join(errors)
    assert "notes[0]" in text and "a miss has no aim error to split" in text
    assert "try {1.depth}" in text
    assert "notes[1]" in text and "0-2" in text
    assert "unknown field obj" in text
    assert "no object field 'deepst'" in text
    assert "{brace} is not a placeholder" in text


def test_rho_is_withheld_below_the_viewers_minimum():
    _, errors = notes.resolve([{"objects": [0, 2], "text": "{0-2.rho}"}], SCENE)
    assert len(errors) == 1 and f"below {notes.RHO_MIN}" in errors[0]


def test_object_fields_and_note_fields_do_not_mix():
    _, errors = notes.resolve([{"objects": 1, "at": [1, 2], "text": "x"},
                               {"time": 10, "anchor": 1, "text": "x"}], SCENE)
    assert any("at and until belong to time notes" in e for e in errors)
    assert any("anchor and point belong to object notes" in e for e in errors)


def test_values_show_the_raw_number_behind_each_placeholder():
    lines = notes.values("{1.deepest} {1.press} {0-2.misses}", SCENE)
    assert lines == ["1.deepest = -67.50 ms", "1.press = 28.00 ms", "0-2.misses = 1 of 3"]


def test_describe_leaves_missing_values_blank():
    table = notes.describe(SCENE, 1, 1).splitlines()
    header = table[-2].split("\t")
    row = dict(zip(header, table[-1].split("\t")))
    assert row["result"] == "miss" and row["press"] == "28.0" and row["reach"] == ""
