"""Annotations for the viewer: validated against the replay, anchored to objects, timed by
what they cite.

Written for agents first. A note is a sentence and the objects it is about; everything else
-- when it appears, when playback stops for it, where its pointer goes, and every number in
it -- is worked out here from the scene document, so a note cannot cite a figure the
inspector does not show or point at an object that is not there. This is the charter's
"constrain the model to speak only from computed features and to cite the number", made a
property of the file format rather than a request.

    {"notes": [
      {"objects": [914, 939], "anchor": 916, "point": "press",
       "text": "Cursor leaves first: deepest {916.deepest}, press {916.press}."},
      {"time": 120000, "until": 128000, "text": "Kiai: the jumps widen here."}
    ]}

A bare list of notes is accepted too. Fields:

    text     optional. Prose, with placeholders {N.field} for object N or {A-B.field} for
             the pattern A..B. The viewer fills them in with the inspector's own formatting.
             Without text, an object note is a mark: its objects are highlighted on the
             field and it does not stop playback -- a bookmark, or a way to show a set of
             objects with no commentary at all.
    label    optional. A short name, up to 48 characters and with no placeholders: a tag on
             the field beside the objects, and the name the notes list groups by. Notes
             sharing a label collapse into one row of the list with a step back and forth.
    objects  an index, or [first, last]: the note is about those objects. Indices are the
             beatmap's object positions, the same numbers the viewer shows and `ora clip`
             takes. Exactly one of objects, time or select.
    select   instead of objects: {"run": {"snap": "1/4", "size": 3}} makes one note per
             constant-snap run matching it, with that run's objects. size counts tapped
             objects -- circles and slider heads, not slider ends -- and is a number or
             [least, most], defaulting to 3 or more; "circles": true keeps only runs whose
             taps are all circles. The objects come from the rhythm pass, never from the
             author. In a select note's text, {run.field} cites the pattern of that note's
             own run.
    anchor   with objects: which object the pointer goes to. Defaults to the last one.
    point    with objects: "object" (its centre, the default), "press" (where the press
             landed) or "deep" (where the cursor was deepest). Before the press or the deepest
             point has happened, the pointer goes to the centre.
    time     map time in ms, for a note about a stretch rather than objects.
    until    with time: when it ends. Defaults to three seconds after.
    at       with time: [x, y] in playfield units to point at. Without it the note is a caption.
    stop     whether playback eases to a halt for it. Defaults to true for an object note
             with text, and false for a mark or a time note.

Stopping happens just after the last event the note mentions. A note citing {916.press}
stops once that press has landed, so the field shows what the sentence says; one citing
nothing stops after its anchor object's press. A note and a miss at the same moment stop
once, with the note shown, because the note is almost always about the miss: the viewer
drops a miss's own stop when a note's stop lands inside that miss's window (its time out to
the meh window plus the 100 ms a stray press can trail by). A miss elsewhere in a long
pattern note keeps its own stop, since the note does not mention it.
"""
from __future__ import annotations

import json
import math
import re

PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
REFERENCE = re.compile(r"^(\d+)(?:-(\d+))?\.([a-z]+)$")

# How long a note stays up after its stop, and how long a time note lasts by default.
HOLD = 1500.0
MARK_TAIL = 240.0
CAPTION = 3000.0
# The viewer withholds the hands' correlation below this many objects; so does a note.
RHO_MIN = 8

KEYS = {"text", "label", "objects", "select", "anchor", "point", "time", "until", "at", "stop"}
# A label is a name for the list and a tag on the field, not a sentence.
LABEL_MAX = 48
POINTS = ("object", "press", "deep")

# Per-object placeholders: what the value needs to exist, and what to suggest when it does not.
OBJECT_FIELDS = {
    "time": "when the object is, as a clock",
    "result": "its judgement: great, ok, meh, miss",
    "press": "button against the beat; a miss's stray press if it had one",
    "deepest": "when the cursor was deepest in the circle, against the beat",
    "depth": "how close to the centre the cursor got, in radii",
    "gap": "press against deepest: how far apart in time the two hands were",
    "entered": "when the cursor entered the circle",
    "left": "when the cursor left the circle",
    "aim": "aim error at the press, in radii",
    "reach": "aim error along the incoming leg; negative fell short",
    "inside": "aim error across the leg; positive toward the inside of the turn",
    "spacing": "the jump into the object, in radii",
    "heading": "the direction of the jump into it",
    "turn": "the turn at the object",
    "meant": "in a shifted stream, the press meant for it against its beat (streams.py)",
    "shift": "in a shifted stream, where the shift began",
}
# Where a stream shift began, in words: the viewer's inspector says the same (SHIFT_WORDS).
SHIFT_WORDS = {
    "stray-nothing": "a tap landed on nothing", "notelock": "a tap was notelocked",
    "cursor left early": "the cursor left a circle before its tap",
    "dropped": "read as a dropped tap", "double": "read as a double tap",
    "stray-window": "a tap landed outside its window", "other": "cause unclear",
}
PATTERN_FIELDS = {
    "press": "button timing, mean and spread",
    "deepest": "cursor's deepest point, mean and spread",
    "reach": "reach, mean and spread",
    "inside": "inside, mean and spread",
    "gap": "press against deepest, mean and spread",
    "rho": f"how strongly the two hands' timing moved together (needs {RHO_MIN} objects)",
    "misses": "misses, as n of m",
    "count": "how many objects",
    "span": "how long the pattern lasts",
    "shifted": "circles judged by a neighbour's tap in a shifted stream, as n of m",
}


def press_value(o: dict):
    if o.get("error") is not None:
        return o["error"]
    return o.get("sy")


def press_time(o: dict):
    if o.get("error") is not None and o.get("hitTime") is not None:
        return o["hitTime"]
    if o.get("sy") is not None:
        return o["t"] + o["sy"]
    return None


def deep_time(o: dict):
    return o["t"] + o["cl"] if o.get("cl") is not None else None


def object_field(o: dict, name: str):
    """(present, events, why-not) for one per-object placeholder."""
    press, deep = press_time(o), deep_time(o)
    when_measured = [press if press is not None else o["t"]]
    if o["type"] == "spinner" and name not in ("time", "result"):
        return False, [], "it is a spinner, and only {N.time} and {N.result} apply to one"
    table = {
        "time": (True, [o["t"]], ""),
        "result": (True, when_measured, ""),
        "press": (press_value(o) is not None, [press],
                  "it was missed with no press attributed to it; try {N.deepest}"),
        "deepest": (o.get("cl") is not None, [deep], "no cursor visit was recorded"),
        "depth": (o.get("cd") is not None, [deep], "no cursor visit was recorded"),
        "gap": (o.get("cl") is not None and press_value(o) is not None, [press, deep],
                "it needs both a press and a cursor visit"),
        "entered": (o.get("en") is not None, [o["t"] + (o.get("en") or 0)],
                    "the cursor never entered the circle; try {N.depth}"),
        "left": (o.get("ex") is not None, [o["t"] + (o.get("ex") or 0)],
                 "the cursor never entered the circle; try {N.depth}"),
        "aim": (o.get("ae") is not None, when_measured,
                "a miss has no aim error in the geometry pass; try {N.depth}"),
        "reach": (o.get("rr") is not None, when_measured,
                  "a miss has no aim error to split, and the first and last objects "
                  "have no corner; try {N.depth}"),
        "inside": (o.get("ri") is not None, when_measured,
                   "a miss has no aim error to split, and the first and last objects "
                   "have no corner; try {N.depth}"),
        "spacing": (o.get("sp") is not None, [o["t"]], "the first object has no jump into it"),
        "heading": (o.get("hd") is not None, [o["t"]], "nothing jumps into it"),
        "turn": (o.get("tn") is not None, [o["t"]], "there is no turn at it"),
        "meant": (o.get("mt") is not None, [o["t"] + (o.get("mt") or 0), press],
                  "it is not in a shifted stream, or no press was meant for it; try {N.press}"),
        "shift": (o.get("og") is not None, [press if press is not None else o["t"]],
                  "it is not in a shifted stream"),
    }
    present, events, why = table[name]
    return present, [e for e in events if e is not None], why


def pattern_field(members: list[dict], name: str):
    """(present, events, why-not) for one pattern placeholder."""
    presses = [press_time(m) for m in members]
    every = [p if p is not None else m["t"] for p, m in zip(presses, members)]
    both = [m for m in members if m.get("error") is not None and m.get("cl") is not None]
    table = {
        "press": (any(m.get("error") is not None for m in members), every,
                  "no object in it has a judged press"),
        "deepest": (any(m.get("cl") is not None for m in members),
                    [d for d in map(deep_time, members) if d is not None],
                    "no object in it has a cursor visit"),
        "reach": (any(m.get("rr") is not None for m in members), every,
                  "no object in it has a reach"),
        "inside": (any(m.get("ri") is not None for m in members), every,
                   "no object in it has an inside"),
        "gap": (bool(both), every, "no object in it has both a press and a cursor visit"),
        "rho": (len(both) >= RHO_MIN, every,
                f"only {len(both)} of its objects have both a press and a cursor visit, and "
                f"the viewer withholds the correlation below {RHO_MIN}"),
        "misses": (True, every, ""),
        "count": (True, [members[0]["t"]], ""),
        "span": (True, [members[0]["t"]], ""),
        "shifted": (True, every, ""),
    }
    return table[name]


def read_runs(path: str) -> tuple[list[dict], int]:
    """Constant-snap runs from `ora rhythm`'s CSV for one replay: (runs, skipped).

    Each run as its snap in beats and the times and kinds of its tapped events. Rebuilt from
    the run's first event and its size rather than by collecting rows with its id, because
    neighbouring runs share their boundary event and the CSV files that event under the
    later run only.

    Then checked, because the CSV only writes events that belong to a group. A run whose
    gaps reach a beat or more can have members outside any group, and rebuilding it from
    the rows that were written makes a run with holes in it. Measured on one replay: nine of
    295 runs, every one at 1 or 2 beats. Those are skipped and counted, never guessed.
    Every sub-beat run -- the bursts and streams a selector is for -- rebuilds cleanly.
    """
    import csv
    with open(path, newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda r: float(r["time"]))
    starts = [(k, int(r["runSize"]), float(r["snap"])) for k, r in enumerate(rows)
              if r["runIndex"] == "0" and r["runId"] != "-1" and r["snap"]]
    runs, skipped = [], 0
    for k, size, snap in starts:
        events = rows[k:k + size]
        gaps = [(float(b["time"]) - float(a["time"])) / float(a["beatLength"])
                for a, b in zip(events, events[1:])]
        if len(events) < size or any(abs(g - snap) > 0.03 for g in gaps):
            skipped += 1
            continue
        tapped = [e for e in events if e["clickIndex"] != "-1"]
        runs.append({"snap": snap, "taps": [float(e["time"]) for e in tapped],
                     "kinds": [e["kind"] for e in tapped]})
    return runs, skipped


def snap_value(spec):
    """A snap as written by an author -- "1/4", 0.25 -- in beats, or None."""
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        return float(spec) if spec > 0 else None
    if isinstance(spec, str):
        found = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", spec)
        if found and int(found.group(2)) > 0 and int(found.group(1)) > 0:
            return int(found.group(1)) / int(found.group(2))
    return None


def expand(index: int, note: dict, objects: list[dict], runs: list[dict] | None):
    """One selector into the marks it matches. Returns (marks, problems, warnings).

    The objects come from the rhythm pass's runs, never from the author: this is where the
    charter's rule -- the model never decides which objects are in a group -- holds even for
    an agent asking to mark two hundred bursts. A run's size counts what is tapped in it,
    circles and slider heads, because a slider end belongs to the rhythm but not to the
    fingers, and "a 3-note burst" means three taps.
    """
    where = f"notes[{index}] (select)"
    problems, warnings = [], []
    for key in ("objects", "time", "anchor", "at", "until"):
        if key in note:
            problems.append(f"{key} does not go with select; the selector chooses the objects")
    select = note["select"]
    if not (isinstance(select, dict) and set(select) == {"run"} and isinstance(select["run"], dict)):
        return [], [f'{where}: select is {{"run": {{"snap": "1/4", "size": 3}}}}'], []
    run = select["run"]
    unknown = sorted(set(run) - {"snap", "size", "circles"})
    if unknown:
        problems.append(f"select.run has no field {', '.join(unknown)}; it takes snap, size "
                        f"and circles")
    snap = snap_value(run.get("snap"))
    if snap is None:
        problems.append('select.run.snap is required: a fraction of a beat like "1/4", or a '
                        'number of beats')
    size = run.get("size")
    if size is None:
        lo_size, hi_size = 3, 10 ** 9
    elif isinstance(size, int) and not isinstance(size, bool) and size >= 2:
        lo_size = hi_size = size
    elif (isinstance(size, list) and len(size) == 2
          and all(isinstance(v, int) and not isinstance(v, bool) for v in size)
          and 2 <= size[0] <= size[1]):
        lo_size, hi_size = size
    else:
        problems.append("select.run.size is a number of tapped objects, 2 or more, or "
                        "[least, most]")
        lo_size, hi_size = 3, 10 ** 9
    circles = run.get("circles", False)
    if not isinstance(circles, bool):
        problems.append("select.run.circles is true or false")
    if runs is None:
        problems.append("select needs the rhythm pass, which did not run for this replay")
    if problems:
        return [], [f"{where}: {p}" for p in problems], []

    by_time = {round(o["t"]): o["i"] for o in objects}
    label = note.get("label")
    shape = (f"{size}" if isinstance(size, int) else
             f"{lo_size}-{hi_size}" if size is not None else "3+")
    group = label if isinstance(label, str) and label else \
        f"{run['snap']} runs of {shape}{' circles' if circles else ''}"
    marks = []
    for r in runs:
        if abs(r["snap"] - snap) > 0.01 or not lo_size <= len(r["taps"]) <= hi_size:
            continue
        if circles and any(kind != "circle" for kind in r["kinds"]):
            continue
        indices = [by_time.get(round(t)) for t in r["taps"]]
        if None in indices or indices != list(range(indices[0], indices[0] + len(indices))):
            continue
        lo, hi = indices[0], indices[-1]
        mark = {k: v for k, v in note.items() if k != "select"}
        if isinstance(mark.get("text"), str):
            mark["text"] = mark["text"].replace("{run.", "{" + f"{lo}-{hi}.")
        mark["objects"] = [lo, hi]
        mark["_group"] = group
        mark["_where"] = f"notes[{index}] (select, objects {lo}-{hi})"
        marks.append(mark)
    if not marks:
        warnings.append(f"{where}: matched no runs on this map ({group})")
    return marks, [], warnings


def resolve(raw, document: dict, runs: list[dict] | None = None):
    """Validate notes against a scene document.

    Returns (notes for the payload, errors, warnings). Every problem is collected rather than
    the first one raised, because an agent fixing a file in a loop pays a full build per
    round trip. A selector's marks share their author's mistakes, so a bad placeholder in a
    selector is reported once with a count, not two hundred times.
    """
    objects = list(document["objects"])
    count = len(objects)
    preempt = document["beatmap"]["timePreempt"]
    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(raw, dict):
        if set(raw) != {"notes"}:
            return [], ['top level: expected {"notes": [...]} or a bare list of notes'], []
        raw = raw["notes"]
    if not isinstance(raw, list):
        return [], ["top level: notes must be a list"], []

    out = []
    for n, note in enumerate(raw):
        if not isinstance(note, dict):
            errors.append(f"notes[{n}]: a note is an object, with objects, time or select")
            continue
        if "select" in note:
            marks, problems, said = expand(n, note, objects, runs)
            errors.extend(problems)
            warnings.extend(said)
            failed, first = 0, None
            for mark in marks:
                entry, trouble = check(mark, mark.pop("_where"), objects, preempt, count)
                if trouble:
                    failed += 1
                    first = first or trouble
                else:
                    entry["group"] = mark["_group"]
                    out.append(entry)
            if first:
                errors.extend(first)
                if failed > 1:
                    errors.append(f"notes[{n}] (select): and the same in {failed - 1} more "
                                  f"of its {len(marks)} marks")
            continue
        label = note.get("text") or note.get("label") or ""
        where = f"notes[{n}]"
        if isinstance(label, str) and label:
            where += f' ("{label[:40]}{"..." if len(label) > 40 else ""}")'
        entry, trouble = check(note, where, objects, preempt, count)
        if trouble:
            errors.extend(trouble)
        else:
            out.append(entry)
    return out, errors, warnings


def check(note: dict, where: str, objects: list[dict], preempt: float, count: int):
    """One note, hand-written or expanded from a selector: (payload entry, problems)."""
    problems: list[str] = []

    unknown = sorted(set(note) - KEYS - {"_group"})
    if unknown:
        problems.append(f"unknown field {', '.join(unknown)}; the fields are "
                        f"{', '.join(sorted(KEYS))}")
    text = note.get("text", "")
    if not isinstance(text, str):
        problems.append("text is a string")
        text = ""
    label = note.get("label")
    if label is not None and (not isinstance(label, str) or not label.strip()
                              or len(label) > LABEL_MAX or "{" in label):
        problems.append(f"label is a short name, 1-{LABEL_MAX} characters, with no "
                        f"placeholders; numbers go in text")
        label = None

    has_objects, has_time = "objects" in note, "time" in note
    if has_objects == has_time:
        problems.append("give exactly one of objects, time or select")

    entry: dict = {"text": text}
    if label:
        entry["label"] = label
    events: list[float] = []

    # Placeholders first, since they decide the stop.
    for match in PLACEHOLDER.finditer(text):
        body = match.group(1)
        ref = REFERENCE.match(body)
        if not ref:
            hint = (" -- {run.field} only works inside a select note"
                    if body.startswith("run.") else "")
            problems.append(f"{{{body}}} is not a placeholder; write {{N.field}} for an "
                            f"object or {{A-B.field}} for a pattern{hint}")
            continue
        a, b, name = int(ref.group(1)), ref.group(2), ref.group(3)
        if b is None:
            if not 0 <= a < count:
                problems.append(f"{{{body}}}: object {a} does not exist (the map has "
                                f"0-{count - 1})")
                continue
            if name not in OBJECT_FIELDS:
                problems.append(f"{{{body}}}: no object field '{name}'; the fields are "
                                f"{', '.join(OBJECT_FIELDS)}")
                continue
            present, evs, why = object_field(objects[a], name)
            if not present:
                problems.append(f"{{{body}}} is empty for object {a}: "
                                + why.replace("{N.", "{" + str(a) + "."))
                continue
        else:
            b = int(b)
            if not (0 <= a <= b < count):
                problems.append(f"{{{body}}}: pattern {a}-{b} is not a range inside "
                                f"0-{count - 1}")
                continue
            if name not in PATTERN_FIELDS:
                problems.append(f"{{{body}}}: no pattern field '{name}'; the fields are "
                                f"{', '.join(PATTERN_FIELDS)}")
                continue
            members = [o for o in objects[a:b + 1] if o["type"] != "spinner"]
            if not members:
                problems.append(f"{{{body}}}: pattern {a}-{b} holds only spinners")
                continue
            present, evs, why = pattern_field(members, name)
            if not present:
                problems.append(f"{{{body}}} is empty: {why}")
                continue
        events.extend(evs)

    if has_objects and not has_time:
        span = note["objects"]
        if isinstance(span, bool) or not (isinstance(span, int) or (
                isinstance(span, list) and len(span) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in span))):
            problems.append("objects is an index or [first, last]")
        else:
            lo, hi = (span, span) if isinstance(span, int) else span
            if not (0 <= lo <= hi < count):
                problems.append(f"objects {lo}-{hi} is not a range inside 0-{count - 1}")
            else:
                anchor = note.get("anchor", hi)
                if not isinstance(anchor, int) or isinstance(anchor, bool) or not lo <= anchor <= hi:
                    problems.append(f"anchor must be one of the note's objects, {lo}-{hi}")
                    anchor = hi
                point = note.get("point", "object")
                if point not in POINTS:
                    problems.append(f"point is one of {', '.join(POINTS)}")
                if "at" in note or "until" in note:
                    problems.append("at and until belong to time notes; an object note "
                                    "is timed and placed by its objects")

                # Stop just after the last event the note cites, and never before the
                # anchor's own press: the field should already show what the text says.
                # Not after the last object's: a pattern note anchored on one miss is
                # about that miss, and stopping four seconds later at the pattern's end
                # shows something else. A note that cites the whole pattern ({A-B.x})
                # carries every member's events and so stops at the end anyway.
                last = objects[hi]
                held = press_time(objects[anchor])
                base = [held if held is not None else objects[anchor]["t"]]
                stop_at = max(base + events) + 1
                end = last.get("endTime", last["t"])
                # A note with words is read, so it stops; a bare mark is a highlight, and
                # stopping on two hundred of them would be the brake at its worst.
                entry.update({
                    "kind": "objects", "lo": lo, "hi": hi, "anchor": anchor,
                    "point": point if point in POINTS else "object",
                    "from": objects[lo]["t"] - preempt,
                    # A note is held up long enough to read; a mark only as long as its
                    # objects are on screen, or neighbouring marks overlap and the list
                    # cannot say which one the playhead is in.
                    "to": max(stop_at, end) + (HOLD if text.strip() else MARK_TAIL),
                    "stop": note.get("stop", bool(text.strip())), "stopAt": stop_at,
                })

    if has_time and not has_objects:
        time = note["time"]
        until = note.get("until")
        if not isinstance(time, (int, float)) or isinstance(time, bool) or not math.isfinite(time):
            problems.append("time is a number of milliseconds of map time")
        else:
            if until is None:
                until = time + CAPTION
            if not isinstance(until, (int, float)) or isinstance(until, bool) or until <= time:
                problems.append("until must be a number after time")
                until = time + CAPTION
            at = note.get("at")
            if at is not None and not (isinstance(at, list) and len(at) == 2 and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) for v in at)):
                problems.append("at is [x, y] in playfield units (512 x 384, y down)")
                at = None
            if "anchor" in note or "point" in note:
                problems.append("anchor and point belong to object notes; a time note "
                                "points with at")
            if not text.strip() and not label:
                problems.append("a time note needs text or a label: with neither there is "
                                "nothing to show and no objects to highlight")
            entry.update({
                "kind": "time", "from": time, "to": until,
                "stop": note.get("stop", False), "stopAt": time + 1,
            })
            if at is not None:
                entry["at"] = at

    if not isinstance(entry.get("stop", True), bool):
        problems.append("stop is true or false")

    return (None if problems else entry), [f"{where}: {p}" for p in problems]


def values(text: str, document: dict) -> list[str]:
    """Each placeholder in a valid note with the plain number behind it, for --check.

    Plain numbers, not the viewer's prose formatting, which lives in one place (the page)
    so the note and the inspector cannot format the same value two ways. This is what lets
    the author confirm the sentence says what they meant before building.
    """
    objects = document["objects"]
    out = []
    for match in PLACEHOLDER.finditer(text):
        a, b, name = REFERENCE.match(match.group(1)).groups()
        a = int(a)
        if b is None:
            o = objects[a]
            press = press_value(o)
            raw = {
                "time": (o["t"], "ms"), "result": (o["result"].lower(), ""),
                "press": (press, "ms"), "deepest": (o.get("cl"), "ms"),
                "depth": (o.get("cd"), "r"),
                "gap": ((press - o["cl"]) if press is not None and o.get("cl") is not None
                        else None, "ms after deepest"),
                "entered": (o.get("en"), "ms"), "left": (o.get("ex"), "ms"),
                "aim": (o.get("ae"), "r"), "reach": (o.get("rr"), "r"),
                "inside": (o.get("ri"), "r"), "spacing": (o.get("sp"), "r"),
                "heading": (math.degrees(o["hd"]) if o.get("hd") is not None else None, "deg"),
                "turn": (math.degrees(o["tn"]) if o.get("tn") is not None else None, "deg"),
                "meant": (o.get("mt"), "ms"),
                "shift": (f"{o.get('og')} ({'cursor' if o.get('ok') == 'c' else 'button'} class)", ""),
            }[name]
            value = raw[0] if isinstance(raw[0], str) else f"{raw[0]:.2f}"
            out.append(f"{match.group(1)} = {value} {raw[1]}".rstrip())
        else:
            members = [o for o in objects[a:int(b) + 1] if o["type"] != "spinner"]
            both = [m for m in members if m.get("error") is not None and m.get("cl") is not None]

            def mean_sd(vs):
                vs = [v for v in vs if v is not None]
                m = sum(vs) / len(vs)
                return f"{m:.2f} +/- {math.sqrt(sum((v - m) ** 2 for v in vs) / len(vs)):.2f}"

            if name == "rho":
                xs = [m["error"] for m in both]
                ys = [m["cl"] for m in both]
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / len(xs))
                sy = math.sqrt(sum((y - my) ** 2 for y in ys) / len(ys))
                cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
                value = f"{cov / (sx * sy):.2f} over {len(both)}" if sx and sy else "0"
            elif name == "misses":
                value = f"{sum(1 for m in members if m['result'] == 'Miss')} of {len(members)}"
            elif name == "shifted":
                value = f"{sum(1 for m in members if m.get('og'))} of {len(members)}"
            elif name == "count":
                value = str(len(members))
            elif name == "span":
                last = objects[int(b)]
                value = f"{last.get('endTime', last['t']) - objects[a]['t']:.0f} ms"
            else:
                column = {"press": "error", "deepest": "cl", "reach": "rr", "inside": "ri"}
                if name == "gap":
                    value = mean_sd([m["error"] - m["cl"] for m in both]) + " ms"
                else:
                    value = mean_sd([m.get(column[name]) for m in members]) + \
                        (" r" if name in ("reach", "inside") else " ms")
            out.append(f"{match.group(1)} = {value}")
    return out


def load(path: str) -> object:
    with open(path) as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}: not JSON: {e.msg} at line {e.lineno}, column {e.colno}")


def describe(document: dict, lo: int | None = None, hi: int | None = None) -> str:
    """The per-object table a note's author reads before writing: every value a placeholder
    can cite, as plain numbers, one object per row."""
    rows = []
    rows.append("# placeholders: {N.field} for object N, {A-B.field} for the pattern A..B")
    rows.append("# object fields:  " + ", ".join(OBJECT_FIELDS))
    rows.append("# pattern fields: " + ", ".join(PATTERN_FIELDS))
    rows.append("# times in ms of map time against the object's beat, negative early; "
                "distances in circle radii; angles in degrees, positive counter-clockwise")
    meh = document["hitWindows"]["meh"]
    rows.append(f"# the cursor is searched for over the meh window, +/-{meh:g} ms: entered "
                f"-{meh:g} means already inside when it opened, left +{meh:g} still inside "
                f"when it closed")
    rows.append("# a blank cell is a value that does not exist, and citing it is an error")
    columns = ["i", "t", "type", "result", "press", "deepest", "depth", "gap", "entered",
               "left", "aim", "reach", "inside", "spacing", "heading", "turn", "meant", "shift"]
    rows.append("\t".join(columns))

    def f(v, places=1):
        return "" if v is None else f"{v:.{places}f}"

    for o in document["objects"][lo or 0: (hi + 1) if hi is not None else None]:
        press = press_value(o)
        gap = (press - o["cl"]) if press is not None and o.get("cl") is not None else None
        deg = (lambda r: None if r is None else math.degrees(r))
        rows.append("\t".join([
            str(o["i"]), f"{o['t']:.0f}", o["type"], o["result"].lower(),
            f(press), f(o.get("cl")), f(o.get("cd"), 3), f(gap), f(o.get("en")), f(o.get("ex")),
            f(o.get("ae"), 3), f(o.get("rr"), 3), f(o.get("ri"), 3), f(o.get("sp"), 3),
            f(deg(o.get("hd")), 0), f(deg(o.get("tn")), 0), f(o.get("mt")), o.get("og") or "",
        ]))
    return "\n".join(rows)
